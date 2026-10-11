"""Master data and admin: vendors (approve / reject after the portal upload), POs with consumption,
in-app notifications."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import po_ledger
from app.api.cases import case_out
from app.api.deps import get_orchestrator, get_session
from app.api.schemas import (
    NotificationOut,
    PODetailOut,
    POOut,
    VendorActionIn,
    VendorActionOut,
    VendorDetailOut,
    VendorOut,
)
from app.models import (
    Case,
    CaseStatus,
    Notification,
    POConsumption,
    POLine,
    PurchaseOrder,
    Vendor,
    VendorMemory,
    VendorRequest,
)
from app.orchestrator import Orchestrator
from app.portal import PortalError, approve_vendor, reject_vendor

router = APIRouter(tags=["master data"])

SessionDep = Annotated[Session, Depends(get_session)]
OrchDep = Annotated[Orchestrator, Depends(get_orchestrator)]
OPEN = (CaseStatus.received, CaseStatus.processing, CaseStatus.needs_review, CaseStatus.on_hold)


@router.get("/api/vendors", response_model=list[VendorOut])
async def list_vendors(s: SessionDep, status: str | None = None) -> list[Vendor]:
    stmt = select(Vendor).order_by(Vendor.code)
    if status:
        stmt = stmt.where(Vendor.status.in_(status.split(",")))
    return list(s.scalars(stmt).all())


@router.get("/api/vendors/{vendor_id}", response_model=VendorDetailOut)
async def get_vendor(vendor_id: int, s: SessionDep, orch: OrchDep) -> dict[str, Any]:
    v = s.get(Vendor, vendor_id)
    if v is None:
        raise HTTPException(404, f"vendor {vendor_id} not found")
    out = VendorOut.model_validate(v).model_dump()
    out["requests"] = s.scalars(select(VendorRequest).where(VendorRequest.vendor_id == v.id).order_by(VendorRequest.id.desc())).all()
    out["memory"] = [m.hint for m in s.scalars(select(VendorMemory).where(VendorMemory.vendor_id == v.id).order_by(VendorMemory.id.desc())).all()]
    cases = s.scalars(select(Case).where(Case.vendor_id == v.id, Case.status.in_(OPEN)).order_by(Case.id)).all()
    out["open_cases"] = [case_out(s, orch, c) for c in cases]
    return out


@router.post("/api/vendors/{vendor_id}/approve", response_model=VendorActionOut)
async def approve(vendor_id: int, body: VendorActionIn, s: SessionDep, orch: OrchDep) -> dict[str, Any]:
    try:
        return await approve_vendor(s, orch, vendor_id, body.actor)
    except PortalError as e:
        raise HTTPException(e.status, str(e)) from e


@router.post("/api/vendors/{vendor_id}/reject", response_model=VendorActionOut)
async def reject(vendor_id: int, body: VendorActionIn, s: SessionDep) -> dict[str, Any]:
    try:
        return reject_vendor(s, vendor_id, body.actor, body.reason)
    except PortalError as e:
        raise HTTPException(e.status, str(e)) from e


def _po_out(s: Session, po: PurchaseOrder) -> dict[str, Any]:
    vendor = s.get(Vendor, po.vendor_id) if po.vendor_id else None
    bal = po_ledger.balance(s, po)
    return {
        "id": po.id, "po_number": po.po_number, "vendor_id": po.vendor_id,
        "vendor_name": vendor.legal_name if vendor else None, "currency": po.currency, "amount": po.amount,
        "consumed": bal["consumed"], "reserved": bal["reserved"], "remaining": bal["remaining"],
        "status": po.status, "created_at": po.created_at, "approved_by": po.approved_by, "notes": po.notes,
    }


@router.get("/api/pos", response_model=list[POOut])
async def list_pos(s: SessionDep, vendor: int | None = None) -> list[dict[str, Any]]:
    stmt = select(PurchaseOrder).order_by(PurchaseOrder.po_number)
    if vendor is not None:
        stmt = stmt.where(PurchaseOrder.vendor_id == vendor)
    return [_po_out(s, po) for po in s.scalars(stmt).all()]


@router.get("/api/pos/{po_id}", response_model=PODetailOut)
async def get_po(po_id: int, s: SessionDep) -> dict[str, Any]:
    po = s.get(PurchaseOrder, po_id)
    if po is None:
        raise HTTPException(404, f"PO {po_id} not found")
    out = _po_out(s, po)
    out["lines"] = s.scalars(select(POLine).where(POLine.po_id == po.id).order_by(POLine.line_no)).all()
    out["consumption"] = s.scalars(select(POConsumption).where(POConsumption.po_id == po.id).order_by(POConsumption.id)).all()
    return out


@router.get("/api/notifications", response_model=list[NotificationOut])
async def notifications(s: SessionDep, audience: str | None = None, case_id: int | None = None, limit: int = 50) -> list[Notification]:
    stmt = select(Notification).order_by(Notification.id.desc()).limit(min(max(limit, 1), 200))
    if audience:
        stmt = stmt.where(Notification.audience == audience)
    if case_id is not None:
        stmt = stmt.where(Notification.case_id == case_id)
    return list(s.scalars(stmt).all())


@router.post("/api/notifications/{notification_id}/read", response_model=NotificationOut)
async def mark_read(notification_id: int, s: SessionDep) -> Notification:
    n = s.get(Notification, notification_id)
    if n is None:
        raise HTTPException(404, f"notification {notification_id} not found")
    n.read = True
    s.commit()
    return n
