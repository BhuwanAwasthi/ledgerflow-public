"""The variables an ERP profile template sees: case, vendor, inv, po, lines, version."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.case_state import effective, gl_for_line
from app.models import Case, PurchaseOrder, Vendor
from app.rules.engine import dec
from app.tenancy import scoped


def _line_tax(line: dict[str, Any], inv: dict[str, Any], subtotal: Decimal | None) -> Decimal | None:
    """Printed line rate when present, else the invoice tax spread over lines by amount."""
    amount = dec(line.get("amount"))
    if amount is None:
        return None
    rate = dec(line.get("tax_rate"))
    if rate is not None:
        return (amount * rate / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    tax = dec(inv.get("tax_total"))
    if tax is None or not subtotal:
        return None
    return (tax * amount / subtotal).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def template_context(s: Session, case: Case, state: dict[str, Any], version: int) -> dict[str, Any]:
    st = effective(state)
    inv: dict[str, Any] = (st.get("normaliser") or {}).get("invoice") or {}
    pm: dict[str, Any] = st.get("po_matching") or {}
    own = case.owner_id
    vendor = s.scalars(scoped(select(Vendor).where(Vendor.id == case.vendor_id), own)).first() if case.vendor_id else None
    po = s.scalars(scoped(select(PurchaseOrder).where(PurchaseOrder.id == pm["po_id"]), own)).first() if pm.get("po_id") else None
    subtotal = dec(inv.get("subtotal"))
    lines: list[dict[str, Any]] = []
    for i, ln in enumerate(inv.get("lines") or []):
        gl, dept = gl_for_line(st, i)
        lines.append(
            {
                **ln,
                "index": i,
                "gl_code": gl,
                "department": dept,
                "tax_amount": _line_tax(ln, inv, subtotal),
            }
        )
    return {
        "case": {
            "id": case.id,
            "ref": case.ref,
            "decision": case.decision,
            "approved_by": case.approved_by,
            "currency": case.currency,
            "total": case.total,
        },
        "vendor": {"code": vendor.code, "legal_name": vendor.legal_name, "tax_id": vendor.tax_id} if vendor else None,
        "inv": inv,
        "po": {"po_number": po.po_number, "currency": po.currency} if po else None,
        "lines": lines,
        "version": version,
    }
