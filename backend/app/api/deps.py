from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from app.config_loader import ResolvedConfig, resolved_config
from app.orchestrator import Orchestrator
from app.settings import Settings
from app.storage import Storage


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as s:
        yield s


def get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def current_config(request: Request) -> ResolvedConfig:
    """Files overlaid with active DB overrides (Agent Studio)."""
    with request.app.state.session_factory() as s:
        return resolved_config(request.app.state.settings.config_dir, s)
