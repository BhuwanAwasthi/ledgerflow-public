"""Policy engine (code): runs every enabled rule in rules.yaml through rules/engine.py and stores RuleResult
rows (pass / warn / fail, severity, plain-English explanation, evidence).

Reviewer changes from the reconciler (B4) arrive in case state under `reconciler`:
`rule_overrides {rule_id: reason}` and `po_amendments {po_number: amount}`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import po_ledger
from app.agents.base import Agent, AgentContext, AgentResult
from app.agents.vendor import iban_hash
from app.models import Case, PurchaseOrder, RuleResult, Vendor
from app.rules.engine import LineView, OtherCase, POView, RuleContext, VendorView, dec, evaluate

# statuses whose invoices still count for duplicate detection
_DEAD = ("failed", "unsupported", "split", "rejected")


def today() -> date:
    """Clock for date rules (tests pin it)."""
    return datetime.now(UTC).date()


def build_rule_context(s: Session, case: Case, state: dict[str, Any]) -> RuleContext:
    inv: dict[str, Any] = state.get("normaliser", {}).get("invoice") or {}
    meta: dict[str, Any] = state.get("extraction", {}).get("field_meta") or {}
    pm: dict[str, Any] = state.get("po_matching", {})
    rec: dict[str, Any] = state.get("reconciler", {})

    vendor_id = state.get("vendor", {}).get("vendor_id") or case.vendor_id
    v = s.get(Vendor, vendor_id) if vendor_id else None
    vendor = VendorView(v.id, v.legal_name, v.status, v.bank_last4, v.iban_hash) if v else None

    po_view: POView | None = None
    po_id = pm.get("po_id")
    po = s.get(PurchaseOrder, po_id) if po_id else None
    if po is not None:
        po_view = POView(
            po.id, po.po_number, po.vendor_id, po.currency, po.amount, po.status,
            po_ledger.committed(s, po.id, exclude_case_id=case.id),
        )

    lines = [
        LineView(
            index=ln["index"],
            description=ln.get("description") or f"line {ln['index'] + 1}",
            qty=dec(ln.get("qty")),
            unit_price=dec(ln.get("unit_price")),
            amount=dec(ln.get("amount")),
            po_line_no=ln.get("po_line_no"),
            po_description=ln.get("po_description"),
            po_unit_price=dec(ln.get("po_unit_price")),
            grn_received=dec(ln.get("grn_received")),
            invoiced_before=dec(ln.get("invoiced_before")) or Decimal(0),
        )
        for ln in (pm.get("lines") or [])
    ] if po is not None else []

    others: list[OtherCase] = []
    if v is not None:
        rows = s.scalars(
            select(Case).where(
                # only earlier cases: two copies processed concurrently must not reject each other
                Case.vendor_id == v.id, Case.id < case.id, Case.seed.is_(False), Case.status.not_in(_DEAD)
            )
        ).all()
        others = [
            OtherCase(c.id, c.ref or f"case {c.id}", c.invoice_number_norm, c.total, c.invoice_date, c.created_at)
            for c in rows
        ]

    gl = pm.get("gl") or {}
    return RuleContext(
        invoice=inv,
        field_confidence={f: m.get("confidence") for f, m in meta.items() if inv.get(f) not in (None, "", [])},
        today=today(),
        vendor=vendor,
        invoice_iban_hash=iban_hash((inv.get("remit_bank") or {}).get("iban")),
        po=po_view,
        match_type=pm.get("match_type") or "none",
        match_score=pm.get("score"),
        explicit_issue=pm.get("explicit_issue"),
        lines=lines,
        gl_code=gl.get("primary"),
        gl_name=gl.get("primary_name"),
        other_cases=others,
        created_at=case.created_at,
        overrides=dict(rec.get("rule_overrides") or {}),
        po_amendments={k: Decimal(str(a)) for k, a in (rec.get("po_amendments") or {}).items()},
    )


class PolicyAgent(Agent):
    async def run(self, action: str, ctx: AgentContext) -> AgentResult:
        with ctx.session_factory() as s:
            case = s.get(Case, ctx.case_id)
            assert case is not None
            rc = build_rule_context(s, case, ctx.state)
            results, skipped = evaluate(ctx.config.rules, rc)
            s.execute(delete(RuleResult).where(RuleResult.case_id == case.id, RuleResult.run_no == ctx.run_no))
            for r in results:
                s.add(
                    RuleResult(
                        case_id=case.id,
                        run_no=ctx.run_no,
                        rule_id=r.rule_id,
                        status=r.status,
                        severity=r.severity,
                        explanation=r.explanation,
                        evidence=r.evidence,
                    )
                )
            s.commit()

        failed = [r for r in results if r.status == "fail"]
        warned = [r for r in results if r.status == "warn"]
        passed = len(results) - len(failed) - len(warned)
        reasoning = [
            f"{len(results)} rules checked: {passed} passed, {len(failed)} failed, {len(warned)} warned"
            + (f", {len(skipped)} not applicable." if skipped else ".")
        ]
        order = {sev: i for i, sev in enumerate(ctx.config.rules.decision_precedence)}
        reasoning += [r.explanation for r in sorted(failed, key=lambda r: order.get(r.severity, 9))][:3]
        if len(reasoning) < 5:
            reasoning += [r.explanation for r in warned][: 5 - len(reasoning)]
        return AgentResult(
            "evaluated",
            {
                "results": [r.as_dict() for r in results],
                "not_applicable": skipped,
                "failed": [r.rule_id for r in failed],
                "warned": [r.rule_id for r in warned],
                "run_no": ctx.run_no,
            },
            reasoning[:5],
            confidence=1.0,
        )
