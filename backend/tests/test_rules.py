"""Phase B3: every rule in config/rules.yaml at its boundary (exactly at tolerance passes, 0.01 above fails),
explain templates, overrides, decision precedence and the auto-approve gates."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from app.config_loader import ResolvedConfig, Rule, build_config, read_config_files
from app.rules.engine import (
    RULES,
    GateInput,
    LineView,
    OtherCase,
    POView,
    RuleContext,
    VendorView,
    decide,
    evaluate,
    evaluate_rule,
)
from tests.conftest import CONFIG_DIR, patch_yaml

D = Decimal
TODAY = date(2026, 10, 11)


@pytest.fixture(scope="module")
def cfg() -> ResolvedConfig:
    return build_config(read_config_files(CONFIG_DIR))


def rule(cfg: ResolvedConfig, rule_id: str, **params: Any) -> Rule:
    r = next(r for r in cfg.rules.rules if r.id == rule_id)
    return r.model_copy(update={"params": {**r.params, **params}}) if params else r


def invoice(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "vendor_name": "Acme Office Supplies Inc",
        "invoice_number": "INV-1",
        "invoice_number_normalised": "INV1",
        "invoice_date": "2026-10-02",
        "currency": "USD",
        "po_reference": "PO-1",
        "lines": [{"description": "Widget", "qty": "10", "unit_price": "100.00", "amount": "1000.00", "tax_rate": None}],
        "subtotal": "1000.00",
        "tax_total": "82.50",
        "shipping": None,
        "total": "1082.50",
        "remit_bank": {"bank_name": "B", "account_last4": "4321", "iban": None},
    }
    base.update(kw)
    return base


def ctx(**kw: Any) -> RuleContext:
    base = RuleContext(
        invoice=invoice(),
        field_confidence={"invoice_number": 0.99, "invoice_date": 0.99, "total": 0.99, "vendor_name": 0.99},
        today=TODAY,
        vendor=VendorView(1, "Acme Office Supplies Inc", "approved", "4321"),
        po=POView(1, "PO-1", 1, "USD", D("10000.00"), "open", D("0")),
        match_type="explicit",
        match_score=0.99,
        lines=[LineView(0, "Widget", D("10"), D("100.00"), D("1000.00"), 1, "Widget", D("100.00"), D("10"))],
        created_at=datetime(2026, 10, 10, tzinfo=UTC),
    )
    return replace(base, **kw)


def status(cfg: ResolvedConfig, rule_id: str, rc: RuleContext, **params: Any) -> str | None:
    out = evaluate_rule(rule(cfg, rule_id, **params), rc)
    return out.status if out else None


# ---------------------------------------------------------------- config coverage
def test_every_configured_rule_has_an_implementation(cfg: ResolvedConfig):
    assert {r.id for r in cfg.rules.rules} <= set(RULES)


def test_clean_invoice_passes_every_rule(cfg: ResolvedConfig):
    results, skipped = evaluate(cfg.rules, ctx())
    assert {r.rule_id: r.status for r in results if r.status != "pass"} == {}
    assert skipped == ["non_po_requires_review"]


# ---------------------------------------------------------------- boundaries
def test_required_fields(cfg: ResolvedConfig):
    assert status(cfg, "required_fields", ctx()) == "pass"
    out = evaluate_rule(rule(cfg, "required_fields"), ctx(invoice=invoice(invoice_number=None, invoice_date=None)))
    assert out and out.status == "fail" and out.severity == "hold"
    assert out.explanation.startswith("Missing invoice_number, invoice_date.")


def test_low_confidence_boundary(cfg: ResolvedConfig):
    at = ctx(field_confidence={"total": 0.60})
    below = ctx(field_confidence={"total": 0.59})
    assert status(cfg, "low_confidence_fields", at) == "pass"
    assert status(cfg, "low_confidence_fields", below) == "fail"


@pytest.mark.parametrize(("total", "expected"), [("1082.55", "pass"), ("1082.45", "pass"), ("1082.56", "fail"), ("1082.44", "fail")])
def test_arithmetic_boundary(cfg: ResolvedConfig, total: str, expected: str):
    assert status(cfg, "arithmetic", ctx(invoice=invoice(total=total))) == expected


def test_duplicates(cfg: ResolvedConfig):
    created = datetime(2026, 10, 1, tzinfo=UTC)
    same = OtherCase(7, "INV-2026-0007", "INV1", D("1082.50"), date(2026, 10, 2), created)
    out = evaluate_rule(rule(cfg, "duplicate_exact"), ctx(other_cases=[same]))
    assert out and out.status == "fail" and out.severity == "reject" and out.evidence["other_case_id"] == 7
    assert "INV-2026-0007" in out.explanation
    other_amount = replace(same, total=D("1082.51"))
    assert status(cfg, "duplicate_exact", ctx(other_cases=[other_amount])) == "pass"
    # near duplicate: same amount, different number, within the 30-day window
    near = replace(same, invoice_number_norm="INV2", invoice_date=date(2026, 9, 2))  # 30 days
    far = replace(near, invoice_date=date(2026, 9, 1))  # 31 days
    assert status(cfg, "duplicate_near", ctx(other_cases=[near])) == "fail"
    assert status(cfg, "duplicate_near", ctx(other_cases=[far])) == "pass"
    assert status(cfg, "duplicate_near", ctx(other_cases=[same])) == "pass"  # exact duplicate is the other rule


def test_vendor_and_bank(cfg: ResolvedConfig):
    assert status(cfg, "vendor_approved", ctx(vendor=VendorView(1, "Granite IT Hardware", "inactive"))) == "fail"
    out = evaluate_rule(rule(cfg, "vendor_approved"), ctx(vendor=None))
    assert out and out.status == "fail" and "not in the vendor master" in out.explanation
    bad = ctx(invoice=invoice(remit_bank={"bank_name": "X", "account_last4": "9910", "iban": None}))
    out = evaluate_rule(rule(cfg, "bank_details_match"), bad)
    assert out and out.status == "fail" and "9910" in out.explanation and "payment-fraud" in out.explanation
    assert status(cfg, "bank_details_match", ctx(invoice=invoice(remit_bank=None))) is None


def test_po_validity_and_implied(cfg: ResolvedConfig):
    issue = ctx(po=None, lines=[], explicit_issue="PO PO-9 quoted on the invoice does not exist.")
    out = evaluate_rule(rule(cfg, "po_valid"), issue)
    assert out and out.status == "fail" and out.explanation == "PO PO-9 quoted on the invoice does not exist."
    assert status(cfg, "po_valid", ctx(po=POView(1, "PO-1", 1, "USD", D("10000.00"), "closed", D("0")))) == "fail"
    implied = evaluate_rule(rule(cfg, "implied_po_requires_review"), ctx(match_type="implied", match_score=0.915))
    assert implied and implied.status == "fail" and "PO-1 (score 0.92)" in implied.explanation
    assert status(cfg, "implied_po_requires_review", ctx()) == "pass"


def _priced(po_price: str, inv_price: str) -> RuleContext:
    return ctx(lines=[LineView(0, "Freight", D("1"), D(inv_price), D(inv_price), 1, "Freight", D(po_price), None)])


@pytest.mark.parametrize(
    ("po_price", "inv_price", "expected"),
    [
        ("5000.00", "5100.00", "pass"),  # exactly 2% (abs 100 > 50): pct is not above tolerance
        ("5000.00", "5100.01", "fail"),  # 0.01 above 2%, abs above $50
        ("1000.00", "1050.00", "pass"),  # exactly $50 (5%): abs is not above tolerance
        ("1000.00", "1050.01", "fail"),
        ("100.00", "110.00", "pass"),  # 10% but only $10: both must exceed
        ("1250.00", "1310.00", "fail"),  # S08: +4.8% and +$60
        ("1250.00", "1200.00", "pass"),  # under-billing never fails
    ],
)
def test_price_tolerance_boundaries(cfg: ResolvedConfig, po_price: str, inv_price: str, expected: str):
    assert status(cfg, "price_tolerance", _priced(po_price, inv_price)) == expected


def test_price_tolerance_either_mode(cfg: ResolvedConfig):
    assert status(cfg, "price_tolerance", _priced("100.00", "102.01"), mode="either_exceeds") == "fail"
    assert status(cfg, "price_tolerance", _priced("100.00", "102.00"), mode="either_exceeds") == "pass"
    out = evaluate_rule(rule(cfg, "price_tolerance"), _priced("1250.00", "1310.00"))
    assert out and out.explanation == (
        "Freight: invoiced 1,310.00 vs PO 1,250.00 (4.8%, 60.00 per unit). Tolerance 2% / 50.00."
    )


def _qty(qty: str, received: str | None, before: str = "0") -> RuleContext:
    return ctx(lines=[LineView(0, "Widget", D(qty), D("100"), D("100"), 1, "Widget", D("100"), D(received) if received else None, D(before))])


def test_quantity_vs_grn_boundary(cfg: ResolvedConfig):
    assert status(cfg, "quantity_vs_grn", _qty("3", "3")) == "pass"
    assert status(cfg, "quantity_vs_grn", _qty("3.01", "3")) == "fail"
    assert status(cfg, "quantity_vs_grn", _qty("2", "3", before="1")) == "pass"
    out = evaluate_rule(rule(cfg, "quantity_vs_grn"), _qty("2", "3", before="2"))
    assert out and out.status == "fail" and out.explanation == "Widget: invoiced qty 2 but only 1 received."
    assert status(cfg, "quantity_vs_grn", _qty("5", None)) is None  # no GRN recorded: not applicable


def _balance(subtotal: str, committed: str = "6000.00", **kw: Any) -> RuleContext:
    po = POView(1, "PO-1001", 1, "USD", D("10000.00"), "open", D(committed))
    return ctx(invoice=invoice(subtotal=subtotal), po=po, **kw)


def test_po_balance_boundary(cfg: ResolvedConfig):
    assert status(cfg, "po_balance", _balance("4080.00")) == "pass"  # exactly 2% over 4,000
    assert status(cfg, "po_balance", _balance("4080.01")) == "fail"
    out = evaluate_rule(rule(cfg, "po_balance"), _balance("4300.00"))
    assert out and out.explanation == (
        "Invoice subtotal 4,300.00 vs PO PO-1001 remaining 4,000.00 (already invoiced 6,000.00 of 10,000.00): "
        "over by 7.5%, tolerance 2%."
    )
    assert status(cfg, "po_balance", _balance("4300.00", po_amendments={"PO-1001": D("10500")})) == "pass"
    assert status(cfg, "po_balance", _balance("0.01", committed="10000.00")) == "fail"  # nothing left


def test_unmatched_non_po_currency(cfg: ResolvedConfig):
    off = ctx(lines=[*ctx().lines, LineView(1, "Premium support add-on", D("1"), D("300"), D("300"))])
    out = evaluate_rule(rule(cfg, "unmatched_lines"), off)
    assert out and out.status == "fail" and out.explanation == "Lines not on PO-1: Premium support add-on."
    non_po = ctx(po=None, lines=[], match_type="none", gl_code="6100", gl_name="Facilities Maintenance", invoice=invoice(po_reference=None))
    out = evaluate_rule(rule(cfg, "non_po_requires_review"), non_po)
    assert out and out.status == "fail" and "6100 Facilities Maintenance" in out.explanation
    assert status(cfg, "price_tolerance", non_po) is None
    assert status(cfg, "non_po_requires_review", ctx()) is None
    assert status(cfg, "currency_match", ctx(invoice=invoice(currency="EUR"))) == "fail"


@pytest.mark.parametrize(
    ("day", "expected"),
    [("2026-10-14", "pass"), ("2026-10-15", "fail"), ("2025-10-11", "pass"), ("2025-10-10", "fail")],
)
def test_date_sanity_boundary(cfg: ResolvedConfig, day: str, expected: str):
    assert status(cfg, "date_sanity", ctx(invoice=invoice(invoice_date=day))) == expected


def test_tax_rate(cfg: ResolvedConfig):
    assert status(cfg, "tax_rate_valid", ctx()) == "pass"  # 82.50 on 1,000.00 = 8.25%
    assert status(cfg, "tax_rate_valid", ctx(invoice=invoice(subtotal="4415.00", tax_total="364.24"))) == "pass"
    assert status(cfg, "tax_rate_valid", ctx(invoice=invoice(tax_total="90.00"))) == "fail"
    line9 = invoice(lines=[{"description": "W", "qty": "1", "unit_price": "1", "amount": "1", "tax_rate": "9"}])
    out = evaluate_rule(rule(cfg, "tax_rate_valid"), ctx(invoice=line9))
    assert out and out.status == "fail" and out.explanation == "Tax rate 9% is not an allowed rate."


# ---------------------------------------------------------------- explain templates, overrides, severity
def test_explain_templates_use_only_evidence_keys(cfg: ResolvedConfig):
    """Each failing rule's template renders with StrictUndefined (no silently blank placeholders)."""
    strict = SandboxedEnvironment(undefined=StrictUndefined)
    other = OtherCase(7, "INV-2026-0007", "INV1", D("1082.50"), date(2026, 10, 2), datetime(2026, 10, 1, tzinfo=UTC))
    failing = {
        "required_fields": ctx(invoice=invoice(total=None)),
        "document_classification": ctx(
            uncertain_invoice={"filename": "a.pdf", "label": "invoice", "confidence": 0.5, "min_confidence": 0.7}
        ),
        "low_confidence_fields": ctx(field_confidence={"total": 0.1}),
        "arithmetic": ctx(invoice=invoice(total="1.00")),
        "duplicate_exact": ctx(other_cases=[other]),
        "duplicate_near": ctx(other_cases=[replace(other, invoice_number_norm="X")]),
        "vendor_approved": ctx(vendor=None),
        "bank_details_match": ctx(invoice=invoice(remit_bank={"account_last4": "0000"})),
        "po_valid": ctx(explicit_issue="bad"),
        "implied_po_requires_review": ctx(match_type="implied"),
        "price_tolerance": _priced("1250.00", "1310.00"),
        "quantity_vs_grn": _qty("4", "3"),
        "po_balance": _balance("4300.00"),
        "unmatched_lines": ctx(lines=[LineView(0, "X", D("1"), D("1"), D("1"))]),
        "non_po_requires_review": ctx(po=None, lines=[]),
        "currency_match": ctx(invoice=invoice(currency="EUR")),
        "date_sanity": ctx(invoice=invoice(invoice_date="2030-01-01")),
        "tax_rate_valid": ctx(invoice=invoice(tax_total="90.00")),
    }
    assert set(failing) == {r.id for r in cfg.rules.rules}
    for rid, rc in failing.items():
        r = rule(cfg, rid)
        out = evaluate_rule(r, rc)
        assert out and out.status == "fail", rid
        items = out.evidence.get("items") or [{}]
        for item in items:
            strict.from_string(r.explain).render(**{**out.evidence, **item})  # raises on a missing key


