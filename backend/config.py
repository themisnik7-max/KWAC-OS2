"""
KWAC OS — Central configuration
All environment variables are read here and nowhere else.
If a required variable is missing, the app refuses to start with a clear error.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(
            f"Missing required environment variable: {key}\n"
            f"Check your .env file (copy from .env.example)"
        )
    return val


# Database
DATABASE_URL: str = _require("DATABASE_URL")

# Security
SECRET_KEY: str = _require("SECRET_KEY")
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))

# Anthropic
ANTHROPIC_API_KEY: str = _require("ANTHROPIC_API_KEY")
ANTHROPIC_MAX_TOKENS: int = int(os.getenv("ANTHROPIC_MAX_TOKENS", "1000"))
ANTHROPIC_MONTHLY_BUDGET_USD: float = float(os.getenv("ANTHROPIC_MONTHLY_BUDGET_USD", "20.0"))

# Google
GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")

# Email
SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER: str = os.getenv("SMTP_USER", "")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")

# App
APP_ENV: str = os.getenv("APP_ENV", "development")
APP_URL: str = os.getenv("APP_URL", "http://localhost:8000")
FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3000")

# Admin seed
ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "")
ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "")

IS_DEV = APP_ENV == "development"
