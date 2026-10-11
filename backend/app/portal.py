"""Vendor portal and vendor admin (no login; the token in the link is the credential).

Flow: upload on /portal/{token} -> `vendor_docs_received`: store + render each file, classify it with the
classifier agent, run code verification checks (name on the document, tax-ID format, bank account digits)
-> when every needed document is in, the request is `submitted` and the AP team is notified -> an admin
approves the vendor -> the waiting case resumes (`fire_event` with the paused stage's `wait_for`).
An info request that only needs a corrected invoice resumes the case at once (nothing for an admin to check).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any

from rapidfuzz import fuzz
from rapidfuzz.utils import default_process
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import pdf
from app.agents.base import AgentContext
from app.agents.classifier import ClassifierAgent
from app.agents.vendor import iban_hash
from app.case_state import effective, fold_state
from app.cases import UploadedFile
from app.config_loader import ResolvedConfig
from app.documents import page_texts
from app.models import Case, CaseStatus, Document, Notification, ReviewActionRow, Vendor, VendorRequest
from app.orchestrator import Orchestrator


def admin_checked(cfg: ResolvedConfig) -> set[str]:
    """Needs that wait for an admin's vendor approval (vendor.params.admin_verified_docs)."""
    return set(cfg.agents["vendor"].params.get("admin_verified_docs") or [])


class PortalError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def get_request(s: Session, token: str) -> VendorRequest:
    req = s.scalars(select(VendorRequest).where(VendorRequest.token == token)).first()
    if req is None:
        raise PortalError(404, "This upload link is not valid.")
    return req


def request_view(s: Session, req: VendorRequest, cfg: ResolvedConfig | None = None) -> dict[str, Any]:
    vendor = s.get(Vendor, req.vendor_id) if req.vendor_id else None
    case = s.get(Case, req.case_id) if req.case_id else None
    labels = {lbl.id: lbl.description for lbl in cfg.document_types.labels} if cfg else {}
    need_text = (cfg.agents["comms"].params.get("need_text") or {}) if cfg else {}
    return {
        "token": req.token,
        "kind": req.kind,
        "status": req.status,
        "vendor_name": vendor.legal_name if vendor else req.proposed_name,
        "case_ref": case.ref if case else None,
        "invoice_number": case.invoice_number if case else None,
        "docs_needed": [
            {"id": d, "label": need_text.get(d) or labels.get(d) or d, "received": d in (req.docs_received or [])}
            for d in req.docs_needed or []
        ],
        "docs_received": list(req.docs_received or []),
        "checks": list(req.checks or []),
        "message": req.message,
        "open": req.status in ("open", "submitted"),
    }


def _ingest(storage: Any, cfg: ResolvedConfig, doc: Document, data: bytes) -> None:
    """Convert (image/DOCX -> PDF), render pages and store the text layer, like the intake agent."""
    kind = pdf.sniff(data, doc.filename)
    if kind is None:
        raise PortalError(415, f"{doc.filename} is not a PDF, image or Word file.")
    if kind != "pdf":
        data = pdf.image_to_pdf(data) if kind == "image" else pdf.docx_to_pdf(data)
        doc.storage_key = storage.put(doc.storage_key.rsplit(".", 1)[0] + ".pdf", data, "application/pdf")
    doc.mime = "application/pdf"
    limits = cfg.app.limits
    rendered = pdf.render(data, limits.render_dpi, limits.max_pages_per_document)
    if rendered.pages > limits.max_pages_per_document:
        raise PortalError(413, f"{doc.filename} has {rendered.pages} pages; the limit is {limits.max_pages_per_document}.")
    for i, png in enumerate(rendered.pngs, start=1):
        storage.put(pdf.page_png_key(doc.sha256, i), png, "image/png")
    storage.put(pdf.text_key(doc.sha256), pdf.dump_texts(rendered.texts), "application/json")
    doc.pages = rendered.pages
    min_text = int(cfg.agents["intake"].params.get("min_text_chars", 20))
    doc.has_text_layer = sum(len(t.strip()) for t in rendered.texts) >= min_text


def _digits(v: str | None) -> str:
    return re.sub(r"[^0-9]", "", v or "")


