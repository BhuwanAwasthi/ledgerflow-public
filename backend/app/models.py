"""SQLAlchemy models: every table in SPEC section 6. Created with create_all at startup (no Alembic)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.db import Base

Money = Numeric(14, 2, asdecimal=True)
Qty = Numeric(14, 4, asdecimal=True)


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetimes on every backend (SQLite drops tzinfo on the way back)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(UTC)
        return value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value


class CaseStatus(StrEnum):
    received = "received"
    processing = "processing"
    needs_review = "needs_review"
    on_hold = "on_hold"
    approved = "approved"
    exported = "exported"
    rejected = "rejected"
    failed = "failed"
    unsupported = "unsupported"  # workflow.yaml `end:unsupported` (no invoice in the upload)
    split_bundle = "split"  # workflow.yaml `end:split` (bundle: each invoice continues as a child case)


# Statuses a workflow may finish in via `end:<status>`.
TERMINAL_STATUSES = frozenset(
    {CaseStatus.approved, CaseStatus.rejected, CaseStatus.unsupported, CaseStatus.split_bundle, CaseStatus.failed}
)


class StageStatus(StrEnum):
    started = "started"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"
    waiting = "waiting"


class ReviewAction(StrEnum):
    approve = "approve"
    remark = "remark"
    reject = "reject"
    request_info = "request_info"


def _ts() -> Mapped[datetime]:
    return mapped_column(UTCDateTime(), default=utcnow)


class Vendor(Base):
    __tablename__ = "vendors"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    legal_name: Mapped[str] = mapped_column(String(255))
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    tax_id: Mapped[str | None] = mapped_column(String(64))
    country: Mapped[str | None] = mapped_column(String(2))
    email_domain: Mapped[str | None] = mapped_column(String(255))
    bank_last4: Mapped[str | None] = mapped_column(String(4))
    iban_hash: Mapped[str | None] = mapped_column(String(64))
    category: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(16), default="approved"
    )  # approved|pending_docs|rejected|inactive
    default_gl: Mapped[str | None] = mapped_column(String(16))
    non_po_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _ts()


class VendorRequest(Base):
    __tablename__ = "vendor_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id"))
    proposed_name: Mapped[str | None] = mapped_column(String(255))
    token: Mapped[str] = mapped_column(String(64), unique=True)
    docs_needed: Mapped[list[str]] = mapped_column(JSON, default=list)
    docs_received: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|submitted|approved|rejected
    created_at: Mapped[datetime] = _ts()
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"))  # the case that asked
    kind: Mapped[str] = mapped_column(String(16), default="onboarding")  # onboarding|info
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)  # verification of uploads
    message: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # the comms draft shown on the portal


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    po_number: Mapped[str] = mapped_column(String(32), unique=True)
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id"))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    amount: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|closed
    created_at: Mapped[datetime] = _ts()
    approved_by: Mapped[str | None] = mapped_column(String(128))
    notes: Mapped[str | None] = mapped_column(Text)  # e.g. "Raised from quote Q-77" (implied-PO evidence)


class POLine(Base):
    __tablename__ = "po_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"))
    line_no: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text)
    qty: Mapped[Decimal] = mapped_column(Qty)
    unit_price: Mapped[Decimal] = mapped_column(Money)
    amount: Mapped[Decimal] = mapped_column(Money)
    gl_code: Mapped[str | None] = mapped_column(String(16))


class POAmendment(Base):
    """Audit trail of changes to a PO's master amount (a reviewer's set_po_amount_note on approval, or a
    master-data CSV import)."""

    __tablename__ = "po_amendments"
    id: Mapped[int] = mapped_column(primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"))  # None for an import
    amended_by: Mapped[str] = mapped_column(String(128))
    old_amount: Mapped[Decimal] = mapped_column(Money)
    new_amount: Mapped[Decimal] = mapped_column(Money)
    reason: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="remark")  # remark|import
    created_at: Mapped[datetime] = _ts()


class GRN(Base):
    __tablename__ = "grns"
    id: Mapped[int] = mapped_column(primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"))
    line_no: Mapped[int] = mapped_column(Integer)
    qty_received: Mapped[Decimal] = mapped_column(Qty)
    received_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class GLAccount(Base):
    __tablename__ = "gl_accounts"
    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(64))


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[int] = mapped_column(primary_key=True)
    ref: Mapped[str | None] = mapped_column(String(32), unique=True)
    parent_case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"))
    submitter_name: Mapped[str | None] = mapped_column(String(255))
    submitter_email: Mapped[str | None] = mapped_column(String(255))
    note: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="upload")  # portal|upload|imap|fixture
    status: Mapped[str] = mapped_column(String(16), default=CaseStatus.received)
    current_stage: Mapped[str | None] = mapped_column(String(64))
    decision: Mapped[str | None] = mapped_column(String(32))
    decision_summary: Mapped[str | None] = mapped_column(Text)
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id"))
    po_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id"))
    currency: Mapped[str | None] = mapped_column(String(3))
    total: Mapped[Decimal | None] = mapped_column(Money)
    invoice_number: Mapped[str | None] = mapped_column(String(64))  # as printed (set by the normaliser)
    invoice_number_norm: Mapped[str | None] = mapped_column(String(64), index=True)  # duplicate checks
    invoice_date: Mapped[date | None] = mapped_column(Date)
    confidence: Mapped[float | None] = mapped_column(Float)
    config_hash: Mapped[str | None] = mapped_column(String(64))
    config_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    sla_due_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    seed: Mapped[bool] = mapped_column(Boolean, default=False)  # seeded demo history (labelled in the UI)
    approved_by: Mapped[str | None] = mapped_column(String(128))  # reviewer, or "auto" (set at finalize)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    exported_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str | None] = mapped_column(String(128))
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(512))
    pages: Mapped[int | None] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(String(32))
    label_confidence: Mapped[float | None] = mapped_column(Float)
    page_from: Mapped[int | None] = mapped_column(Integer)
    page_to: Mapped[int | None] = mapped_column(Integer)
    has_text_layer: Mapped[bool | None] = mapped_column(Boolean)


class Extraction(Base):
    __tablename__ = "extractions"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    field_meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class POConsumption(Base):
    __tablename__ = "po_consumption"
    id: Mapped[int] = mapped_column(primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    po_line_id: Mapped[int | None] = mapped_column(ForeignKey("po_lines.id"))
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"))
    amount: Mapped[Decimal] = mapped_column(Money)
    qty: Mapped[Decimal | None] = mapped_column(Qty)
    state: Mapped[str] = mapped_column(String(16), default="reserved")  # reserved|consumed|released


class RuleResult(Base):
    __tablename__ = "rule_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    run_no: Mapped[int] = mapped_column(Integer)
    rule_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(8))  # pass|warn|fail
    severity: Mapped[str] = mapped_column(String(8))
    explanation: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class StageEvent(Base):
    """Append-only: one row per stage transition (started, then completed/failed/waiting, or skipped)."""

    __tablename__ = "stage_events"
    __table_args__ = (UniqueConstraint("case_id", "seq"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    run_no: Mapped[int] = mapped_column(Integer)
    seq: Mapped[int] = mapped_column(Integer)
    stage_id: Mapped[str] = mapped_column(String(64))
    agent_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    outcome: Mapped[str | None] = mapped_column(String(64))
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reasoning: Mapped[list[str]] = mapped_column(JSON, default=list)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = _ts()
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class TemplateVersion(Base):
    __tablename__ = "template_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    erp_profile: Mapped[str] = mapped_column(String(64))
    rows: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    file_key: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(8), default="draft")  # draft|final
    diff_from_prev: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(16), default="agent")  # agent|reviewer


class ReviewActionRow(Base):
    __tablename__ = "review_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(16))  # approve|remark|reject|request_info|override
    text: Mapped[str | None] = mapped_column(Text)
    changeset: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = _ts()


class ExportBatch(Base):
    __tablename__ = "export_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    ref: Mapped[str | None] = mapped_column(String(32), unique=True)
    erp_profile: Mapped[str] = mapped_column(String(64))
    case_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    file_key: Mapped[str | None] = mapped_column(String(512))
    created_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = _ts()


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    audience: Mapped[str] = mapped_column(String(16))  # submitter|reviewer
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"))
    text: Mapped[str] = mapped_column(Text)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _ts()


class VendorMemory(Base):
    __tablename__ = "vendor_memory"
    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), index=True)
    hint: Mapped[str] = mapped_column(Text)
    source_case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"))
    created_at: Mapped[datetime] = _ts()


class ConfigVersion(Base):
    __tablename__ = "config_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(255), index=True)
    content: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    author: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = _ts()


class LLMCache(Base):
    __tablename__ = "llm_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    response: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = _ts()
