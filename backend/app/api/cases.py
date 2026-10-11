from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.api.deps import current_config, get_orchestrator, get_session, get_storage
from app.api.schemas import CaseDetailOut, CaseListOut, CaseOut, ReviewIn
from app.cases import UploadedFile, create_case
from app.config_loader import ResolvedConfig
from app.db import dumps
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

router = APIRouter(prefix="/api/cases", tags=["cases"])

SessionDep = Annotated[Session, Depends(get_session)]
OrchDep = Annotated[Orchestrator, Depends(get_orchestrator)]


def _case_out(s: Session, orch: Orchestrator, case: Case) -> dict[str, Any]:
    out = CaseOut.model_validate(case).model_dump()
    stage_id = case.current_stage
    if stage_id and not stage_id.startswith("end:") and case.config_snapshot:
        try:
            out["status_label"] = orch.case_config(s, case).workflow.stage(stage_id).status_label
        except KeyError:
            pass
    return out


def _load_case(s: Session, case_id: int) -> Case:
    case = s.get(Case, case_id)
    if case is None:
        raise HTTPException(404, f"case {case_id} not found")
    return case


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
    return _case_out(s, orch, case)


@router.get("", response_model=CaseListOut)
async def list_cases(
    s: SessionDep,
    orch: OrchDep,
    status: str | None = None,
    q: str | None = None,
    vendor: int | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    stmt = select(Case)
    if status:
        stmt = stmt.where(Case.status.in_(status.split(",")))
    if vendor is not None:
        stmt = stmt.where(Case.vendor_id == vendor)
    if q:
        like = f"%{q}%"
        stmt = stmt.outerjoin(Vendor, Vendor.id == Case.vendor_id).where(
            or_(Case.ref.ilike(like), Case.submitter_name.ilike(like), Vendor.legal_name.ilike(like))
        )
    total = s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page, page_size = max(page, 1), min(max(page_size, 1), 200)
    rows = s.scalars(stmt.order_by(Case.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {
        "items": [_case_out(s, orch, c) for c in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{case_id}", response_model=CaseDetailOut)
async def get_case(case_id: int, s: SessionDep, orch: OrchDep) -> dict[str, Any]:
    case = _load_case(s, case_id)
    out = _case_out(s, orch, case)
    last_rule_run = s.scalar(select(func.max(RuleResult.run_no)).where(RuleResult.case_id == case_id))
    out.update(
        documents=s.scalars(select(Document).where(Document.case_id == case_id).order_by(Document.id)).all(),
        extraction=s.scalars(
            select(Extraction).where(Extraction.case_id == case_id).order_by(Extraction.version.desc())
        ).first(),
        rule_results=s.scalars(
            select(RuleResult).where(RuleResult.case_id == case_id, RuleResult.run_no == last_rule_run)
        ).all()
        if last_rule_run
        else [],
        templates=s.scalars(
            select(TemplateVersion)
            .where(TemplateVersion.case_id == case_id)
            .order_by(TemplateVersion.version)
        ).all(),
        review_actions=s.scalars(
            select(ReviewActionRow).where(ReviewActionRow.case_id == case_id).order_by(ReviewActionRow.id)
        ).all(),
        events=s.scalars(
            select(StageEvent).where(StageEvent.case_id == case_id).order_by(StageEvent.seq)
        ).all(),
    )
    return out


@router.get("/{case_id}/events/stream")
async def stream_events(
    case_id: int,
    request: Request,
    follow: bool = True,
    last_event_id: Annotated[str | None, Header()] = None,
) -> EventSourceResponse:
    """SSE: replays stored stage_events (after Last-Event-ID), then streams live `stage_event`,
    `case_updated` and `progress` events. `follow=false` closes after the replay."""
    orch: Orchestrator = request.app.state.orchestrator
    with request.app.state.session_factory() as s:
        _load_case(s, case_id)
    after = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0

    async def gen() -> AsyncIterator[dict[str, str]]:
        last_seq = after
        queue = orch.bus.subscribe(case_id)  # before reading history so nothing is missed
        try:
            with request.app.state.session_factory() as s:
                history = s.scalars(
                    select(StageEvent)
                    .where(StageEvent.case_id == case_id, StageEvent.seq > after)
                    .order_by(StageEvent.seq)
                ).all()
                case = _load_case(s, case_id)
                for ev in history:
                    last_seq = ev.seq
                    yield {"event": "stage_event", "id": str(ev.seq), "data": dumps(event_to_dict(ev))}
                yield {"event": "case_updated", "data": dumps(case_summary(case))}
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
    _load_case(s, case_id)
    if body.action in ("remark", "reject") and not (body.text or "").strip():
        raise HTTPException(422, f"`text` is required for {body.action}")
    try:
        await orch.submit_review(case_id, body.action, body.text, body.actor)
    except OrchestratorError as e:
        raise HTTPException(409, str(e)) from e
    s.expire_all()
    return _case_out(s, orch, _load_case(s, case_id))