def verify(doc: Document, label: str, text: str, vendor: Vendor | None, expected_last4: str | None,
           params: dict[str, Any]) -> list[dict[str, Any]]:
    """Code checks on an uploaded onboarding document. A scan without a text layer is left for the admin."""
    base = {"document_id": doc.id, "filename": doc.filename, "label": label}
    if not doc.has_text_layer:
        return [{**base, "check": "readable", "status": "manual", "detail": "Scanned document: check it by eye."}]
    out: list[dict[str, Any]] = []
    min_score = float(params.get("document_name_min_score", 85))
    if vendor is not None and (label == "tax_form" or params.get("verify_bank_letter_name", True)):
        score = fuzz.partial_ratio(vendor.legal_name, text, processor=default_process)
        out.append({**base, "check": "name", "status": "pass" if score >= min_score else "fail",
                    "detail": f"'{vendor.legal_name}' found on the document (similarity {score:.0f}, needs {min_score:.0f})."})
    digits = _digits(text)
    if label == "tax_form" and vendor is not None and vendor.tax_id:
        fmt = (params.get("tax_id_formats") or {}).get(vendor.country or "US")
        ok_format = bool(re.match(fmt, vendor.tax_id)) if fmt else True
        on_doc = _digits(vendor.tax_id) in digits
        out.append({**base, "check": "tax_id", "status": "pass" if ok_format and on_doc else "fail",
                    "detail": f"Tax ID {vendor.tax_id} " + ("is on the form" if on_doc else "is not on the form")
                    + ("" if ok_format else " and does not match the expected format") + "."})
    if label == "bank_letter" and expected_last4:
        found = bool(re.search(rf"{re.escape(expected_last4)}(?!\d)", text))
        out.append({**base, "check": "account", "status": "pass" if found else "fail",
                    "detail": f"Account ending {expected_last4} " + ("confirmed by the letter." if found else "not found in the letter.")})
    return out


async def vendor_docs_received(
    s: Session, orch: Orchestrator, storage: Any, token: str, files: list[UploadedFile]
) -> dict[str, Any]:
    req = get_request(s, token)
    if req.status not in ("open", "submitted"):
        raise PortalError(409, f"This request is {req.status}; no more uploads are needed.")
    if not files:
        raise PortalError(422, "Choose at least one file.")
    case = s.get(Case, req.case_id) if req.case_id else None
    if case is None:
        raise PortalError(409, "This request is not linked to an invoice.")
    cfg = orch.case_config(s, case)
    vendor = s.get(Vendor, req.vendor_id) if req.vendor_id else None
    state = effective(fold_state(s, case.id))
    invoice_last4 = ((state.get("normaliser") or {}).get("invoice") or {}).get("remit_bank", {}) or {}
    expected_last4 = invoice_last4.get("account_last4") or (vendor.bank_last4 if vendor else None)

    agent_cfg = cfg.agents["classifier"]
    classifier = ClassifierAgent(agent_cfg)
    ctx = AgentContext(case_id=case.id, case_ref=case.ref, stage_id="portal", run_no=0, config=cfg,
                       agent_config=agent_cfg, state={}, session_factory=orch.session_factory,
                       llm=orch.llm, storage=storage)
    params = cfg.agents["vendor"].params
    max_bytes = int(cfg.app.limits.max_upload_mb * 1024 * 1024)
    routes = {lbl.id: lbl.route for lbl in cfg.document_types.labels}
    received = list(req.docs_received or [])
    checks = list(req.checks or [])
    uploaded: list[dict[str, Any]] = []
    for f in files:
        if len(f.data) > max_bytes:
            raise PortalError(413, f"{f.filename} is larger than {cfg.app.limits.max_upload_mb:g} MB.")
        sha = hashlib.sha256(f.data).hexdigest()
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", f.filename).strip("._") or "file"
        key = storage.put(f"cases/{case.id}/portal/{sha[:12]}_{safe}", f.data, f.mime)
        doc = Document(case_id=case.id, filename=f.filename, mime=f.mime, sha256=sha, storage_key=key)
        s.add(doc)
        s.flush()
        await asyncio.to_thread(_ingest, storage, cfg, doc, f.data)
        entries, _ = await classifier.classify_document(ctx, doc)
        top = entries[0] if entries else None
        min_conf = cfg.document_types.min_confidence
        label = top.label if top and top.confidence >= min_conf and top.label in routes else "other"
        doc.label, doc.label_confidence = label, top.confidence if top else 0.0
        doc.page_from, doc.page_to = 1, doc.pages
        text = "\n".join(await asyncio.to_thread(page_texts, storage, doc))
        doc_checks = verify(doc, label, text, vendor, expected_last4, params) if label in admin_checked(cfg) else []
        checks += doc_checks
        if label in (req.docs_needed or []) and label not in received:
            received.append(label)
        uploaded.append({"document_id": doc.id, "filename": f.filename, "label": label,
                         "confidence": doc.label_confidence, "checks": doc_checks})

    req.docs_received = received
    req.checks = checks
    missing = [d for d in req.docs_needed or [] if d not in received]
    resumed = False
    if not missing:
        req.status = "submitted"
        who = vendor.legal_name if vendor else "the vendor"
        if set(req.docs_needed or []) & admin_checked(cfg):
            failed = [c for c in checks if c["status"] != "pass"]
            s.add(Notification(audience="reviewer", case_id=case.id, text=(
                f"{case.ref}: {who} uploaded {', '.join(received)}. "
                + (f"{len(failed)} check(s) need a look. " if failed else "All checks passed. ")
                + "Approve the vendor to resume the invoice.")))
        else:
            req.status = "approved"
            s.add(Notification(audience="reviewer", case_id=case.id,
                               text=f"{case.ref}: {who} sent a corrected invoice; processing resumed."))
            resumed = True
    s.commit()
    if resumed:
        resumed = await resume_case(s, orch, case.id)
    out = request_view(s, req, cfg)
    out.update(uploaded=uploaded, missing=missing, resumed=resumed)
    return out


