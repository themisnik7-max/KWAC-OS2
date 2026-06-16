"""
KWAC OS v2 — FastAPI Entry Point
Run locally:  uvicorn main:app --reload --port 8000
Production:   uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
import config
from database import get_db, run_schema
from auth import authenticate_user, create_access_token, hash_password, require_role

# Routers (we build these next)
from routers import agents, properties, ceo, board, people, admin

# ── Logging ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO if not config.IS_DEV else logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("kwac.log"),
    ]
)
logger = logging.getLogger("kwac.main")


# ── Startup / Shutdown ───────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("KWAC OS starting up...")

    # Apply schema in dev automatically
    if config.IS_DEV:
        logger.info("Development mode: applying schema...")
        await run_schema()

    # Start scheduler
    from services.scheduler import start_scheduler
    start_scheduler()
    logger.info("Scheduler started.")

    # Seed admin user if not exists
    if config.ADMIN_EMAIL and config.ADMIN_PASSWORD:
        try:
            await _seed_admin()
        except Exception as e:
            logger.warning(f"Could not seed admin (schema may not be applied yet): {e}")

    logger.info("KWAC OS ready.")
    yield

    logger.info("KWAC OS shutting down.")


async def _seed_admin():
    """Create the admin user on first run if it doesn't exist."""
    from database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": config.ADMIN_EMAIL},
        )
        if result.first():
            return
        user_id_result = await db.execute(
            text("""
                INSERT INTO users (email, password_hash, full_name, role)
                VALUES (:email, :hash, 'Administrator', 'admin')
                RETURNING id
            """),
            {"email": config.ADMIN_EMAIL, "hash": hash_password(config.ADMIN_PASSWORD)},
        )
        await db.commit()
        logger.info(f"Admin user created: {config.ADMIN_EMAIL}")


# ── App ──────────────────────────────────────────────────────

app = FastAPI(
    title="KWAC OS API",
    version="2.0.0",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)

# CORS — only allow our frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.FRONTEND_URL, "http://localhost:3000"],
    allow_credentials=True,                         # required for cookies
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Content-Type", "Authorization"],
)


# ── Security headers middleware ──────────────────────────────

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if not config.IS_DEV:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# ── Rate limiting middleware ─────────────────────────────────

from collections import defaultdict
from time import time

_rate_store: dict = defaultdict(list)
RATE_LIMIT = 60       # requests
RATE_WINDOW = 60      # seconds

@app.middleware("http")
async def rate_limit(request: Request, call_next):
    ip = request.client.host
    now = time()
    window_start = now - RATE_WINDOW
    _rate_store[ip] = [t for t in _rate_store[ip] if t > window_start]
    if len(_rate_store[ip]) >= RATE_LIMIT:
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests. Please slow down."},
        )
    _rate_store[ip].append(now)
    return await call_next(request)


# ── Auth routes ──────────────────────────────────────────────

from pydantic import BaseModel, EmailStr

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


@app.post("/auth/login")
async def login(body: LoginRequest, response: Response, db=Depends(get_db)):
    user = await authenticate_user(body.email, body.password, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    token = create_access_token(
        user_id=str(user["id"]),
        role=user["role"],
        email=user["email"],
    )

    # Set httponly cookie — JS cannot read this, prevents XSS token theft
    response.set_cookie(
        key="kwac_token",
        value=token,
        httponly=True,
        secure=not config.IS_DEV,   # HTTPS only in production
        samesite="lax",
        max_age=config.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )

    return {
        "user": {
            "id": str(user["id"]),
            "email": user["email"],
            "full_name": user["full_name"],
            "role": user["role"],
        }
    }


@app.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("kwac_token")
    return {"ok": True}


@app.get("/auth/me")
async def me(user=Depends(require_role("agent", "ceo", "admin"))):
    return user


# ── Root ────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"service": "KWAC OS API", "version": "2.0.0", "status": "running", "docs": "/docs"}


# ── Health check ─────────────────────────────────────────────

@app.get("/health")
async def health(db=Depends(get_db)):
    """Used by Render to confirm the service is alive."""
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "env": config.APP_ENV}


# ── Register routers ─────────────────────────────────────────

app.include_router(agents.router,     prefix="/agents",     tags=["agents"])
app.include_router(properties.router, prefix="/properties", tags=["properties"])
app.include_router(people.router,     prefix="/people",     tags=["people"])
app.include_router(ceo.router,        prefix="/ceo",        tags=["ceo"])
app.include_router(board.router,      prefix="/board",      tags=["board"])
app.include_router(admin.router,      prefix="/admin",      tags=["admin"])


# ── Global error handler ─────────────────────────────────────

@app.exception_handler(Exception)
async def global_error_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error on {request.url}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. Check kwac.log for details."},
    )
