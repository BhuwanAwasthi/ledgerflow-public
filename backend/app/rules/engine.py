"""Deterministic procurement rules (config/rules.yaml) and the decision function.

Every rule is a small pure function `(params, RuleContext) -> Check | None` (None = not applicable to this
invoice). `evaluate` runs the enabled rules, applies reviewer overrides and renders each rule's `explain`
template (sandboxed Jinja2) from the evidence, so the wording a reviewer sees is reproducible. `decide`
turns the results into a decision: highest failed severity by `decision_precedence`, else the
`auto_approve_gates`. No LLM is involved anywhere in this module.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from jinja2.sandbox import SandboxedEnvironment

from app.config_loader import Rule, RulesConfig

_JINJA = SandboxedEnvironment(autoescape=False)
CENT = Decimal("0.01")
ZERO = Decimal(0)


# ---------------------------------------------------------------- inputs
@dataclass
class VendorView:
    id: int
    legal_name: str
    status: str
    bank_last4: str | None = None
    iban_hash: str | None = None


@dataclass
class POView:
    id: int
    po_number: str
    vendor_id: int | None
    currency: str
    amount: Decimal  # as in the master data
    status: str
    committed_by_others: Decimal  # reserved + consumed by other cases


@dataclass
class LineView:
    index: int  # 0-based invoice line index
    description: str
    qty: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None
    po_line_no: int | None = None  # None = not on the PO
    po_description: str | None = None
    po_unit_price: Decimal | None = None
    grn_received: Decimal | None = None  # None = no GRN recorded for this PO line
    invoiced_before: Decimal = ZERO  # qty already invoiced against this PO line by other cases


@dataclass
class OtherCase:
    case_id: int
    ref: str
    invoice_number_norm: str | None
    total: Decimal | None
    invoice_date: date | None
    created_at: datetime


@dataclass
class RuleContext:
    invoice: dict[str, Any]  # normaliser output (amounts as decimal strings)
    field_confidence: dict[str, float | None]
    today: date
    vendor: VendorView | None = None
    invoice_iban_hash: str | None = None
    po: POView | None = None
    match_type: str = "none"  # explicit | implied | none
    match_score: float | None = None
    explicit_issue: str | None = None  # why a printed PO reference could not be used
    lines: list[LineView] = field(default_factory=list)
    gl_code: str | None = None
    gl_name: str | None = None
    other_cases: list[OtherCase] = field(default_factory=list)  # same vendor, still live
    created_at: datetime | None = None
    overrides: dict[str, str] = field(default_factory=dict)  # rule_id -> reviewer reason
    po_amendments: dict[str, Decimal] = field(default_factory=dict)  # po_number -> amended amount

    @property
    def po_reference(self) -> str | None:
        return self.invoice.get("po_reference") or None

    @property
    def po_amount(self) -> Decimal | None:
        if self.po is None:
            return None
        return self.po_amendments.get(self.po.po_number, self.po.amount)


@dataclass
class Check:
    failed: bool
    evidence: dict[str, Any] = field(default_factory=dict)
    items: list[dict[str, Any]] = field(default_factory=list)  # one explain line per offending item


@dataclass
class RuleOutcome:
    rule_id: str
    name: str
    status: str  # pass | warn | fail
    severity: str
    explanation: str
    evidence: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "status": self.status,
            "severity": self.severity,
            "explanation": self.explanation,
            "evidence": self.evidence,
        }


# ---------------------------------------------------------------- helpers
def dec(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def fmt_money(v: Decimal | None) -> str:
    return "n/a" if v is None else f"{v.quantize(CENT, rounding=ROUND_HALF_UP):,.2f}"


def fmt_pct(v: Decimal) -> str:
    """7.50 -> '7.5', 2 -> '2', 4.8000 -> '4.8' (two decimals at most)."""
    s = f"{v.quantize(CENT, rounding=ROUND_HALF_UP):f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def pct_over(value: Decimal, base: Decimal) -> Decimal:
    return (value - base) / base * 100


def _sum_or_none(values: list[Decimal | None]) -> Decimal | None:
    known = [v for v in values if v is not None]
    return sum(known, ZERO) if values and len(known) == len(values) else None


def _p(params: dict[str, Any], key: str) -> Decimal:
    return Decimal(str(params[key]))


# ---------------------------------------------------------------- rules
def required_fields(params: dict[str, Any], rc: RuleContext) -> Check | None:
    missing = [f for f in params["fields"] if rc.invoice.get(f) in (None, "")]
    return Check(bool(missing), {"missing": missing})


def low_confidence_fields(params: dict[str, Any], rc: RuleContext) -> Check | None:
    floor = float(params["min_confidence"])
    weak = sorted(f for f, c in rc.field_confidence.items() if c is not None and c < floor)
    return Check(bool(weak), {"fields": weak, "min_confidence": floor})


def arithmetic(params: dict[str, Any], rc: RuleContext) -> Check | None:
    total = dec(rc.invoice.get("total"))
    lines_sum = _sum_or_none([dec(ln.get("amount")) for ln in rc.invoice.get("lines") or []])
    if lines_sum is None:
        lines_sum = dec(rc.invoice.get("subtotal"))
    if total is None or lines_sum is None:
        return None
    tax = dec(rc.invoice.get("tax_total")) or ZERO
    shipping = dec(rc.invoice.get("shipping")) or ZERO
    computed = lines_sum + tax + shipping
    diff = abs(computed - total)
    return Check(
        diff > _p(params, "abs_tolerance"),
        {
            "lines_sum": fmt_money(lines_sum),
            "tax": fmt_money(tax),
            "shipping": fmt_money(shipping),
            "computed": fmt_money(computed),
            "total": fmt_money(total),
            "difference": fmt_money(diff),
        },
    )


def duplicate_exact(params: dict[str, Any], rc: RuleContext) -> Check | None:
    number, total = rc.invoice.get("invoice_number_normalised"), dec(rc.invoice.get("total"))
    if rc.vendor is None or not number or total is None:
        return None
    for o in rc.other_cases:
        if o.invoice_number_norm == number and o.total is not None and o.total == total:
            return Check(
                True,
                {
                    "number": rc.invoice.get("invoice_number") or number,
                    "amount": fmt_money(total),
                    "other_ref": o.ref,
                    "other_case_id": o.case_id,
                    "other_date": o.created_at.date().isoformat(),
                },
            )
    return Check(False, {"number": number, "amount": fmt_money(total)})


def duplicate_near(params: dict[str, Any], rc: RuleContext) -> Check | None:
    number, total = rc.invoice.get("invoice_number_normalised"), dec(rc.invoice.get("total"))
    if rc.vendor is None or total is None:
        return None
    mine = _invoice_day(rc.invoice.get("invoice_date")) or (rc.created_at.date() if rc.created_at else rc.today)
    window = int(params["window_days"])
    for o in rc.other_cases:
        if o.total != total or (number and o.invoice_number_norm == number):
            continue
        days = abs((mine - (o.invoice_date or o.created_at.date())).days)
        if days <= window:
            return Check(
                True,
                {"amount": fmt_money(total), "other_ref": o.ref, "other_case_id": o.case_id, "days": days},
            )
    return Check(False, {"amount": fmt_money(total), "window_days": window})


def vendor_approved(params: dict[str, Any], rc: RuleContext) -> Check | None:
    if rc.vendor is None:
        return Check(True, {"vendor_name": rc.invoice.get("vendor_name") or "The vendor", "status": "not in the vendor master"})
    return Check(rc.vendor.status != "approved", {"vendor_name": rc.vendor.legal_name, "status": rc.vendor.status})


def bank_details_match(params: dict[str, Any], rc: RuleContext) -> Check | None:
    bank = rc.invoice.get("remit_bank") or {}
    inv_last4 = (bank.get("account_last4") or "").strip() or None
    if rc.vendor is None:
        return None
    ev = {"invoice_last4": inv_last4, "master_last4": rc.vendor.bank_last4}
    if rc.invoice_iban_hash and rc.vendor.iban_hash and rc.invoice_iban_hash != rc.vendor.iban_hash:
        return Check(True, {**ev, "iban_changed": True})
    if inv_last4 is None or rc.vendor.bank_last4 is None:
        return None
    return Check(inv_last4 != rc.vendor.bank_last4, ev)


def po_valid(params: dict[str, Any], rc: RuleContext) -> Check | None:
    if rc.explicit_issue:
        return Check(True, {"reason": rc.explicit_issue, "po_reference": rc.po_reference})
    if rc.po is None:
        return None
    if rc.po.status != "open":
        return Check(True, {"reason": f"{rc.po.po_number} is {rc.po.status}.", "po_number": rc.po.po_number})
    if rc.vendor is None or rc.po.vendor_id != rc.vendor.id:
        return Check(True, {"reason": f"{rc.po.po_number} belongs to a different vendor.", "po_number": rc.po.po_number})
    return Check(False, {"reason": f"{rc.po.po_number} is open and belongs to the vendor.", "po_number": rc.po.po_number})


def implied_po_requires_review(params: dict[str, Any], rc: RuleContext) -> Check | None:
    if rc.po is None:
        return None
    score = rc.match_score if rc.match_score is not None else 0.0
    return Check(
        rc.match_type == "implied",
        {"po_number": rc.po.po_number, "score": f"{score:.2f}", "match_type": rc.match_type, "min_score": params["min_score"]},
    )


def price_tolerance(params: dict[str, Any], rc: RuleContext) -> Check | None:
    if rc.po is None:
        return None
    tol_pct, tol_abs, mode = _p(params, "pct"), _p(params, "abs"), params["mode"]
    items: list[dict[str, Any]] = []
    checked = 0
    for ln in rc.lines:
        if ln.unit_price is None or not ln.po_unit_price:
            continue
        checked += 1
        diff = ln.unit_price - ln.po_unit_price
        pct = pct_over(ln.unit_price, ln.po_unit_price)
        over_pct, over_abs = pct > tol_pct, diff > tol_abs
        if (over_pct and over_abs) if mode == "both_must_exceed" else (over_pct or over_abs):
            items.append(
                {
                    "line_index": ln.index,
                    "line_desc": ln.description,
                    "inv_price": fmt_money(ln.unit_price),
                    "po_price": fmt_money(ln.po_unit_price),
                    "pct": fmt_pct(pct),
                    "abs_diff": fmt_money(diff),
                }
            )
    if not checked:
        return None
    ev = {"tol_pct": fmt_pct(tol_pct), "tol_abs": fmt_money(tol_abs), "mode": mode, "lines_checked": checked}
    return Check(bool(items), ev, items)


def quantity_vs_grn(params: dict[str, Any], rc: RuleContext) -> Check | None:
    if rc.po is None:
        return None
    items: list[dict[str, Any]] = []
    checked = 0
    for ln in rc.lines:
        if ln.grn_received is None or ln.qty is None:
            continue
        checked += 1
        available = ln.grn_received - ln.invoiced_before
        if ln.qty > available:
            items.append(
                {
                    "line_index": ln.index,
                    "line_desc": ln.description,
                    "inv_qty": fmt_pct(ln.qty),
                    "grn_qty": fmt_pct(max(available, ZERO)),
                    "received": fmt_pct(ln.grn_received),
                    "invoiced_before": fmt_pct(ln.invoiced_before),
                }
            )
    if not checked:
        return None
    return Check(bool(items), {"lines_checked": checked}, items)


def po_balance(params: dict[str, Any], rc: RuleContext) -> Check | None:
    if rc.po is None or rc.po_amount is None:
        return None
    amount = dec(rc.invoice.get("subtotal"))
    if amount is None:
        amount = _sum_or_none([ln.amount for ln in rc.lines])
    if amount is None:
        return None
    remaining = rc.po_amount - rc.po.committed_by_others
    tol = _p(params, "pct")
    if remaining > 0:
        over = pct_over(amount, remaining)
        failed = over > tol
        over_txt = fmt_pct(max(over, ZERO))
    else:
        failed = amount > 0
        over_txt = "100+"
    ev = {
        "amount": fmt_money(amount),
        "po_number": rc.po.po_number,
        "remaining": fmt_money(remaining),
        "consumed": fmt_money(rc.po.committed_by_others),
        "po_amount": fmt_money(rc.po_amount),
        "over_pct": over_txt,
        "tol_pct": fmt_pct(tol),
    }
    if rc.po_amount != rc.po.amount:
        ev["amended_from"] = fmt_money(rc.po.amount)
    return Check(failed, ev)


def unmatched_lines(params: dict[str, Any], rc: RuleContext) -> Check | None:
    if rc.po is None or not rc.lines:
        return None
    off = [ln for ln in rc.lines if ln.po_line_no is None]
    return Check(
        bool(off),
        {"po_number": rc.po.po_number, "lines": [ln.description for ln in off], "line_indexes": [ln.index for ln in off]},
    )


def non_po_requires_review(params: dict[str, Any], rc: RuleContext) -> Check | None:
    if rc.po is not None:
        return None
    return Check(True, {"gl_code": rc.gl_code or "(none)", "gl_name": rc.gl_name or ""})


def currency_match(params: dict[str, Any], rc: RuleContext) -> Check | None:
    cur = rc.invoice.get("currency")
    if rc.po is None or not cur:
        return None
    return Check(cur.upper() != rc.po.currency.upper(), {"inv_currency": cur, "po_currency": rc.po.currency})


def date_sanity(params: dict[str, Any], rc: RuleContext) -> Check | None:
    d = _invoice_day(rc.invoice.get("invoice_date"))
    if d is None:
        return None
    ahead = (d - rc.today).days
    age = (rc.today - d).days
    ev = {"date": d.isoformat()}
    if ahead > int(params["max_future_days"]):
        return Check(True, {**ev, "reason": f"{ahead} days in the future"})
    if age > int(params["max_age_days"]):
        return Check(True, {**ev, "reason": f"{age} days old (limit {params['max_age_days']})"})
    return Check(False, {**ev, "reason": "plausible"})


def tax_rate_valid(params: dict[str, Any], rc: RuleContext) -> Check | None:
    allowed = [Decimal(str(r)) for r in params["allowed_rates_pct"]]
    printed = {r for ln in rc.invoice.get("lines") or [] if (r := dec(ln.get("tax_rate"))) is not None}
    if printed:
        bad = sorted(r for r in printed if r not in allowed)
        return Check(bool(bad), {"rate": ", ".join(fmt_pct(r) for r in bad) or None, "source": "lines"})
    tax, base = dec(rc.invoice.get("tax_total")), dec(rc.invoice.get("subtotal"))
    if tax is None or not base:
        return None
    slack = _p(params, "rounding_tolerance")
    ok = any(abs(base * r / 100 - tax) <= slack for r in allowed)
    return Check(not ok, {"rate": fmt_pct(tax / base * 100), "source": "tax_total / subtotal"})


def _invoice_day(v: Any) -> date | None:
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return None


RuleFn = Callable[[dict[str, Any], RuleContext], Check | None]
RULES: dict[str, RuleFn] = {
    fn.__name__: fn
    for fn in (
        required_fields,
        low_confidence_fields,
        arithmetic,
        duplicate_exact,
        duplicate_near,
        vendor_approved,
        bank_details_match,
        po_valid,
        implied_po_requires_review,
        price_tolerance,
        quantity_vs_grn,
        po_balance,
        unmatched_lines,
        non_po_requires_review,
        currency_match,
        date_sanity,
        tax_rate_valid,
    )
}


# ---------------------------------------------------------------- evaluation
def applies(rule: Rule, rc: RuleContext) -> bool:
    if rule.applies_to == "po":
        return rc.po is not None or bool(rc.po_reference)
    if rule.applies_to == "non_po":
        return rc.po is None
    return True


def render(template: str, evidence: dict[str, Any], items: list[dict[str, Any]]) -> str:
    tpl = _JINJA.from_string(template)
    if items:
        return " ".join(tpl.render(**{**evidence, **item}) for item in items)
    return tpl.render(**evidence)


def evaluate_rule(rule: Rule, rc: RuleContext) -> RuleOutcome | None:
    """One rule; None when it does not apply to this invoice."""
    fn = RULES.get(rule.id)
    if fn is None:
        return RuleOutcome(rule.id, rule.name, "warn", "info", f"No implementation for rule '{rule.id}'.", {})
    if not applies(rule, rc):
        return None
    check = fn(rule.params, rc)
    if check is None:
        return None
    evidence = {**check.evidence, **({"items": check.items} if check.items else {})}
    if not check.failed:
        return RuleOutcome(rule.id, rule.name, "pass", rule.severity, f"{rule.name}: OK.", evidence)
    text = render(rule.explain, check.evidence, check.items)
    if rule.id in rc.overrides:
        reason = rc.overrides[rule.id]
        evidence["overridden"] = {"reason": reason, "original": text}
        return RuleOutcome(rule.id, rule.name, "warn", rule.severity, f"Overridden by reviewer: {reason} (was: {text})", evidence)
    # info-severity rules never block: a failure is shown as a warning
    status = "warn" if rule.severity == "info" else "fail"
    return RuleOutcome(rule.id, rule.name, status, rule.severity, text, evidence)


def evaluate(cfg: RulesConfig, rc: RuleContext) -> tuple[list[RuleOutcome], list[str]]:
    """Every enabled rule in config order. Returns (results, ids of rules that did not apply)."""
    results: list[RuleOutcome] = []
    skipped: list[str] = []
    for rule in cfg.rules:
        if not rule.enabled:
            continue
        out = evaluate_rule(rule, rc)
        if out is None:
            skipped.append(rule.id)
        else:
            results.append(out)
    return results, skipped


# ---------------------------------------------------------------- decision
@dataclass
class GateInput:
    required_confidences: dict[str, float | None]
    total: Decimal | None
    match_type: str
    vendor_status: str | None


@dataclass
class DecisionCalc:
    severity: str | None  # highest failed severity, None when nothing failed
    failed_rules: list[str]
    gate_failures: list[str]


def check_gates(gates: dict[str, Any], g: GateInput) -> list[str]:
    """Reasons an otherwise clean invoice may not auto-approve (empty = all gates pass)."""
    out: list[str] = []
    if "min_extraction_confidence" in gates:
        floor = float(gates["min_extraction_confidence"])
        weak = [f for f, c in g.required_confidences.items() if c is None or c < floor]
        if weak:
            out.append(f"Read confidence below {floor:.2f} on {', '.join(sorted(weak))}.")
    if "max_amount" in gates and g.total is not None and g.total > Decimal(str(gates["max_amount"])):
        out.append(f"Total {fmt_money(g.total)} is above the auto-approve limit {fmt_money(Decimal(str(gates['max_amount'])))}.")
    if gates.get("require_explicit_po") and g.match_type != "explicit":
        out.append("Only invoices quoting a valid PO number can auto-approve" + (" (this PO was inferred)." if g.match_type == "implied" else " (no PO)."))
    if gates.get("require_approved_vendor") and g.vendor_status != "approved":
        out.append(f"Vendor is {g.vendor_status or 'unknown'}, not approved.")
    return out


def decide(cfg: RulesConfig, results: list[dict[str, Any]], gates: GateInput) -> DecisionCalc:
    failed = [r for r in results if r["status"] == "fail"]
    severities = {r["severity"] for r in failed}
    severity = next((sev for sev in cfg.decision_precedence if sev in severities), None)
    gate_failures = [] if severity else check_gates(cfg.auto_approve_gates, gates)
    return DecisionCalc(severity, [r["rule_id"] for r in failed], gate_failures)
