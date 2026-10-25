"""SQLAlchemy engine + session factory. SQLite in dev, Supabase Postgres (session pooler) in prod."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def json_default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, set):
        return sorted(o)
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def dumps(o: Any) -> str:
    return json.dumps(o, default=json_default)


def normalise_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def make_engine(url: str) -> Engine:
    url = normalise_url(url)
    kwargs: dict[str, Any] = {"json_serializer": dumps, "future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        # Supabase free pooler caps clients (~15) shared by Render and a laptop `make seed`; the orchestrator
        # runs 2 jobs. prepare_threshold=None keeps psycopg working if the transaction pooler URL is pasted.
        kwargs.update(pool_pre_ping=True, pool_size=3, max_overflow=4, pool_recycle=300,
                      connect_args={"prepare_threshold": None})
    return create_engine(url, **kwargs)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


BACKEND_DIR = Path(__file__).resolve().parents[1]


def alembic_config() -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.attributes["configure_logging"] = False  # keep the app's logging setup
    return cfg


def init_db(engine: Engine) -> None:
    """Bring the schema to the latest Alembic revision (a no-op when it is already there). Render also runs
    `alembic upgrade head` before uvicorn starts."""
    cfg = alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
