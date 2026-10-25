"""Approved library and exports: list approved invoices, export them (ZIP or batch import file), download."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.cases import case_out
from app.api.deps import current_config, get_orchestrator, get_session, get_storage, owned_or_404
from app.api.schemas import CaseListOut, ExportBatchOut, ExportIn
from app.config_loader import ResolvedConfig
from app.exports.service import EXPORTABLE, ExportError, batch_file, create_export
from app.models import Case, CaseStatus, ExportBatch
from app.orchestrator import Orchestrator
from app.storage import Storage
from app.tenancy import require_owner, scoped

router = APIRouter(tags=["exports"])

SessionDep = Annotated[Session, Depends(get_session)]
StorageDep = Annotated[Storage, Depends(get_storage)]


def download(data: bytes, mime: str, filename: str) -> Response:
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def batch_out(b: ExportBatch) -> dict[str, Any]:
    out = ExportBatchOut.model_validate(b).model_dump()
    out["filename"] = b.file_key.rsplit("/", 1)[-1] if b.file_key else None
    out["download_url"] = f"/api/exports/{b.id}/download"
    return out


@router.get("/api/approved", response_model=CaseListOut)
def approved(
    s: SessionDep,
    orch: Annotated[Orchestrator, Depends(get_orchestrator)],
    exported: bool | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """Approved library. `exported=false` = not exported yet; `true` = already exported; omit for both."""
    statuses = {None: list(EXPORTABLE), False: [CaseStatus.approved], True: [CaseStatus.exported]}[exported]
    stmt = scoped(select(Case).where(Case.status.in_(statuses), Case.is_sample.is_(False)), require_owner(s))
    total = s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page, page_size = max(page, 1), min(max(page_size, 1), 200)
    rows = s.scalars(stmt.order_by(Case.approved_at.desc(), Case.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [case_out(s, orch, c) for c in rows], "total": total, "page": page, "page_size": page_size}


@router.post("/api/exports", response_model=ExportBatchOut, status_code=201)
def export(
    body: ExportIn, s: SessionDep, storage: StorageDep, cfg: Annotated[ResolvedConfig, Depends(current_config)]
) -> dict[str, Any]:
    try:
        b = create_export(s, storage, cfg, body.case_ids, body.erp_profile, body.mode, body.actor, body.force)
    except ExportError as e:
        raise HTTPException(e.status, str(e)) from e
    return batch_out(b)


@router.get("/api/exports", response_model=list[ExportBatchOut])
def list_exports(s: SessionDep) -> list[dict[str, Any]]:
    rows = s.scalars(scoped(select(ExportBatch), require_owner(s)).order_by(ExportBatch.id.desc())).all()
    return [batch_out(b) for b in rows]


@router.get("/api/exports/{batch_id}/download")
def download_export(batch_id: int, s: SessionDep, storage: StorageDep) -> Response:
    b = owned_or_404(s, ExportBatch, batch_id, require_owner(s), "export")
    try:
        return download(*batch_file(storage, b))
    except ExportError as e:
        raise HTTPException(e.status, str(e)) from e
