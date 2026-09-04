import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
_env_path = Path(__file__).resolve().parent.parent / ".env"
_loaded = load_dotenv(_env_path, override=True)

DATABASE_URL = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1)
if not _loaded:
    import warnings
    warnings.warn(f".env file not found at {_env_path} — relying on system environment variables")

_raw_url = os.environ.get("DATABASE_URL")
if not _raw_url:
    raise RuntimeError(
        f"DATABASE_URL is not set. Ensure .env exists at {_env_path} "
        "or export DATABASE_URL as a system/PM2 environment variable."
    )

DATABASE_URL = _raw_url.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
