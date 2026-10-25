"""Vendor portal: the token in the upload link identifies the request. In this public demo the portal
also needs sign-in and ownership: a user only reaches the requests of their own workspace (they play the
vendor). A production deployment would make it public, token-only (docs/DECISIONS.md)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_orchestrator, get_session, get_storage
from app.api.schemas import PortalOut
from app.cases import UploadedFile
from app.models import Case
from app.orchestrator import Orchestrator
from app.portal import PortalError, get_request, request_view, vendor_docs_received
from app.storage import Storage
from app.tenancy import scoped

router = APIRouter(prefix="/portal", tags=["portal"])

SessionDep = Annotated[Session, Depends(get_session)]
OrchDep = Annotated[Orchestrator, Depends(get_orchestrator)]


@router.get("/{token}", response_model=PortalOut)
def portal(token: str, s: SessionDep, orch: OrchDep) -> dict[str, Any]:
    try:
        req = get_request(s, token)
    except PortalError as e:
        raise HTTPException(e.status, str(e)) from e
    case = s.scalars(scoped(select(Case).where(Case.id == req.case_id), req.owner_id)).first() if req.case_id else None
    return request_view(s, req, orch.case_config(s, case) if case else None)


@router.post("/{token}/upload", response_model=PortalOut)
async def upload(
    token: str,
    s: SessionDep,
    orch: OrchDep,
    storage: Annotated[Storage, Depends(get_storage)],
    files: Annotated[list[UploadFile], File(description="Requested documents (W-9, bank letter, corrected invoice)")],
) -> dict[str, Any]:
    uploads = [UploadedFile(f.filename or "upload", f.content_type, await f.read()) for f in files]
    try:
        # classification (DB + LLM) runs on the orchestrator loop, not the API loop
        return await orch.call(vendor_docs_received(s, orch, storage, token, uploads))
    except PortalError as e:
        raise HTTPException(e.status, str(e)) from e
