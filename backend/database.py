"""
KWAC OS — Database connection
Single async engine shared across the entire app.
Import `get_db` in any router to get a session.
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
import config

# Convert postgresql:// to postgresql+asyncpg:// for async driver
_url = config.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    _url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,      # reconnects if DB dropped the connection
    echo=config.IS_DEV,      # logs all SQL in development only
    connect_args={"server_settings": {"search_path": "public"}},
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    """FastAPI dependency — yields a db session, always closes it."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def run_schema():
    """
    Run schema.sql against the database.
    Called on startup if APP_ENV=development, or manually:
        python -c "import asyncio; from database import run_schema; asyncio.run(run_schema())"
    """
    import asyncpg
    from pathlib import Path

    schema_path = Path(__file__).parent / "db" / "schema.sql"
    sql = schema_path.read_text()

    # Use raw asyncpg for DDL (SQLAlchemy async has issues with multi-statement DDL)
    raw_url = config.DATABASE_URL
    conn = await asyncpg.connect(raw_url)
    try:
        await conn.execute(sql)
        print("Schema applied successfully.")
    finally:
        await conn.close()
