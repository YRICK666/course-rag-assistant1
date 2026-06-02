from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "app.db"


class Base(DeclarativeBase):
    pass


def get_db_url() -> str:
    return os.environ.get("DATABASE_URL") or f"sqlite:///{DEFAULT_DB_PATH}"


_engine = None


def get_engine():
    global _engine
    if _engine is None:
        url = get_db_url()
        if url.startswith("sqlite"):
            _engine = create_engine(url, connect_args={"check_same_thread": False})
        else:
            _engine = create_engine(url)
    return _engine


_Session = None


def get_session():
    global _Session
    if _Session is None:
        _Session = sessionmaker(bind=get_engine())
    return _Session()


def init_db():
    from .models import QAHistory  # noqa: F401

    Base.metadata.create_all(bind=get_engine())
