"""Typed access to data/scenarios.yaml (the single source of truth for synthetic master data and test
documents) plus the money maths the fixture generator, seed, API and tests share."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

CENT = Decimal("0.01")


def money(v: Decimal | float | str) -> Decimal:
    """Round half-up to cents (what a printed invoice shows)."""
    return Decimal(str(v)).quantize(CENT, rounding=ROUND_HALF_UP)


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GLAccountSpec(_M):
    code: str
    name: str
    category: str


class VendorSpec(_M):
    code: str
    legal_name: str
    tax_id: str
    email_domain: str
    bank_last4: str
    category: str
    default_gl: str
    status: str
    non_po_allowed: bool = False
    country: str = "US"
    address: str | None = None
    bank_name: str | None = None
    aliases: list[str] = Field(default_factory=list)


class POLineSpec(_M):
    line_no: int
    description: str
    qty: Decimal
    unit_price: Decimal
    gl_code: str

    @property
    def amount(self) -> Decimal:
        return money(self.qty * self.unit_price)


class POSpec(_M):
    po_number: str
    vendor: str
    lines: list[POLineSpec]
    notes: str | None = None
    created_at: date | None = None
    approved_by: str | None = None

    @property
    def amount(self) -> Decimal:
        return sum((ln.amount for ln in self.lines), Decimal("0.00"))


class GRNSpec(_M):
    po_number: str
    line_no: int
    qty_received: Decimal
    received_at: date | None = None


class LineSpec(_M):
    description: str
    qty: Decimal
    unit_price: Decimal
    uom: str | None = None

    @property
    def amount(self) -> Decimal:
        return money(self.qty * self.unit_price)


class InvoiceSpec(_M):
    vendor: str | None = None  # vendor code from the master; None for an unknown vendor
    vendor_name_printed: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    terms: str | None = None
    po_reference: str | None = None
    other_references: list[str] = Field(default_factory=list)
    lines: list[LineSpec]
    tax_rate_pct: Decimal = Decimal(0)
    bank_last4: str | None = None
    footer_note: str | None = None

    @property
    def subtotal(self) -> Decimal:
        return sum((ln.amount for ln in self.lines), Decimal("0.00"))

    @property
    def tax(self) -> Decimal:
        return money(self.subtotal * self.tax_rate_pct / Decimal(100))

    @property
    def total(self) -> Decimal:
        return self.subtotal + self.tax


class PageSpec(_M):
    invoice: InvoiceSpec


class SupportingFileSpec(_M):
    file: str
    kind: str
    account_holder: str | None = None
    bank_last4: str | None = None


class NewVendorSpec(_M):
    legal_name: str
    tax_id: str
    email_domain: str
    bank_last4: str
    category: str
    address: str | None = None
    bank_name: str | None = None


class ScenarioSpec(_M):
    id: str
    title: str
    file: str
    render: str  # clean | scan
    expect: dict[str, Any]
    invoice: InvoiceSpec | None = None
    pages: list[PageSpec] | None = None
    copy_of: str | None = None
    override: dict[str, Any] = Field(default_factory=dict)
    demo_remark: str | None = None
    # scenarios this one builds on (S02b needs S02a's consumption of PO-1001): the launcher runs them first
    # when the workspace has no run of them yet
    requires: list[str] = Field(default_factory=list)
    new_vendor: NewVendorSpec | None = None
    supporting_files: list[SupportingFileSpec] = Field(default_factory=list)


class BuyerSpec(_M):
    name: str
    address: str
    ap_email: str


class SeedHistorySpec(_M):
    enabled: bool = False
    cases: int = 0
    days_back: int = 21
    decision_mix: dict[str, float] = Field(default_factory=dict)


class Scenarios(_M):
    buyer: BuyerSpec
    gl_accounts: list[GLAccountSpec]
    vendors: list[VendorSpec]
    purchase_orders: list[POSpec]
    grns: list[GRNSpec]
    run_order: list[str]
    scenarios: list[ScenarioSpec]
    seed_history: SeedHistorySpec = Field(default_factory=SeedHistorySpec)

    def vendor(self, code: str) -> VendorSpec:
        return next(v for v in self.vendors if v.code == code)

    def po(self, po_number: str) -> POSpec:
        return next(p for p in self.purchase_orders if p.po_number == po_number)

    def scenario(self, sid: str) -> ScenarioSpec:
        for sc in self.scenarios:
            if sc.id == sid:
                return sc
        raise KeyError(sid)

    def prerequisites(self, sc: ScenarioSpec) -> list[ScenarioSpec]:
        """Every scenario `sc` builds on, transitively, in the order they must run (dependencies first)."""
        out: list[ScenarioSpec] = []

        def visit(s: ScenarioSpec, path: tuple[str, ...]) -> None:
            for dep_id in s.requires:
                if dep_id in path:
                    raise ValueError(f"scenario dependency cycle: {' -> '.join((*path, dep_id))}")
                dep = self.scenario(dep_id)
                visit(dep, (*path, dep_id))
                if dep not in out:
                    out.append(dep)

        visit(sc, (sc.id,))
        return out

    def invoices(self, sc: ScenarioSpec) -> list[InvoiceSpec]:
        """The invoice(s) printed in a scenario's file, in page order (copy_of resolved)."""
        if sc.copy_of:
            return self.invoices(self.scenario(sc.copy_of))
        if sc.pages:
            return [p.invoice for p in sc.pages]
        return [sc.invoice] if sc.invoice else []


def load_scenarios(path: Path) -> Scenarios:
    return Scenarios.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
