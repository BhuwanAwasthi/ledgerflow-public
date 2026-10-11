"""Normaliser (code): canonical invoice values for the rules.

Dates -> ISO, amounts -> Decimal (serialised as strings so nothing is lost in JSON), currency inference,
invoice-number normalisation, embedded-tax split using the line tax_rate, unit-of-measure aliases, and
missing line amounts computed from qty x unit price. Every change is listed in `changes`.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from app.agents.base import Agent, AgentContext, AgentResult
from app.documents import page_texts
from app.models import Case, Document

CENT = Decimal("0.01")


def money(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


def to_decimal(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(re.sub(r"[^\d.\-]", "", str(v)) if isinstance(v, str) else str(v))
    except InvalidOperation:
        return None


def parse_date(
    raw: str | None, text_formats: list[str], numeric: dict[str, list[str]], hint: list[str]
) -> tuple[str | None, bool]:
    """(ISO date or None, ambiguous). ISO and month-name formats first; then numeric formats in `hint`
    order (e.g. [MDY, DMY, ISO]); a date both MDY and DMY can read differently is flagged ambiguous."""
    if not raw:
        return None, False
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", re.sub(r"\s+", " ", raw.strip()))
    for fmt in numeric.get("ISO", []) + text_formats:
        for cand, f in ((s, fmt), (s.replace(",", ""), fmt.replace(",", ""))):
            try:
                return datetime.strptime(cand, f).date().isoformat(), False
            except ValueError:
                continue
    found: list[date] = []
    for order in hint:
        for fmt in numeric.get(order, []):
            try:
                found.append(datetime.strptime(s, fmt).date())
                break
            except ValueError:
                continue
    if not found:
        return None, False
    return found[0].isoformat(), len(set(found)) > 1


def normalise_invoice_number(raw: str | None, mode: str) -> str | None:
    if raw is None:
        return None
    if mode == "upper_alnum":
        return re.sub(r"[^A-Z0-9]", "", raw.upper()) or None
    return raw.strip()


def _s(v: Decimal | None) -> str | None:
    return None if v is None else str(v)


class NormaliserAgent(Agent):
    async def run(self, action: str, ctx: AgentContext) -> AgentResult:
        p = ctx.params
        raw: dict[str, Any] = ctx.state.get("extraction", {}).get("invoice") or {}
        changes: list[str] = []
        warnings: list[str] = []
        hint = list(p["date_formats_hint"])
        text_formats = list(p["date_text_formats"])
        numeric = dict(p["date_numeric_formats"])

        inv: dict[str, Any] = {k: raw.get(k) for k in raw if k not in ("confidence", "location")}

        for f in ("invoice_date", "due_date"):
            iso, ambiguous = parse_date(raw.get(f), text_formats, numeric, hint)
            if raw.get(f) and iso is None:
                warnings.append(f"{f} '{raw.get(f)}' could not be read as a date")
            elif iso and iso != raw.get(f):
                changes.append(f"{f} '{raw.get(f)}' -> {iso}")
            if ambiguous:
                warnings.append(f"{f} '{raw.get(f)}' is ambiguous (day/month); read as {iso} ({hint[0]})")
            inv[f] = iso

        number = raw.get("invoice_number")
        inv["invoice_number_normalised"] = normalise_invoice_number(number, p["invoice_number_normalisation"])
        if number and inv["invoice_number_normalised"] != number:
            changes.append(f"invoice number '{number}' -> {inv['invoice_number_normalised']}")

        inv["currency"], how = self._currency(ctx, raw.get("currency"))
        if how:
            changes.append(how)

        aliases = {k.lower(): v for k, v in (p.get("uom_aliases") or {}).items()}
        lines: list[dict[str, Any]] = []
        for i, ln in enumerate(raw.get("lines") or []):
            qty, price, amount = (
                to_decimal(ln.get("qty")),
                to_decimal(ln.get("unit_price")),
                to_decimal(ln.get("amount")),
            )
            rate = to_decimal(ln.get("tax_rate"))
            if amount is None and qty is not None and price is not None:
                amount = money(qty * price)
                changes.append(f"line {i + 1} amount computed as {qty} x {price} = {amount}")
            uom = ln.get("uom")
            if uom and uom.strip().lower() in aliases:
                canon = aliases[uom.strip().lower()]
                if canon != uom:
                    changes.append(f"line {i + 1} unit '{uom}' -> {canon}")
                uom = canon
            lines.append(
                {
                    "description": ln.get("description"),
                    "qty": _s(qty),
                    "unit_price": _s(money(price) if price is not None else None),
                    "amount": _s(money(amount) if amount is not None else None),
                    "tax_rate": _s(rate),
                    "uom": uom,
                }
            )

        subtotal = to_decimal(raw.get("subtotal"))
        tax_total = to_decimal(raw.get("tax_total"))
        shipping = to_decimal(raw.get("shipping"))
        total = to_decimal(raw.get("total"))
        tax_inclusive = bool(raw.get("tax_inclusive"))

        if tax_inclusive:
            net_sum, any_split = Decimal(0), False
            for ln in lines:
                amt, rate = to_decimal(ln["amount"]), to_decimal(ln["tax_rate"])
                if amt is None:
                    continue
                if rate:
                    net = money(amt / (1 + rate / 100))
                    ln["amount_incl_tax"], ln["amount"], ln["tax"] = _s(amt), _s(net), _s(amt - net)
                    any_split = True
                    net_sum += net
                else:
                    net_sum += amt
            if any_split:
                gross = (
                    total
                    if total is not None
                    else sum(
                        (to_decimal(ln.get("amount_incl_tax") or ln["amount"]) or 0 for ln in lines),
                        Decimal(0),
                    )
                )
                subtotal, tax_total = money(net_sum), money(gross - net_sum - (shipping or 0))
                changes.append(f"embedded tax split: net {subtotal}, tax {tax_total}")
            else:
                warnings.append("prices include tax but no line tax rate is printed; tax not split")

        if subtotal is None and lines and all(ln["amount"] is not None for ln in lines):
            subtotal = money(sum((Decimal(ln["amount"]) for ln in lines), Decimal(0)))
            changes.append(f"subtotal computed from lines = {subtotal}")

        arithmetic_ok = None
        if total is not None and subtotal is not None:
            expected = money(subtotal + (tax_total or 0) + (shipping or 0))
            arithmetic_ok = expected == money(total)
            if not arithmetic_ok:
                warnings.append(
                    f"subtotal + tax + shipping = {expected}, but the printed total is {money(total)}"
                )

        inv.update(
            {
                "lines": lines,
                "subtotal": _s(money(subtotal) if subtotal is not None else None),
                "tax_total": _s(money(tax_total) if tax_total is not None else None),
                "shipping": _s(money(shipping) if shipping is not None else None),
                "total": _s(money(total) if total is not None else None),
                "tax_inclusive": tax_inclusive,
                "arithmetic_ok": arithmetic_ok,
            }
        )

        with ctx.session_factory() as s:
            case = s.get(Case, ctx.case_id)
            assert case is not None
            case.currency = inv["currency"]
            case.total = money(total) if total is not None else None
            case.invoice_number = number
            case.invoice_number_norm = inv["invoice_number_normalised"]
            case.invoice_date = date.fromisoformat(inv["invoice_date"]) if inv.get("invoice_date") else None
            s.commit()

        number_out = inv["invoice_number_normalised"] or "(no number)"
        reasoning = [
            f"Invoice {number_out} dated {inv.get('invoice_date') or '(no date)'}, "
            + f"total {inv['total'] or '(none)'} {inv['currency']}."
        ]
        if changes:
            reasoning.append(
                f"{len(changes)} value(s) normalised: "
                + "; ".join(changes[:3])
                + ("…" if len(changes) > 3 else ".")
            )
        else:
            reasoning.append("All values were already in canonical form.")
        reasoning += [w[0].upper() + w[1:] + "." for w in warnings[:3]]
        return AgentResult(
            "ok",
            {"invoice": inv, "changes": changes, "warnings": warnings},
            reasoning[:5],
            confidence=1.0,
        )

    def _currency(self, ctx: AgentContext, raw: str | None) -> tuple[str, str | None]:
        symbols: dict[str, str] = ctx.params.get("currency_symbols") or {}
        default = ctx.params["default_currency"]
        if raw:
            r = raw.strip()
            if r in symbols:
                return symbols[r], f"currency '{r}' -> {symbols[r]}"
            if re.fullmatch(r"[A-Za-z]{3}", r):
                return r.upper(), None if r.isupper() else f"currency '{r}' -> {r.upper()}"
        text = self._text(ctx)
        for sym, code in symbols.items():
            if sym in text:
                return code, f"currency inferred from '{sym}' on the document: {code}"
        return default, f"currency not printed; default {default}"

    def _text(self, ctx: AgentContext) -> str:
        doc_id = ctx.state.get("extraction", {}).get("document_id")
        if ctx.storage is None or not doc_id:
            return ""
        with ctx.session_factory() as s:
            doc = s.get(Document, doc_id)
            if doc is None or not doc.has_text_layer:
                return ""
            return " ".join(page_texts(ctx.storage, doc))
