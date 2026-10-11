"""Vendor verification (hybrid, code today): match the invoice to the vendor master and compare bank details;
open a light onboarding request (documents needed + a no-login portal token) for an unknown vendor.

Match order (SPEC section 5): tax ID exact, then legal name / alias fuzzy (rapidfuzz token_set_ratio >=
params.name_match_threshold), then email domain (vendor email on the invoice, else the submitter's).
"""

from __future__ import annotations

import hashlib
import re
import secrets
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.base import Agent, AgentContext, AgentResult
from app.models import Case, Vendor, VendorRequest


def iban_hash(iban: str | None) -> str | None:
    """sha256 of the upper-case alphanumeric IBAN (the master stores only the hash)."""
    if not iban:
        return None
    clean = re.sub(r"[^A-Z0-9]", "", iban.upper())
    return hashlib.sha256(clean.encode()).hexdigest() if clean else None


def _digits(v: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", v or "").upper()


def _domain(email: str | None) -> str | None:
    return email.rsplit("@", 1)[1].strip().lower() if email and "@" in email else None


def match_vendor(
    s: Session, invoice: dict[str, Any], submitter_email: str | None, threshold: float
) -> tuple[Vendor | None, str | None, float | None]:
    """(vendor, method, score). Every master vendor is a candidate, whatever its status."""
    vendors = s.scalars(select(Vendor).order_by(Vendor.id)).all()
    tax = _digits(invoice.get("vendor_tax_id"))
    if tax:
        for v in vendors:
            if v.tax_id and _digits(v.tax_id) == tax:
                return v, "tax_id", 100.0
    name = (invoice.get("vendor_name") or "").strip()
    if name:
        best: tuple[float, Vendor | None] = (0.0, None)
        for v in vendors:
            for candidate in [v.legal_name, *(v.aliases or [])]:
                score = fuzz.token_set_ratio(name.lower(), candidate.lower())
                if score > best[0]:
                    best = (score, v)
        if best[1] is not None and best[0] >= threshold:
            return best[1], "name", round(best[0], 1)
    for method, domain in (("email_domain", _domain(invoice.get("vendor_email"))), ("submitter_domain", _domain(submitter_email))):
        if domain:
            v = s.scalars(select(Vendor).where(func.lower(Vendor.email_domain) == domain)).first()
            if v is not None:
                return v, method, None
    return None, None, None


def vendor_view(v: Vendor) -> dict[str, Any]:
    return {
        "id": v.id,
        "code": v.code,
        "legal_name": v.legal_name,
        "status": v.status,
        "category": v.category,
        "default_gl": v.default_gl,
        "bank_last4": v.bank_last4,
        "non_po_allowed": v.non_po_allowed,
    }


class VendorAgent(Agent):
    async def run(self, action: str, ctx: AgentContext) -> AgentResult:
        if action == "request_onboarding":
            return self._request_onboarding(ctx)
        return self._check(ctx)

    # ---------------- check
    def _check(self, ctx: AgentContext) -> AgentResult:
        inv: dict[str, Any] = ctx.state.get("normaliser", {}).get("invoice") or {}
        bank = inv.get("remit_bank") or {}
        inv_last4 = (bank.get("account_last4") or "").strip() or None
        inv_iban = iban_hash(bank.get("iban"))
        with ctx.session_factory() as s:
            case = s.get(Case, ctx.case_id)
            assert case is not None
            vendor, method, score = match_vendor(
                s, inv, case.submitter_email, float(ctx.params["name_match_threshold"])
            )
            case.vendor_id = vendor.id if vendor else None
            s.commit()
            data: dict[str, Any] = {
                "vendor_id": vendor.id if vendor else None,
                "vendor": vendor_view(vendor) if vendor else None,
                "match": {"method": method, "score": score},
                "bank": {"invoice_last4": inv_last4, "master_last4": vendor.bank_last4 if vendor else None, "match": None},
                "printed_name": inv.get("vendor_name"),
            }
            if vendor is None:
                return AgentResult(
                    "new_vendor",
                    data,
                    [
                        (
                            f"'{inv.get('vendor_name') or 'Unnamed vendor'}' is not in the vendor master "
                            "(no tax ID, name or email-domain match)."
                        ),
                        "A light onboarding request will be opened.",
                    ],
                    confidence=1.0,
                )
            how = {
                "tax_id": f"tax ID {vendor.tax_id}",
                "name": f"name (similarity {score})",
                "email_domain": f"email domain {vendor.email_domain}",
                "submitter_domain": f"submitter email domain {vendor.email_domain}",
            }[method or "name"]
            reasoning = [f"Matched {vendor.legal_name} ({vendor.code}) on {how}; master status {vendor.status}."]

            iban_changed = bool(inv_iban and vendor.iban_hash and inv_iban != vendor.iban_hash)
            last4_changed = bool(inv_last4 and vendor.bank_last4 and inv_last4 != vendor.bank_last4)
            if inv_last4 or inv_iban:
                data["bank"]["match"] = not (iban_changed or last4_changed)
            if vendor.status in ("pending_docs",):
                outcome = "new_vendor"
                reasoning.append("The vendor's onboarding is not finished (documents pending).")
            elif vendor.status != "approved":
                outcome = "inactive_vendor"
                reasoning.append(f"The vendor is {vendor.status}; the invoice will be held.")
            elif iban_changed or last4_changed:
                outcome = "bank_mismatch"
                reasoning.append(
                    f"Bank account on the invoice ends {inv_last4 or '?'} but the master has "
                    f"{vendor.bank_last4 or '?'}: possible payment-fraud pattern, analysis continues."
                )
            else:
                outcome = "approved_vendor"
                reasoning.append(
                    f"Bank account ending {inv_last4} matches the master."
                    if inv_last4
                    else "No bank details printed on the invoice."
                )
            return AgentResult(outcome, data, reasoning, confidence=1.0 if method == "tax_id" else 0.9)

    # ---------------- request_onboarding
    def _request_onboarding(self, ctx: AgentContext) -> AgentResult:
        inv: dict[str, Any] = ctx.state.get("normaliser", {}).get("invoice") or {}
        bank = inv.get("remit_bank") or {}
        docs = list(ctx.params["docs_required_new_vendor"])
        with ctx.session_factory() as s:
            case = s.get(Case, ctx.case_id)
            assert case is not None
            vendor = s.get(Vendor, case.vendor_id) if case.vendor_id else None
            created = False
            if vendor is None:
                vendor = Vendor(
                    code=self._next_code(s),
                    legal_name=inv.get("vendor_name") or "Unknown vendor",
                    tax_id=inv.get("vendor_tax_id"),
                    email_domain=_domain(inv.get("vendor_email")) or _domain(case.submitter_email),
                    bank_last4=(bank.get("account_last4") or None),
                    iban_hash=iban_hash(bank.get("iban")),
                    status="pending_docs",
                )
                s.add(vendor)
                s.flush()
                created = True
                case.vendor_id = vendor.id
            req = s.scalars(
                select(VendorRequest).where(
                    VendorRequest.vendor_id == vendor.id,
                    VendorRequest.kind == "onboarding",
                    VendorRequest.status.in_(["open", "submitted"]),
                )
            ).first()
            reused = req is not None
            if req is None:
                req = VendorRequest(
                    vendor_id=vendor.id,
                    proposed_name=vendor.legal_name,
                    token=secrets.token_urlsafe(16),
                    docs_needed=docs,
                    docs_received=[],
                    status="open",
                    case_id=case.id,
                    kind="onboarding",
                )
                s.add(req)
            s.commit()
            data = {
                "vendor_id": vendor.id,
                "vendor": vendor_view(vendor),
                "request": {
                    "id": req.id,
                    "token": req.token,
                    "portal_path": f"/portal/{req.token}",
                    "docs_needed": list(req.docs_needed),
                },
            }
            reasoning = [
                (f"Created vendor {vendor.code} '{vendor.legal_name}' with status pending_docs." if created
                 else f"Vendor {vendor.code} '{vendor.legal_name}' is pending documents."),
                ("Reusing the open onboarding request." if reused else "Opened an onboarding request")
                + f" asking for: {', '.join(req.docs_needed)}.",
                "The vendor gets a no-login upload link; the case resumes once an admin approves the vendor.",
            ]
            return AgentResult("requested", data, reasoning, confidence=1.0)

    @staticmethod
    def _next_code(s: Session) -> str:
        codes = s.scalars(select(Vendor.code)).all()
        nums = [int(m.group(1)) for c in codes if (m := re.fullmatch(r"V(\d+)", c))]
        return f"V{(max(nums, default=0) + 1):03d}"
