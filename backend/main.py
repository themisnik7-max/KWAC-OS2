"""
KWAC OS v2 -- FastAPI Entry Point
"""
import logging
import os
from collections import defaultdict
from contextlib import asynccontextmanager
from time import time

from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

import config
from database import get_db
from auth import authenticate_user, create_access_token, hash_password, require_role
from routers import agents, board, admin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("kwac.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("KWAC OS starting up...")
    await _migrate()
    if config.ADMIN_EMAIL and config.ADMIN_PASSWORD:
        try:
            await _seed_admin()
        except Exception as e:
            logger.warning(f"Seed admin skipped: {e}")
    logger.info("KWAC OS ready.")
    yield
    logger.info("KWAC OS shutting down.")


async def _migrate():
    from database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        try:
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS gps_goals (
                    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                    agent_id        UUID UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    year            INTEGER DEFAULT EXTRACT(YEAR FROM NOW())::INTEGER,
                    annual_gci      INTEGER DEFAULT 0,
                    units_target    INTEGER DEFAULT 0,
                    listings_target INTEGER DEFAULT 0,
                    buyers_target   INTEGER DEFAULT 0,
                    updated_at      TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await db.commit()
            logger.info("Migrations OK.")
        except Exception as e:
            logger.warning(f"Migration warning (non-fatal): {e}")
            await db.rollback()


async def _seed_admin():
    from database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        r = await db.execute(text("SELECT id FROM users WHERE email = :email"), {"email": config.ADMIN_EMAIL})
        if r.first():
            return
        uid = await db.execute(text("""
            INSERT INTO users (email, password_hash, full_name, role)
            VALUES (:email, :hash, 'Administrator', 'admin')
            RETURNING id
        """), {"email": config.ADMIN_EMAIL, "hash": hash_password(config.ADMIN_PASSWORD)})
        await db.execute(text("INSERT INTO agents (id) VALUES (:id)"), {"id": uid.scalar()})
        await db.commit()
        logger.info(f"Admin seeded: {config.ADMIN_EMAIL}")


app = FastAPI(
    title="KWAC OS API",
    version="2.0.0",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.FRONTEND_URL, "http://localhost:3000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


_rate_store: dict = defaultdict(list)
RATE_LIMIT = 120
RATE_WINDOW = 60


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    ip = request.client.host
    now = time()
    _rate_store[ip] = [t for t in _rate_store[ip] if t > now - RATE_WINDOW]
    if len(_rate_store[ip]) >= RATE_LIMIT:
        return JSONResponse(status_code=429, content={"detail": "Too many requests."})
    _rate_store[ip].append(now)
    return await call_next(request)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


@app.post("/auth/login")
async def login(body: LoginRequest, response: Response, db=Depends(get_db)):
    user = await authenticate_user(body.email, body.password, db)
    if not user:
        raise HTTPException(status_code=401, detail="Λαθος email η κωδικος.")
    token = create_access_token(str(user["id"]), user["role"], user["email"])
    response.set_cookie(
        key="kwac_token", value=token, httponly=True,
        secure=not config.IS_DEV, samesite="lax",
        max_age=config.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    return {"user": {"id": str(user["id"]), "email": user["email"], "full_name": user["full_name"], "role": user["role"]}}


@app.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("kwac_token")
    return {"ok": True}


@app.get("/auth/me")
async def me(user=Depends(require_role("agent", "ceo", "admin"))):
    return {"id": str(user["id"]), "email": user["email"], "full_name": user["full_name"], "role": user["role"]}


@app.get("/health")
async def health(db=Depends(get_db)):
    await db.execute(text("SELECT 1"))
    return {"status": "ok"}


app.include_router(agents.router, prefix="/agents", tags=["agents"])
app.include_router(board.router,  prefix="/board",  tags=["board"])
app.include_router(admin.router,  prefix="/admin",  tags=["admin"])


_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/", include_in_schema=False)
async def serve_root():
    idx = os.path.join(_static_dir, "index.html")
    if os.path.exists(idx):
        return FileResponse(idx)
    return JSONResponse({"detail": "Frontend not found"}, status_code=404)


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    api_prefixes = ("auth/", "agents/", "board/", "admin/", "properties/", "people/", "ceo/", "health", "docs", "static/", "openapi")
    if any(full_path.startswith(p) for p in api_prefixes):
        raise HTTPException(status_code=404)
    idx = os.path.join(_static_dir, "index.html")
    if os.path.exists(idx):
        return FileResponse(idx)
    raise HTTPException(status_code=404)


@app.exception_handler(Exception)
async def global_error_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error on {request.url}: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})
