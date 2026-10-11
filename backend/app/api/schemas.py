"""API response/request models (they drive /openapi.json and the generated TS client)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class _ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class HealthOut(BaseModel):
    ok: bool
    db: str
    storage: str
    llm_path: str
    config_hash: str | None = None


class DocumentOut(_ORM):
    id: int
    filename: str
    mime: str | None
    sha256: str
    pages: int | None
    label: str | None
    label_confidence: float | None
    page_from: int | None
    page_to: int | None
    has_text_layer: bool | None


class StageEventOut(_ORM):
    id: int
    case_id: int
    run_no: int
    seq: int
    stage_id: str
    agent_id: str | None
    action: str | None
    status: str
    outcome: str | None
    input_summary: dict[str, Any]
    output: dict[str, Any]
    reasoning: list[str]
    metrics: dict[str, Any]
    started_at: datetime
    ended_at: datetime | None


class ExtractionOut(_ORM):
    id: int
    version: int
    data: dict[str, Any]
    field_meta: dict[str, Any]


class RuleResultOut(_ORM):
    id: int
    run_no: int
    rule_id: str
    status: str
    severity: str
    explanation: str
    evidence: dict[str, Any]


class TemplateVersionOut(_ORM):
    id: int
    version: int
    erp_profile: str
    rows: list[dict[str, Any]]
    status: str
    diff_from_prev: dict[str, Any] | None
    created_by: str


class ReviewActionOut(_ORM):
    id: int
    actor: str
    action: str
    text: str | None
    changeset: dict[str, Any] | None
    created_at: datetime


class CaseOut(_ORM):
    id: int
    ref: str | None
    parent_case_id: int | None
    submitter_name: str | None
    submitter_email: str | None
    note: str | None
    source: str
    status: str
    current_stage: str | None
    status_label: str | None = None
    decision: str | None
    decision_summary: str | None
    vendor_id: int | None
    po_id: int | None
    currency: str | None
    total: Decimal | None
    invoice_number: str | None = None
    invoice_date: date | None = None
    confidence: float | None
    config_hash: str | None
    created_at: datetime
    updated_at: datetime
    sla_due_at: datetime | None
    seed: bool = False


class CaseDetailOut(CaseOut):
    documents: list[DocumentOut]
    extraction: ExtractionOut | None
    rule_results: list[RuleResultOut]
    templates: list[TemplateVersionOut]
    review_actions: list[ReviewActionOut]
    events: list[StageEventOut]


class CaseListOut(BaseModel):
    items: list[CaseOut]
    total: int
    page: int
    page_size: int


class ReviewIn(BaseModel):
    action: Literal["approve", "remark", "reject", "request_info"]
    text: str | None = None
    actor: str = "AP Reviewer"


class FixtureFileOut(BaseModel):
    file: str
    kind: str
    available: bool


class FixtureOut(BaseModel):
    id: str
    title: str
    file: str
    render: str
    run_order: int | None
    expect: dict[str, Any]
    demo_remark: str | None
    available: bool
    supporting_files: list[FixtureFileOut]
