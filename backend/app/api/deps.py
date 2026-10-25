"""Request dependencies. Everything a handler touches is the signed-in user's: the session is bound to the
user (app.tenancy), the storage is their prefix, the config is the files plus their Agent Studio edits."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, class_mapper

from app.auth import current_user
from app.baseline import Baseline
from app.config_loader import ResolvedConfig, resolved_config
from app.models import TenantMixin, User
from app.orchestrator import Orchestrator
from app.settings import Settings
from app.storage import OwnerStorage, Storage
from app.tenancy import bind_owner, scoped

CurrentUser = Annotated[User, Depends(current_user)]


def get_session(request: Request, user: CurrentUser) -> Iterator[Session]:
    with bind_owner(request.app.state.session_factory, user)() as s:
        yield s


def get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


def get_storage(request: Request, user: CurrentUser) -> Storage:
    return OwnerStorage(request.app.state.storage, user.id)


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_baseline(request: Request) -> Baseline:
    return request.app.state.baseline


def current_config(request: Request, user: CurrentUser) -> ResolvedConfig:
    """Files overlaid with the user's active DB overrides (Agent Studio)."""
    with bind_owner(request.app.state.session_factory, user)() as s:
        return resolved_config(request.app.state.settings.config_dir, s)


def owned_or_404[M: TenantMixin](s: Session, model: type[M], obj_id: int, user: User | str, what: str) -> M:
    """The row when it exists in the user's workspace; 404 (never 403) otherwise, so ids of other
    workspaces are indistinguishable from ids that do not exist."""
    pk = class_mapper(model).primary_key[0]
    obj = s.scalars(scoped(select(model).where(pk == obj_id), user)).first()
    if obj is None:
        raise HTTPException(404, f"{what} {obj_id} not found")
    return obj
