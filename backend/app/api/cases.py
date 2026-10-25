from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.api.deps import CurrentUser, current_config, get_orchestrator, get_session, get_storage, owned_or_404
from app.api.schemas import (
    CaseDetailOut,
    CaseListOut,
    CaseOut,
    JourneyOut,
    RerunIn,
    ReviewIn,
    SignedUrlOut,
    WorkspaceOut,
)
from app.cases import UploadedFile, create_case
from app.config_loader import ResolvedConfig
from app.db import dumps
from app.documents import png_key
from app.exports.audit_pack import build_audit_pack
from app.exports.service import ExportError, case_file, get_profile
from app.journey import build_journey
from app.models import (
    Case,
    Document,
    Extraction,
    ReviewActionRow,
    RuleResult,
    StageEvent,
    TemplateVersion,
    Vendor,
)
from app.orchestrator import Orchestrator, OrchestratorError, case_summary, event_to_dict
from app.storage import Storage
from app.tenancy import bind_owner, require_owner, scoped
from app.tokens import issue_stream_token
from app.workspace import build_workspace

router = APIRouter(prefix="/api/cases", tags=["cases"])

SessionDep = Annotated[Session, Depends(get_session)]
OrchDep = Annotated[Orchestrator, Depends(get_orchestrator)]


def case_out(s: Session, orch: Orchestrator, case: Case) -> dict[str, Any]:
    out = CaseOut.model_validate(case).model_dump()
    if case.vendor_id is not None:
        vendor = s.scalars(scoped(select(Vendor).where(Vendor.id == case.vendor_id), case.owner_id)).first()
        out["vendor_name"] = vendor.legal_name if vendor else None
    out["latest_template_version"] = s.scalar(
        scoped(select(func.max(TemplateVersion.version)).where(TemplateVersion.case_id == case.id), case.owner_id)
    )
    stage_id = case.current_stage
    if stage_id and not stage_id.startswith("end:") and case.config_snapshot:
        try:
            out["status_label"] = orch.case_config(s, case).workflow.stage(stage_id).status_label
        except KeyError:
            pass
    return out


def _load_case(s: Session, case_id: int) -> Case:
    """The case when it is in the session owner's workspace, else 404."""
    return owned_or_404(s, Case, case_id, require_owner(s), "case")


