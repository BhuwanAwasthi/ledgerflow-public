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
    vendor_name: str | None = None
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
    approved_by: str | None = None
    approved_at: datetime | None = None
    exported_at: datetime | None = None


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


class ExportIn(BaseModel):
    case_ids: list[int]
    erp_profile: str | None = None  # default: app.yaml default_erp_profile
    mode: Literal["zip", "batch"] = "batch"
    force: bool = False  # export cases that were already exported
    actor: str = "AP Reviewer"


class ExportBatchOut(_ORM):
    id: int
    ref: str | None
    erp_profile: str
    case_ids: list[int]
    created_by: str | None
    created_at: datetime
    filename: str | None = None
    download_url: str | None = None


class VendorRequestOut(_ORM):
    id: int
    vendor_id: int | None
    case_id: int | None
    kind: str
    proposed_name: str | None
    token: str
    docs_needed: list[str]
    docs_received: list[str]
    checks: list[dict[str, Any]]
    message: dict[str, Any] | None
    status: str
    created_at: datetime


class VendorOut(_ORM):
    id: int
    code: str
    legal_name: str
    aliases: list[str]
    tax_id: str | None
    country: str | None
    email_domain: str | None
    bank_last4: str | None
    category: str | None
    status: str
    default_gl: str | None
    non_po_allowed: bool
    created_at: datetime


class VendorDetailOut(VendorOut):
    requests: list[VendorRequestOut]
    memory: list[str]
    open_cases: list[CaseOut]


class VendorActionIn(BaseModel):
    actor: str = "Admin"
    reason: str | None = None


class VendorActionOut(BaseModel):
    vendor_id: int
    status: str
    notes: list[str]
    resumed_case_ids: list[int]


class POLineOut(_ORM):
    id: int
    line_no: int
    description: str
    qty: Decimal
    unit_price: Decimal
    amount: Decimal
    gl_code: str | None


class POConsumptionOut(_ORM):
    id: int
    po_line_id: int | None
    case_id: int
    amount: Decimal
    qty: Decimal | None
    state: str


class POOut(BaseModel):
    id: int
    po_number: str
    vendor_id: int | None
    vendor_name: str | None
    currency: str
    amount: Decimal
    consumed: Decimal
    reserved: Decimal
    remaining: Decimal
    status: str
    created_at: datetime
    approved_by: str | None
    notes: str | None


class PODetailOut(POOut):
    lines: list[POLineOut]
    consumption: list[POConsumptionOut]


class NotificationOut(_ORM):
    id: int
    audience: str
    case_id: int | None
    text: str
    read: bool
    created_at: datetime


class PortalNeedOut(BaseModel):
    id: str
    label: str
    received: bool


class PortalUploadedOut(BaseModel):
    document_id: int
    filename: str
    label: str
    confidence: float | None
    checks: list[dict[str, Any]]


class PortalOut(BaseModel):
    token: str
    kind: str
    status: str
    vendor_name: str | None
    case_ref: str | None
    invoice_number: str | None
    docs_needed: list[PortalNeedOut]
    docs_received: list[str]
    checks: list[dict[str, Any]]
    message: dict[str, Any] | None
    open: bool
    uploaded: list[PortalUploadedOut] = []
    missing: list[str] = []
    resumed: bool = False


class RerunIn(BaseModel):
    from_stage: str | None = None  # default: the stage the case failed or paused at


class JourneyStepOut(BaseModel):
    label: str
    state: Literal["done", "current", "waiting", "failed", "skipped", "pending"]
    at: datetime | None


class JourneyOut(BaseModel):
    case_id: int
    ref: str | None
    status: str
    status_label: str | None
    headline: str
    needs: list[str]
    portal_url: str | None
    steps: list[JourneyStepOut]


class DashboardKpisOut(BaseModel):
    cases: int
    decided: int
    straight_through_pct: float | None
    in_review: int
    on_hold: int
    processing: int
    median_time_to_decision_s: float | None
    exceptions_caught_amount: Decimal
    exceptions_caught_cases: int
    llm_cost_per_invoice_usd: float | None
    llm_cost_total_usd: float
    llm_tokens: int


class DecisionCountOut(BaseModel):
    decision: str
    cases: int


class RuleExceptionOut(BaseModel):
    rule_id: str
    name: str
    severity: str | None
    cases: int
    amount: Decimal


class DashboardOut(BaseModel):
    range: str
    ranges: list[str]
    since: datetime
    currency: str
    kpis: DashboardKpisOut
    series: list[dict[str, Any]]  # {date, cases, <decision>: count, ...}
    decision_keys: list[str]
    decision_mix: list[DecisionCountOut]
    exceptions_by_rule: list[RuleExceptionOut]
    seeded_cases: int
    recent: list[CaseOut]