def test_override_turns_fail_into_warn(cfg: ResolvedConfig):
    off = ctx(lines=[LineView(0, "Premium", D("1"), D("300"), D("300"))], overrides={"unmatched_lines": "Accepted by reviewer."})
    out = evaluate_rule(rule(cfg, "unmatched_lines"), off)
    assert out and out.status == "warn" and out.explanation.startswith("Overridden by reviewer: Accepted by reviewer.")
    assert out.evidence["overridden"]["original"] == "Lines not on PO-1: Premium."


def test_info_severity_never_blocks(cfg: ResolvedConfig, config_files):
    files = patch_yaml(config_files, "rules.yaml", lambda d: next(r for r in d["rules"] if r["id"] == "currency_match").update(severity="info"))
    info_cfg = build_config(files)
    out = evaluate_rule(next(r for r in info_cfg.rules.rules if r.id == "currency_match"), ctx(invoice=invoice(currency="EUR")))
    assert out and out.status == "warn"


def test_disabled_rule_is_not_evaluated(config_files):
    files = patch_yaml(config_files, "rules.yaml", lambda d: next(r for r in d["rules"] if r["id"] == "bank_details_match").update(enabled=False))
    results, skipped = evaluate(build_config(files).rules, ctx(invoice=invoice(remit_bank={"account_last4": "0000"})))
    assert "bank_details_match" not in {r.rule_id for r in results} | set(skipped)