@router.post("", response_model=CaseOut, status_code=201)
async def create(
    s: SessionDep,
    orch: OrchDep,
    storage: Annotated[Storage, Depends(get_storage)],
    cfg: Annotated[ResolvedConfig, Depends(current_config)],
    files: Annotated[list[UploadFile], File(description="Invoices and supporting documents")],
    submitter_name: Annotated[str | None, Form()] = None,
    submitter_email: Annotated[str | None, Form()] = None,
    note: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    max_bytes = int(cfg.app.limits.max_upload_mb * 1024 * 1024)
    uploads: list[UploadedFile] = []
    for f in files:
        data = await f.read()
        if len(data) > max_bytes:
            raise HTTPException(413, f"{f.filename} is larger than {cfg.app.limits.max_upload_mb:g} MB")
        uploads.append(UploadedFile(f.filename or "upload", f.content_type, data))

    def persist() -> dict[str, Any]:  # DB + storage writes off the event loop
        case = create_case(
            s,
            cfg,
            storage,
            uploads,
            submitter_name=submitter_name,
            submitter_email=submitter_email,
            note=note,
            source="upload",
        )
        orch.enqueue(case.id)
        return case_out(s, orch, case)

    return await run_in_threadpool(persist)


@router.get("", response_model=CaseListOut)
def list_cases(
    s: SessionDep,
    orch: OrchDep,
    status: str | None = None,
    q: str | None = None,
    vendor: int | None = None,
    submitter_email: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    stmt = scoped(select(Case), require_owner(s))
    if status:
        stmt = stmt.where(Case.status.in_(status.split(",")))
    if vendor is not None:
        stmt = stmt.where(Case.vendor_id == vendor)
    if submitter_email:
        stmt = stmt.where(func.lower(Case.submitter_email) == submitter_email.strip().lower())
    if q:
        like = f"%{q}%"
        stmt = stmt.outerjoin(Vendor, Vendor.id == Case.vendor_id).where(
            or_(Case.ref.ilike(like), Case.submitter_name.ilike(like), Vendor.legal_name.ilike(like))
        )
    total = s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page, page_size = max(page, 1), min(max(page_size, 1), 200)
    rows = s.scalars(stmt.order_by(Case.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {
        "items": [case_out(s, orch, c) for c in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{case_id}", response_model=CaseDetailOut)
def get_case(case_id: int, s: SessionDep, orch: OrchDep) -> dict[str, Any]:
    case = _load_case(s, case_id)
    own = case.owner_id
    out = case_out(s, orch, case)
    last_rule_run = s.scalar(scoped(select(func.max(RuleResult.run_no)).where(RuleResult.case_id == case_id), own))
    out.update(
        documents=s.scalars(
            scoped(select(Document).where(Document.case_id == case_id), own).order_by(Document.id)
        ).all(),
        extraction=s.scalars(
            scoped(select(Extraction).where(Extraction.case_id == case_id), own).order_by(Extraction.version.desc())
        ).first(),
        rule_results=s.scalars(
            scoped(select(RuleResult).where(RuleResult.case_id == case_id, RuleResult.run_no == last_rule_run), own)
        ).all()
        if last_rule_run
        else [],
        templates=s.scalars(
            scoped(select(TemplateVersion).where(TemplateVersion.case_id == case_id), own)
            .order_by(TemplateVersion.version)
        ).all(),
        review_actions=s.scalars(
            scoped(select(ReviewActionRow).where(ReviewActionRow.case_id == case_id), own).order_by(ReviewActionRow.id)
        ).all(),
        events=s.scalars(
            scoped(select(StageEvent).where(StageEvent.case_id == case_id), own).order_by(StageEvent.seq)
        ).all(),
    )
    return out


@router.post("/{case_id}/stream-token", response_model=SignedUrlOut)
def stream_token(case_id: int, request: Request, s: SessionDep) -> dict[str, Any]:
    """A single-use, 60-second token for the SSE stream (browser EventSource cannot send the Authorization
    header): open `url` within the minute."""
    case = _load_case(s, case_id)
    ttl = request.app.state.base_config.app.auth.stream_token_ttl_s
    token = issue_stream_token(s, case.owner_id, case.id, ttl)
    s.commit()
    return {"url": f"/api/cases/{case.id}/events/stream?t={token}", "expires_in": ttl}


@router.get("/{case_id}/events/stream")
async def stream_events(
    case_id: int,
    request: Request,
    user: CurrentUser,
    follow: bool = True,
    after: int | None = None,
    last_event_id: Annotated[str | None, Header()] = None,
) -> EventSourceResponse:
    """SSE: replays stored stage_events (after Last-Event-ID, or `after` = the last seq the client has, for a
    manual reconnect), then streams live `stage_event`, `case_updated` and `progress` events.
    `follow=false` closes after the replay. Authenticates with the Authorization header or with `?t=`, a token
    from POST /stream-token (app.auth)."""
    orch: Orchestrator = request.app.state.orchestrator
    sf = bind_owner(request.app.state.session_factory, user)

    def check() -> None:
        with sf() as s:
            _load_case(s, case_id)

    await run_in_threadpool(check)
    if last_event_id and last_event_id.isdigit():
        after = int(last_event_id)
    after = max(after or 0, 0)

    def replay() -> tuple[list[dict[str, Any]], dict[str, Any]]:
        with sf() as s:
            history = s.scalars(
                scoped(select(StageEvent).where(StageEvent.case_id == case_id, StageEvent.seq > after), user)
                .order_by(StageEvent.seq)
            ).all()
            return [event_to_dict(ev) for ev in history], case_summary(_load_case(s, case_id))

    async def gen() -> AsyncIterator[dict[str, str]]:
        last_seq = after
        queue = orch.bus.subscribe(case_id)  # before reading history so nothing is missed
        try:
            history, summary = await run_in_threadpool(replay)  # never a DB call on the SSE loop
            for ev in history:
                last_seq = ev["seq"]
                yield {"event": "stage_event", "id": str(ev["seq"]), "data": dumps(ev)}
            yield {"event": "case_updated", "data": dumps(summary)}
            if not follow:
                return
            while True:
                msg = await queue.get()
                if msg["event"] == "stage_event":
                    seq = msg["data"]["seq"]
                    if seq <= last_seq:
                        continue
                    last_seq = seq
                    yield {"event": "stage_event", "id": str(seq), "data": dumps(msg["data"])}
                else:
                    yield {"event": msg["event"], "data": dumps(msg["data"])}
        finally:
            orch.bus.unsubscribe(case_id, queue)

    return EventSourceResponse(gen(), ping=15)


@router.post("/{case_id}/review", response_model=CaseOut)
async def review(case_id: int, body: ReviewIn, s: SessionDep, orch: OrchDep) -> dict[str, Any]:
    await run_in_threadpool(_load_case, s, case_id)
    if body.ops and body.action != "remark":
        raise HTTPException(422, "`ops` can only be sent with a remark")
    if body.action in ("remark", "reject") and not ((body.text or "").strip() or body.ops):
        raise HTTPException(422, f"`text` is required for {body.action}")
    ops = [op.model_dump(mode="json") for op in body.ops] if body.ops else None
    try:
        await orch.submit_review(case_id, body.action, body.text, body.actor, ops)
    except OrchestratorError as e:
        raise HTTPException(409, str(e)) from e
    return await run_in_threadpool(_reloaded, s, orch, case_id)


def _reloaded(s: Session, orch: Orchestrator, case_id: int) -> dict[str, Any]:
    s.expire_all()
    return case_out(s, orch, _load_case(s, case_id))


@router.post("/{case_id}/rerun", response_model=CaseOut)
async def rerun(case_id: int, body: RerunIn, s: SessionDep, orch: OrchDep) -> dict[str, Any]:
    """Admin: start a new run of a failed or paused case at `from_stage` (the live view's Retry button)."""
    await run_in_threadpool(_load_case, s, case_id)
    try:
        await orch.rerun(case_id, body.from_stage)
    except OrchestratorError as e:
        raise HTTPException(409, str(e)) from e
    return await run_in_threadpool(_reloaded, s, orch, case_id)


@router.get("/{case_id}/journey", response_model=JourneyOut)
def journey(case_id: int, s: SessionDep, orch: OrchDep) -> dict[str, Any]:
    """Courier-style tracker for the submitter (steps from app.yaml `ui.journey`)."""
    case = _load_case(s, case_id)
    events = s.scalars(
        scoped(select(StageEvent).where(StageEvent.case_id == case_id), case.owner_id).order_by(StageEvent.seq)
    ).all()
    return build_journey(orch.case_config(s, case), case, list(events))


@router.get("/{case_id}/workspace", response_model=WorkspaceOut)
def workspace(case_id: int, s: SessionDep, orch: OrchDep) -> dict[str, Any]:
    """Review workspace view: editable template columns, amber cells, PO match and balance, latest draft."""
    case = _load_case(s, case_id)
    return build_workspace(s, orch.case_config(s, case), case)


def _download(data: bytes, mime: str, filename: str, inline: bool = False) -> Response:
    disp = "inline" if inline else "attachment"
    return Response(data, media_type=mime, headers={"Content-Disposition": f'{disp}; filename="{filename}"'})


@router.get("/{case_id}/template/{version}/download")
def download_template(case_id: int, version: int, s: SessionDep, orch: OrchDep) -> Response:
    """One template version as the ERP profile's file (csv | xlsx)."""
    case = _load_case(s, case_id)
    cfg = orch.case_config(s, case)
    tv = s.scalars(scoped(
        select(TemplateVersion).where(TemplateVersion.case_id == case_id, TemplateVersion.version == version),
        case.owner_id,
    )).first()
    if tv is None:
        raise HTTPException(404, f"{case.ref} has no template v{version}")
    try:
        profile = get_profile(cfg, tv.erp_profile)
        return _download(*case_file(s, case, profile, version))
    except ExportError as e:
        raise HTTPException(e.status, str(e)) from e


@router.get("/{case_id}/audit-pack")
def audit_pack(case_id: int, s: SessionDep, orch: OrchDep) -> Response:
    case = _load_case(s, case_id)
    data = build_audit_pack(s, case, orch.case_config(s, case).app.app.name)
    return _download(data, "application/pdf", f"AUDIT_{case.ref}.pdf")


def _document(s: Session, case_id: int, doc_id: int) -> Document:
    doc = owned_or_404(s, Document, doc_id, require_owner(s), "document")
    if doc.case_id != case_id:
        raise HTTPException(404, f"document {doc_id} not found on case {case_id}")
    return doc


@router.get("/{case_id}/documents/{doc_id}/file")
def document_file(
    case_id: int, doc_id: int, s: SessionDep, storage: Annotated[Storage, Depends(get_storage)]
) -> Response:
    doc = _document(s, case_id, doc_id)
    return _download(storage.get(doc.storage_key), doc.mime or "application/pdf", doc.filename, inline=True)


@router.get("/{case_id}/documents/{doc_id}/pages/{page}.png")
def document_page(
    case_id: int, doc_id: int, page: int, s: SessionDep, storage: Annotated[Storage, Depends(get_storage)]
) -> Response:
    doc = _document(s, case_id, doc_id)
    if not 1 <= page <= (doc.pages or 0):
        raise HTTPException(404, f"{doc.filename} has no page {page}")
    try:
        data = storage.get(png_key(doc, page))
    except (FileNotFoundError, OSError) as e:
        raise HTTPException(404, f"page {page} of {doc.filename} is not rendered") from e
    return Response(data, media_type="image/png")