def waiting_event(s: Session, orch: Orchestrator, case_id: int) -> str | None:
    """The event an on-hold case's paused stage waits for (workflow.yaml `wait_for`)."""
    case = s.get(Case, case_id)
    if case is None or case.status != CaseStatus.on_hold or not case.current_stage:
        return None
    return orch.case_config(s, case).workflow.stage(case.current_stage).wait_for


async def resume_case(s: Session, orch: Orchestrator, case_id: int) -> bool:
    """Fire the event the case's paused stage waits for (from workflow.yaml), if it is waiting."""
    event = waiting_event(s, orch, case_id)
    if not event:
        return False
    ok = await orch.fire_event(case_id, event)
    s.expire_all()
    return ok


async def approve_vendor(s: Session, orch: Orchestrator, vendor_id: int, actor: str) -> dict[str, Any]:
    """Admin approval: the vendor becomes approved, verified bank details from a bank-change request are
    written to the master, and the cases those requests (or the vendor's onboarding) paused resume."""
    vendor = s.get(Vendor, vendor_id)
    if vendor is None:
        raise PortalError(404, f"vendor {vendor_id} not found")
    reqs = s.scalars(
        select(VendorRequest).where(VendorRequest.vendor_id == vendor_id, VendorRequest.status.in_(["open", "submitted"]))
    ).all()
    vendor.status = "approved"
    notes: list[str] = []
    case_ids: list[tuple[int, str]] = []
    for req in reqs:
        if req.kind == "info" and req.status != "submitted":
            continue  # the vendor has not sent what this hold asks for yet: leave that case paused
        if req.kind == "info" and "bank_letter" in (req.docs_received or []) and req.case_id:
            bank = ((effective(fold_state(s, req.case_id)).get("normaliser") or {}).get("invoice") or {}).get("remit_bank") or {}
            if bank.get("account_last4"):
                note = f"bank account changed from ...{vendor.bank_last4} to ...{bank['account_last4']}"
                notes.append(note)
                failed = [c["check"] for c in req.checks or [] if c.get("status") != "pass"]
                s.add(ReviewActionRow(  # audit trail for a payment-detail change (fraud control)
                    case_id=req.case_id, actor=actor, action="override",
                    text=f"{vendor.code} {note} after a bank letter"
                    + (f" (checks not passed: {', '.join(failed)})" if failed else ""),
                    changeset={"op": "vendor_bank_change", "vendor_id": vendor.id, "old_last4": vendor.bank_last4,
                               "new_last4": bank["account_last4"], "request_id": req.id},
                ))
                vendor.bank_last4 = bank["account_last4"]
                vendor.iban_hash = iban_hash(bank.get("iban")) or vendor.iban_hash
        req.status = "approved"
        if req.case_id:
            case_ids.append((req.case_id, req.kind))
        s.add(Notification(audience="submitter", case_id=req.case_id,
                           text=f"{vendor.legal_name}: your documents were verified by {actor}."))
    s.commit()
    # Resume the cases these requests belong to, plus other invoices of this vendor paused on the same
    # onboarding event (they share the vendor's one onboarding request). Cases held for other reasons stay.
    events = {e for cid, kind in case_ids if kind == "onboarding" if (e := waiting_event(s, orch, cid))}
    on_hold = s.scalars(select(Case.id).where(Case.vendor_id == vendor_id, Case.status == CaseStatus.on_hold)).all()
    others = [c for c in on_hold if events and waiting_event(s, orch, c) in events]
    targets = dict.fromkeys([*(cid for cid, _ in case_ids), *others])
    resumed = [cid for cid in targets if await resume_case(s, orch, cid)]
    return {"vendor_id": vendor.id, "status": vendor.status, "notes": notes, "resumed_case_ids": resumed}


def reject_vendor(s: Session, vendor_id: int, actor: str, reason: str | None) -> dict[str, Any]:
    vendor = s.get(Vendor, vendor_id)
    if vendor is None:
        raise PortalError(404, f"vendor {vendor_id} not found")
    vendor.status = "rejected"
    for req in s.scalars(
        select(VendorRequest).where(VendorRequest.vendor_id == vendor_id, VendorRequest.status.in_(["open", "submitted"]))
    ).all():
        req.status = "rejected"
        s.add(Notification(audience="reviewer", case_id=req.case_id,
                           text=f"{vendor.legal_name} rejected by {actor}" + (f": {reason}" if reason else ".")))
    s.commit()
    return {"vendor_id": vendor.id, "status": vendor.status, "notes": [], "resumed_case_ids": []}