# ---------------------------------------------------------------- decision
def _gates(**kw: Any) -> GateInput:
    base = GateInput({"invoice_number": 0.95, "invoice_date": 0.95, "total": 0.95, "vendor_name": 0.95}, D("4779.24"), "explicit", "approved")
    return replace(base, **kw)


def _r(rule_id: str, status: str, severity: str) -> dict[str, Any]:
    return {"rule_id": rule_id, "status": status, "severity": severity}


def test_decision_precedence(cfg: ResolvedConfig):
    results = [_r("a", "fail", "review"), _r("b", "fail", "hold"), _r("c", "fail", "reject"), _r("d", "pass", "reject")]
    assert decide(cfg.rules, results, _gates()).severity == "reject"
    assert decide(cfg.rules, results[:2], _gates()).severity == "hold"
    assert decide(cfg.rules, results[:1], _gates()).severity == "review"
    calc = decide(cfg.rules, [_r("x", "warn", "reject")], _gates())
    assert calc.severity is None and calc.gate_failures == []  # warnings never block


@pytest.mark.parametrize(
    ("gates", "blocked"),
    [
        ({}, False),
        ({"required_confidences": {"total": 0.90}}, False),  # exactly at the gate
        ({"required_confidences": {"total": 0.89}}, True),
        ({"required_confidences": {"total": None}}, True),
        ({"total": D("25000.00")}, False),
        ({"total": D("25000.01")}, True),
        ({"match_type": "implied"}, True),
        ({"match_type": "none"}, True),
        ({"vendor_status": "pending_docs"}, True),
    ],
)
def test_auto_approve_gates(cfg: ResolvedConfig, gates: dict[str, Any], blocked: bool):
    calc = decide(cfg.rules, [_r("a", "pass", "review")], _gates(**gates))
    assert bool(calc.gate_failures) is blocked, calc.gate_failures
