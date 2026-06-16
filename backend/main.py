"""
KWAC OS v2 -- FastAPI Entry Point
"""
import logging
from contextlib import asynccontextmanager
from collections import defaultdict
from time import time

from fastapi import FastAPI, Request, Response, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from pydantic import BaseModel, EmailStr

import config
from database import get_db, AsyncSessionLocal
from auth import authenticate_user, create_access_token, hash_password, require_role
from routers import agents, board, admin, ceo, people
from routers import properties, messages

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("kwac")


# -- DB Migrations ----------------------------------------------------

async def _migrate():
    async with AsyncSessionLocal() as db:
        # GPS Goals
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS gps_goals (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                agent_id UUID UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                year INTEGER DEFAULT EXTRACT(YEAR FROM NOW())::INTEGER,
                annual_gci INTEGER DEFAULT 0,
                units_target INTEGER DEFAULT 0,
                listings_target INTEGER DEFAULT 0,
                buyers_target INTEGER DEFAULT 0,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        # Sprint Sessions
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS sprint_sessions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                agent_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                session_date DATE NOT NULL DEFAULT CURRENT_DATE,
                calls_made INTEGER DEFAULT 0,
                leads INTEGER DEFAULT 0,
                appointments INTEGER DEFAULT 0,
                duration_minutes INTEGER DEFAULT 0,
                notes TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        for col, typ in [("leads", "INTEGER DEFAULT 0"), ("appointments", "INTEGER DEFAULT 0")]:
            try:
                await db.execute(text(f"ALTER TABLE sprint_sessions ADD COLUMN IF NOT EXISTS {col} {typ}"))
            except Exception:
                pass
        # Messages
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS messages (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                from_id UUID NOT NULL REFERENCES users(id),
                to_id UUID NOT NULL REFERENCES users(id),
                property_code TEXT,
                body TEXT NOT NULL,
                is_read BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        # Property valuations (Meeting Akinyton)
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS property_valuations (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
                agent_id UUID NOT NULL REFERENCES users(id),
                estimated_price NUMERIC(12,2),
                comment TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(property_id, agent_id)
            )
        """))
        # Extend properties status check
        try:
            await db.execute(text("ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_status_check"))
            await db.execute(text("""
                ALTER TABLE properties ADD CONSTRAINT properties_status_check
                    CHECK (status IN ('active','sold','rented','withdrawn','meeting'))
            """))
        except Exception:
            pass
        # System settings (kill switch)
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        await db.execute(text("""
            INSERT INTO system_settings (key, value) VALUES ('access_locked', 'false')
            ON CONFLICT (key) DO NOTHING
        """))
        await db.commit()
        logger.info("Migrations applied.")


# -- Admin seed -------------------------------------------------------

async def _seed_admin():
    if not config.ADMIN_EMAIL or not config.ADMIN_PASSWORD:
        return
    async with AsyncSessionLocal() as db:
        hashed = hash_password(config.ADMIN_PASSWORD)
        result = await db.execute(text("""
            INSERT INTO users (email, password_hash, full_name, role)
            VALUES (:e, :h, 'Administrator', 'admin')
            ON CONFLICT (email) DO UPDATE SET password_hash = :h
            RETURNING id
        """), {"e": config.ADMIN_EMAIL, "h": hashed})
        uid = result.scalar()
        await db.execute(text("INSERT INTO agents (id) VALUES (:id) ON CONFLICT DO NOTHING"), {"id": uid})
        await db.commit()
        logger.info(f"Admin seeded/updated: {config.ADMIN_EMAIL}")


# -- Lifespan ---------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await _migrate()
    await _seed_admin()
    logger.info("KWAC OS ready.")
    yield
    logger.info("KWAC OS shutdown.")


# -- App --------------------------------------------------------------

app = FastAPI(title="KWAC OS API", lifespan=lifespan, docs_url=None, redoc_url=None)

app.add_middleware(CORSMiddleware,
    allow_origins=[config.FRONTEND_URL, "http://localhost:3000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response


_rs: dict = defaultdict(list)

@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.url.path.endswith((".js", ".css", ".ico", ".png", ".html")):
        return await call_next(request)
    ip = request.client.host
    now = time()
    _rs[ip] = [t for t in _rs[ip] if t > now - 60]
    if len(_rs[ip]) >= 120:
        return JSONResponse(status_code=429, content={"detail": "Too many requests."})
    _rs[ip].append(now)
    return await call_next(request)


@app.middleware("http")
async def access_lock_check(request: Request, call_next):
    path = request.url.path
    if path.startswith("/auth") or path in ("/health",) or "." in path.split("/")[-1]:
        return await call_next(request)
    try:
        async with AsyncSessionLocal() as db:
            r = await db.execute(text("SELECT value FROM system_settings WHERE key='access_locked'"))
            row = r.first()
            if row and row[0] == "true":
                token = request.cookies.get("kwac_token")
                if token:
                    from auth import decode_token
                    try:
                        payload = decode_token(token)
                        if payload.get("role") in ("admin", "ceo"):
                            return await call_next(request)
                    except Exception:
                        pass
                api_prefixes = ("/agents", "/properties", "/messages", "/board", "/admin", "/people", "/ceo")
                if any(path.startswith(p) for p in api_prefixes):
                    return JSONResponse(status_code=503, content={"detail": "Το σύστημα είναι προσωρινά κλειστό."})
    except Exception:
        pass
    return await call_next(request)


# -- Auth routes ------------------------------------------------------

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


@app.post("/auth/login")
async def login(body: LoginRequest, response: Response, db=Depends(get_db)):
    user = await authenticate_user(body.email, body.password, db)
    if not user:
        raise HTTPException(status_code=401, detail="Λάθος email ή κωδικός.")
    token = create_access_token(user_id=str(user["id"]), role=user["role"], email=user["email"])
    response.set_cookie("kwac_token", token, httponly=True, samesite="lax",
                        max_age=config.ACCESS_TOKEN_EXPIRE_MINUTES * 60, secure=not config.IS_DEV)
    return {"user": {"id": str(user["id"]), "email": user["email"],
                     "full_name": user["full_name"], "role": user["role"]}}


@app.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("kwac_token")
    return {"ok": True}


@app.get("/auth/me")
async def me(user=Depends(require_role("agent", "ceo", "admin"))):
    return user


@app.get("/health")
async def health():
    return {"status": "ok"}


# -- System settings (kill switch) ------------------------------------

@app.get("/system/settings")
async def get_settings(user=Depends(require_role("admin"))):
    async with AsyncSessionLocal() as db:
        r = await db.execute(text("SELECT key, value FROM system_settings"))
        return {row[0]: row[1] for row in r.fetchall()}


@app.post("/system/lock")
async def lock_system(user=Depends(require_role("admin"))):
    async with AsyncSessionLocal() as db:
        await db.execute(text("UPDATE system_settings SET value='true', updated_at=NOW() WHERE key='access_locked'"))
        await db.commit()
    return {"ok": True, "locked": True}


@app.post("/system/unlock")
async def unlock_system(user=Depends(require_role("admin"))):
    async with AsyncSessionLocal() as db:
        await db.execute(text("UPDATE system_settings SET value='false', updated_at=NOW() WHERE key='access_locked'"))
        await db.commit()
    return {"ok": True, "locked": False}


# -- Routers ----------------------------------------------------------

app.include_router(agents.router,     prefix="/agents",     tags=["agents"])
app.include_router(board.router,      prefix="/board",      tags=["board"])
app.include_router(admin.router,      prefix="/admin",      tags=["admin"])
app.include_router(messages.router,   prefix="/messages",   tags=["messages"])

# ── SPA static file serving ───────────────────────────────────────

@app.get("/")
async def serve_index():
    return FileResponse("static/index.html")

app.mount("/static", StaticFiles(directory="static"), name="static_assets")
app.include_router(ceo.router,        prefix="/ceo",        tags=["ceo"])
app.include_router(people.router,     prefix="/people",     tags=["people"])
app.include_router(properties.router, prefix="/properties", tags=["properties"])
app.include_router(messages.router,   prefix="/messages",   tags=["messages"])


# -- Static SPA -------------------------------------------------------

import os as _os
_static = _os.path.join(_os.path.dirname(__file__), "static")
if _os.path.isdir(_static):
    app.mount("/", StaticFiles(directory=_static, html=True), name="static")


# -- Global error handler ---------------------------------------------

@app.exception_handler(Exception)
async def global_error(request: Request, exc: Exception):
    logger.error(f"Unhandled error on {request.url}: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})
