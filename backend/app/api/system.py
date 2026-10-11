from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import current_config
from app.api.schemas import HealthOut
from app.config_loader import ResolvedConfig

router = APIRouter(tags=["system"])


@router.get("/healthz", response_model=HealthOut)
def healthz(request: Request) -> HealthOut:
    state = request.app.state
    try:
        with state.session_factory() as s:
            s.execute(text("select 1"))
        db = "ok"
    except SQLAlchemyError as e:  # report, don't crash the health check
        db = f"error: {type(e).__name__}"
    try:
        storage = "ok" if state.storage.health() else "error"
    except (OSError, httpx.HTTPError) as e:
        storage = f"error: {type(e).__name__}"
    return HealthOut(
        ok=db == "ok" and storage == "ok",
        db=db,
        storage=storage,
        llm_path=state.llm.path if state.llm is not None else "unconfigured",
        config_hash=getattr(state, "config_hash", None),
    )


@router.get("/api/config", tags=["config"])
def get_config(cfg: Annotated[ResolvedConfig, Depends(current_config)]) -> dict[str, Any]:
    """Resolved config (files + active Agent Studio overrides) and its hash."""
    return cfg.public_dict()
