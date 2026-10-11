from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.cases import case_out
from app.api.deps import current_config, get_orchestrator, get_session
from app.api.schemas import DashboardOut
from app.config_loader import ResolvedConfig
from app.dashboard import EXCLUDED, RangeError, metrics
from app.models import Case
from app.orchestrator import Orchestrator

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/metrics", response_model=DashboardOut)
def dashboard_metrics(
    s: Annotated[Session, Depends(get_session)],
    orch: Annotated[Orchestrator, Depends(get_orchestrator)],
    cfg: Annotated[ResolvedConfig, Depends(current_config)],
    range_: Annotated[str | None, Query(alias="range", description="one of app.yaml ui.dashboard.ranges")] = None,
) -> dict[str, Any]:
    """KPIs, cases over time, exceptions by rule, decision mix and recent cases (ranges from app.yaml `ui`)."""
    try:
        out = metrics(s, cfg, range_)
    except RangeError as e:
        raise HTTPException(422, str(e)) from e
    recent = s.scalars(
        select(Case).where(Case.status.not_in(EXCLUDED)).order_by(Case.id.desc()).limit(cfg.app.ui.dashboard.recent_cases)
    ).all()
    out["recent"] = [case_out(s, orch, c) for c in recent]
    return out
