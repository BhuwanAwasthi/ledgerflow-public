"""Master data and admin: vendors (approve / reject after the portal upload), POs with consumption and
amendments, vendor / PO CSV export and import, in-app notifications."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import po_ledger
from app.api.cases import case_out
from app.api.deps import get_orchestrator, get_session, owned_or_404
from app.api.schemas import (
    MasterImportOut,
    NotificationOut,
    POConsumptionOut,
    PODetailOut,
    POOut,
    VendorActionIn,
    VendorActionOut,
    VendorDetailOut,
    VendorOut,
)
from app.master_csv import Entity, MasterCSVError, export_csv, import_csv
from app.models import (
    Case,
    CaseStatus,
    Notification,
    POAmendment,
    POConsumption,
    POLine,
    PurchaseOrder,
    Vendor,
    VendorMemory,
    VendorRequest,
)
from app.orchestrator import Orchestrator
from app.portal import PortalError, approve_vendor, reject_vendor
from app.tenancy import require_owner, scoped

router = APIRouter(tags=["master data"])

SessionDep = Annotated[Session, Depends(get_session)]
OrchDep = Annotated[Orchestrator, Depends(get_orchestrator)]
OPEN = (CaseStatus.received, CaseStatus.processing, CaseStatus.needs_review, CaseStatus.on_hold)


@router.get("/api/vendors", response_model=list[VendorOut])
def list_vendors(s: SessionDep, status: str | None = None) -> list[Vendor]:
    stmt = scoped(select(Vendor), require_owner(s)).order_by(Vendor.code)
    if status:
        stmt = stmt.where(Vendor.status.in_(status.split(",")))
    return list(s.scalars(stmt).all())


@router.get("/api/vendors/{vendor_id}", response_model=VendorDetailOut)
def get_vendor(vendor_id: int, s: SessionDep, orch: OrchDep) -> dict[str, Any]:
    v = owned_or_404(s, Vendor, vendor_id, require_owner(s), "vendor")
    own = v.owner_id
    out = VendorOut.model_validate(v).model_dump()
    out["requests"] = s.scalars(
        scoped(select(VendorRequest).where(VendorRequest.vendor_id == v.id), own).order_by(VendorRequest.id.desc())
    ).all()
    out["memory"] = [m.hint for m in s.scalars(
        scoped(select(VendorMemory).where(VendorMemory.vendor_id == v.id), own).order_by(VendorMemory.id.desc())
    ).all()]
    cases = s.scalars(
        scoped(select(Case).where(Case.vendor_id == v.id, Case.status.in_(OPEN)), own).order_by(Case.id)
    ).all()
    out["open_cases"] = [case_out(s, orch, c) for c in cases]
    return out


@router.post("/api/vendors/{vendor_id}/approve", response_model=VendorActionOut)
async def approve(vendor_id: int, body: VendorActionIn, s: SessionDep, orch: OrchDep) -> dict[str, Any]:
    try:
        return await orch.call(approve_vendor(s, orch, vendor_id, body.actor))  # DB work off the API loop
    except PortalError as e:
        raise HTTPException(e.status, str(e)) from e


@router.post("/api/vendors/{vendor_id}/reject", response_model=VendorActionOut)
def reject(vendor_id: int, body: VendorActionIn, s: SessionDep) -> dict[str, Any]:
    try:
        return reject_vendor(s, vendor_id, body.actor, body.reason)
    except PortalError as e:
        raise HTTPException(e.status, str(e)) from e


def _po_out(s: Session, po: PurchaseOrder) -> dict[str, Any]:
    vendor = s.scalars(scoped(select(Vendor).where(Vendor.id == po.vendor_id), po.owner_id)).first() if po.vendor_id else None
    bal = po_ledger.balance(s, po)
    return {
        "id": po.id, "po_number": po.po_number, "vendor_id": po.vendor_id,
        "vendor_name": vendor.legal_name if vendor else None, "currency": po.currency, "amount": po.amount,
        "consumed": bal["consumed"], "reserved": bal["reserved"], "remaining": bal["remaining"],
        "status": po.status, "created_at": po.created_at, "approved_by": po.approved_by, "notes": po.notes,
    }


@router.get("/api/pos", response_model=list[POOut])
def list_pos(s: SessionDep, vendor: int | None = None) -> list[dict[str, Any]]:
    stmt = scoped(select(PurchaseOrder), require_owner(s)).order_by(PurchaseOrder.po_number)
    if vendor is not None:
        stmt = stmt.where(PurchaseOrder.vendor_id == vendor)
    return [_po_out(s, po) for po in s.scalars(stmt).all()]


@router.get("/api/pos/{po_id}", response_model=PODetailOut)
def get_po(po_id: int, s: SessionDep) -> dict[str, Any]:
    po = owned_or_404(s, PurchaseOrder, po_id, require_owner(s), "PO")
    own = po.owner_id
    out = _po_out(s, po)
    out["lines"] = s.scalars(scoped(select(POLine).where(POLine.po_id == po.id), own).order_by(POLine.line_no)).all()
    rows = []
    consumption = s.scalars(
        scoped(select(POConsumption).where(POConsumption.po_id == po.id), own).order_by(POConsumption.id)
    ).all()
    for c in consumption:
        row = POConsumptionOut.model_validate(c).model_dump()
        case = s.scalars(scoped(select(Case).where(Case.id == c.case_id), own)).first()
        if case is not None:
            parent = (
                s.scalars(scoped(select(Case).where(Case.id == case.parent_case_id), own)).first()
                if case.parent_case_id else None
            )
            row.update(case_ref=case.ref, case_status=case.status, invoice_number=case.invoice_number,
                       split_from=parent.ref if parent else None)
        rows.append(row)
    out["consumption"] = rows
    out["amendments"] = s.scalars(
        scoped(select(POAmendment).where(POAmendment.po_id == po.id), own).order_by(POAmendment.id)
    ).all()
    return out


@router.get("/api/master/{entity}.csv")
def export_master(entity: Entity, s: SessionDep) -> Response:
    """Vendors, purchase orders or PO lines in the data/master CSV format (re-importable)."""
    return Response(
        export_csv(s, entity), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{entity}.csv"'},
    )


@router.post("/api/master/{entity}/import", response_model=MasterImportOut)
async def import_master(
    entity: Entity,
    s: SessionDep,
    file: Annotated[UploadFile, File(description="CSV in the export's format (header row required)")],
    actor: Annotated[str, Form()] = "admin",
) -> dict[str, Any]:
    """Upsert from CSV, all or nothing: any invalid row rejects the file (422 with every row error). Import
    vendors, then purchase orders, then PO lines. A changed PO amount is logged as a PO amendment."""
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise HTTPException(422, {"errors": ["the file is not UTF-8 text"]}) from e

    def work() -> dict[str, Any]:
        try:
            rep = import_csv(s, entity, text, actor)
        except MasterCSVError as e:
            s.rollback()
            raise HTTPException(422, {"errors": e.errors}) from e
        s.commit()
        return {"entity": rep.entity, "created": rep.created, "updated": rep.updated,
                "unchanged": rep.unchanged, "notes": rep.notes}

    return await run_in_threadpool(work)


@router.get("/api/notifications", response_model=list[NotificationOut])
def notifications(s: SessionDep, audience: str | None = None, case_id: int | None = None, limit: int = 50) -> list[Notification]:
    stmt = scoped(select(Notification), require_owner(s)).order_by(Notification.id.desc()).limit(min(max(limit, 1), 200))
    if audience:
        stmt = stmt.where(Notification.audience == audience)
    if case_id is not None:
        stmt = stmt.where(Notification.case_id == case_id)
    return list(s.scalars(stmt).all())


@router.post("/api/notifications/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: int, s: SessionDep) -> Notification:
    n = owned_or_404(s, Notification, notification_id, require_owner(s), "notification")
    n.read = True
    s.commit()
    return n
