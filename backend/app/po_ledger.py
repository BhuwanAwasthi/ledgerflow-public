"""PO consumption ledger (SPEC section 6): remaining = PO amount - (reserved + consumed).

A case reserves its invoice lines at the decision stage (auto_approve / needs_review), so split invoices
processed one after the other or concurrently can't both pass the balance check. Approval consumes the
reservation and rejection releases it. Rows are never deleted: a re-decision releases the old rows first.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import POConsumption, PurchaseOrder

ACTIVE = ("reserved", "consumed")


@dataclass
class Reservation:
    po_line_id: int | None  # None = an invoice line that is not on the PO (counts against the header)
    amount: Decimal
    qty: Decimal | None


def committed(s: Session, po_id: int, *, exclude_case_id: int | None = None) -> Decimal:
    """Reserved + consumed amount on a PO, optionally ignoring one case (the one being evaluated)."""
    q = select(func.coalesce(func.sum(POConsumption.amount), 0)).where(
        POConsumption.po_id == po_id, POConsumption.state.in_(ACTIVE)
    )
    if exclude_case_id is not None:
        q = q.where(POConsumption.case_id != exclude_case_id)
    return Decimal(str(s.scalar(q) or 0))


def committed_qty_by_line(s: Session, po_id: int, *, exclude_case_id: int | None = None) -> dict[int, Decimal]:
    """Quantity already invoiced (reserved or consumed) per PO line by other cases."""
    q = (
        select(POConsumption.po_line_id, func.coalesce(func.sum(POConsumption.qty), 0))
        .where(
            POConsumption.po_id == po_id,
            POConsumption.state.in_(ACTIVE),
            POConsumption.po_line_id.is_not(None),
        )
        .group_by(POConsumption.po_line_id)
    )
    if exclude_case_id is not None:
        q = q.where(POConsumption.case_id != exclude_case_id)
    return {line_id: Decimal(str(qty)) for line_id, qty in s.execute(q).all()}


def release(s: Session, case_id: int) -> Decimal:
    """Release this case's open reservations. Returns the amount released."""
    rows = s.scalars(
        select(POConsumption).where(POConsumption.case_id == case_id, POConsumption.state == "reserved")
    ).all()
    for r in rows:
        r.state = "released"
    return sum((r.amount for r in rows), Decimal(0))


def reserve(s: Session, case_id: int, po_id: int, lines: list[Reservation]) -> Decimal:
    """Replace this case's reservation with `lines` (caller commits). Returns the amount reserved."""
    release(s, case_id)
    for ln in lines:
        s.add(
            POConsumption(
                po_id=po_id,
                po_line_id=ln.po_line_id,
                case_id=case_id,
                amount=ln.amount,
                qty=ln.qty,
                state="reserved",
            )
        )
    return sum((ln.amount for ln in lines), Decimal(0))


def consume(s: Session, case_id: int) -> None:
    """Approval turns the reservation into consumption (caller commits)."""
    for r in s.scalars(
        select(POConsumption).where(POConsumption.case_id == case_id, POConsumption.state == "reserved")
    ).all():
        r.state = "consumed"


def balance(s: Session, po: PurchaseOrder) -> dict[str, Decimal]:
    rows = s.execute(
        select(POConsumption.state, func.coalesce(func.sum(POConsumption.amount), 0))
        .where(POConsumption.po_id == po.id)
        .group_by(POConsumption.state)
    ).all()
    by_state = {state: Decimal(str(v)) for state, v in rows}
    consumed, reserved = by_state.get("consumed", Decimal(0)), by_state.get("reserved", Decimal(0))
    return {
        "amount": po.amount,
        "consumed": consumed,
        "reserved": reserved,
        "remaining": po.amount - consumed - reserved,
    }
