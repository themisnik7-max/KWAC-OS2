"""
KWAC OS — Authentication & Authorization
- Passwords hashed with bcrypt
- JWT stored in httponly cookie (not localStorage — XSS-safe)
- Every protected route uses Depends(require_role(...))
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import config
from database import get_db

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)

ALGORITHM = "HS256"


# ── Password utilities ──────────────────────────────────────

def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ── Token utilities ─────────────────────────────────────────

def create_access_token(user_id: str, role: str, email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": user_id,
        "role": role,
        "email": email,
        "exp": expire,
    }
    return jwt.encode(payload, config.SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, config.SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalid or expired. Please log in again.",
        )


# ── Request extraction ──────────────────────────────────────

def _get_token_from_request(request: Request) -> Optional[str]:
    """Try cookie first, then Authorization header."""
    token = request.cookies.get("kwac_token")
    if token:
        return token
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        return auth[7:]
    return None


# ── Dependencies ────────────────────────────────────────────

async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Returns the current user dict. Raises 401 if not authenticated."""
    token = _get_token_from_request(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Please log in.",
        )
    payload = decode_token(token)
    user_id = payload.get("sub")

    result = await db.execute(
        text("SELECT id, email, full_name, role, is_active FROM users WHERE id = :id"),
        {"id": user_id},
    )
    user = result.mappings().first()
    if not user or not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or deactivated.",
        )
    return dict(user)


def require_role(*roles: str):
    """
    Usage:
        @router.get("/ceo-only")
        async def ceo_view(user = Depends(require_role("ceo", "admin"))):
            ...
    """
    async def dependency(
        request: Request,
        db: AsyncSession = Depends(get_db),
    ) -> dict:
        user = await get_current_user(request, db)
        if user["role"] not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required role: {' or '.join(roles)}.",
            )
        return user
    return dependency


# ── Login helper ────────────────────────────────────────────

async def authenticate_user(email: str, password: str, db: AsyncSession) -> Optional[dict]:
    result = await db.execute(
        text("SELECT id, email, full_name, role, password_hash, is_active FROM users WHERE email = :email"),
        {"email": email.lower().strip()},
    )
    user = result.mappings().first()
    if not user:
        return None
    if not user["is_active"]:
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    return dict(user)
