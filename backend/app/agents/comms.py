"""Communications (hybrid). No email API: drafts (subject + body) with a `mailto:` link, a no-login vendor
portal upload link, and in-app notifications.

- `draft_request`: onboarding documents for a new vendor (the vendor request's docs_needed), or what a held
  invoice needs (params.request_needs per failed rule). Opens (or reuses) a vendor_request so the portal
  upload can resume the case.
- `draft_rejection`: a neutral message with the reasons.
- `notify`: approval notifications.
The LLM writes the wording only; who gets what, the needs list and the links are code. If the model call
fails, a plain deterministic draft is used so the case still pauses with something to send.
"""

from __future__ import annotations

import json
import os
import secrets
from typing import Any
from urllib.parse import quote

from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import Agent, AgentContext, AgentResult
from app.agents.po_matching import llm_context
from app.case_state import effective
from app.llm import LLMError, sum_metrics, text_part
from app.llm_schemas import CommsDraft
from app.models import Case, Notification, ReviewActionRow, Vendor, VendorRequest
from app.settings import get_settings

_JINJA = SandboxedEnvironment(autoescape=False)


REVIEWER_PREFIX = "Reviewer: "


def mailto(to: str | None, subject: str, body: str) -> str:
    return f"mailto:{quote(to or '')}?subject={quote(subject)}&body={quote(body)}"


def portal_link(params: dict[str, Any], token: str) -> str:
    base = os.environ.get(params.get("portal_base_url_env") or "", "") or get_settings().public_app_url
    return f"{base.rstrip('/')}/portal/{token}"


def _humanise(field: str) -> str:
    return field.replace("_", " ")


class CommsAgent(Agent):
    async def run(self, action: str, ctx: AgentContext) -> AgentResult:
        if action == "notify":
            return self._notify(ctx)
        return await self._draft(action, ctx)

    # ---------------- helpers
    def _note(self, s: Session, ctx: AgentContext, key: str, audience: str, case: Case, **kw: Any) -> None:
        tpl = (ctx.params.get("notifications") or {}).get(key)
        if not tpl:
            return
        text = _JINJA.from_string(tpl).render(ref=case.ref, invoice_number=case.invoice_number, **kw)
        s.add(Notification(audience=audience, case_id=case.id, text=text.strip()))

    def _notify(self, ctx: AgentContext) -> AgentResult:
        with ctx.session_factory() as s:
            case = s.get(Case, ctx.case_id)
            assert case is not None
            vendor = s.get(Vendor, case.vendor_id) if case.vendor_id else None
            name = vendor.legal_name if vendor else "the vendor"
            self._note(s, ctx, "approved_submitter", "submitter", case, vendor=name, summary="")
            self._note(s, ctx, "approved_reviewer", "reviewer", case, vendor=name, summary="")
            s.commit()
        return AgentResult("ok", {"notified": ["submitter", "reviewer"]},
                           ["Notified the submitter and the AP team that the invoice is approved."], confidence=1.0)

    def _reasons(self, s: Session, case: Case, state: dict[str, Any], severities: set[str] | None) -> list[str]:
        results = (state.get("policy") or {}).get("results") or []
        reasons = [r["explanation"] for r in results if r.get("status") == "fail" and (severities is None or r.get("severity") in severities)]
        last = s.scalars(
            select(ReviewActionRow).where(ReviewActionRow.case_id == case.id).order_by(ReviewActionRow.id.desc())
        ).first()
        if last is not None and last.action in ("request_info", "reject") and last.text:
            reasons.insert(0, f"{REVIEWER_PREFIX}{last.text}")
        return reasons

    def _needs(self, ctx: AgentContext, state: dict[str, Any]) -> tuple[list[str], list[str]]:
        """(need ids, need sentences) for a held invoice."""
        p = ctx.params
        results = (state.get("policy") or {}).get("results") or []
        ids: list[str] = []
        missing: list[str] = []
        for r in results:
            if r.get("status") != "fail":
                continue
            for n in (p.get("request_needs") or {}).get(r["rule_id"], []):
                if n not in ids:
                    ids.append(n)
            missing += (r.get("evidence") or {}).get("missing") or []
        ids = ids or list(p.get("default_needs") or [])
        texts = []
        for n in ids:
            t = (p.get("need_text") or {}).get(n, n.replace("_", " "))
            if n == "invoice" and missing and p.get("missing_fields_suffix"):
                t += _JINJA.from_string(p["missing_fields_suffix"]).render(fields=[_humanise(f) for f in missing])
            texts.append(t)
        return ids, texts

    # ---------------- drafts
    async def _draft(self, action: str, ctx: AgentContext) -> AgentResult:
        p = ctx.params
        state = effective(ctx.state)
        inv: dict[str, Any] = (state.get("normaliser") or {}).get("invoice") or {}
        rejection = action == "draft_rejection"
        with ctx.session_factory() as s:
            case = s.get(Case, ctx.case_id)
            assert case is not None
            vendor = s.get(Vendor, case.vendor_id) if case.vendor_id else None
            name = vendor.legal_name if vendor else (inv.get("vendor_name") or case.submitter_name or "Supplier")
            req: VendorRequest | None = None
            need_ids: list[str] = []
            needs: list[str] = []
            if rejection:
                reasons = self._reasons(s, case, state, {"reject"}) or self._reasons(s, case, state, None)
                kind = "rejection"
            else:
                onboarding = (state.get("vendor") or {}).get("request")
                if onboarding and vendor is not None and vendor.status == "pending_docs":
                    kind = "onboarding"
                    req = s.get(VendorRequest, onboarding["id"])
                    need_ids = list(req.docs_needed) if req else list(onboarding["docs_needed"])
                    needs = [(p.get("need_text") or {}).get(n, n) for n in need_ids]
                    reasons = [f"{name} is not yet set up as a supplier in our vendor master."]
                else:
                    kind = "info"
                    need_ids, needs = self._needs(ctx, state)
                    reasons = self._reasons(s, case, state, None)
                    req = self._info_request(s, case, need_ids)
            link = portal_link(p, req.token) if req and p.get("include_portal_link") and ctx.config.app.features.get("vendor_portal") else None

            prompt_vars = {
                "signature": p.get("signature", ""),
                "recipient_name": name,
                "recipient_role": "vendor" if vendor else "submitter",
                "invoice_number": inv.get("invoice_number"),
                "currency": inv.get("currency") or case.currency or "",
                "total": inv.get("total") or case.total or "",
                "reasons_json": json.dumps(reasons, indent=1),
                "needs": needs,
                "portal_link": link,
            }
            await ctx.emit("drafting the message")
            metrics: dict[str, Any] = {}
            note: str | None = None
            try:
                out, call = await self.call_llm(ctx, action, CommsDraft, [text_part("Write the email now.")],
                                                prompt_vars=prompt_vars, context=llm_context(ctx, s))
                assert isinstance(out, CommsDraft)
                subject, body = out.subject, out.body
                metrics = sum_metrics([call])
            except (LLMError, RuntimeError) as e:
                subject, body = self._fallback(kind, prompt_vars, reasons, needs)
                note = f"Plain draft used (LLM unavailable: {e})."
            if link and link not in body:
                body = f"{body.rstrip()}\n\nUpload link (no login needed): {link}"
            draft = {
                "kind": kind,
                "to": case.submitter_email,
                "subject": subject,
                "body": body,
                "mailto": mailto(case.submitter_email, subject, body),
                "portal_link": link,
                "needs": need_ids,
                "needs_text": needs,
                "request_id": req.id if req else None,
                "reasons": reasons,
            }
            if req is not None:
                req.message = {"subject": subject, "body": body}
            summary = ", ".join(needs) if needs else "; ".join(reasons[:1])
            if rejection:
                self._note(s, ctx, "rejection_reviewer", "reviewer", case, vendor=name, summary=summary)
                self._note(s, ctx, "rejection_submitter", "submitter", case, vendor=name, summary=summary)
            else:
                self._note(s, ctx, "request_reviewer", "reviewer", case, vendor=name, summary=summary)
                self._note(s, ctx, "request_submitter", "submitter", case, vendor=name, summary=summary)
            s.commit()

        what = {"rejection": "rejection message", "onboarding": "onboarding request", "info": "information request"}[kind]
        reasoning = [f"Drafted a {what} to {case.submitter_email or name}: \"{subject}\"."]
        if needs:
            reasoning.append("Asks for: " + "; ".join(needs) + ".")
        if link:
            reasoning.append("Included a no-login upload link; nothing is sent automatically (copy or open in your mail app).")
        if note:
            reasoning.append(note)
        return AgentResult("drafted", {"draft": draft}, reasoning[:5], confidence=1.0, metrics=metrics)

    @staticmethod
    def _info_request(s: Session, case: Case, need_ids: list[str]) -> VendorRequest:
        """One open info request per case (reused when the case is held again)."""
        req = s.scalars(
            select(VendorRequest).where(
                VendorRequest.case_id == case.id, VendorRequest.kind == "info", VendorRequest.status.in_(["open", "submitted"])
            )
        ).first()
        if req is None:
            req = VendorRequest(vendor_id=case.vendor_id, proposed_name=None, token=secrets.token_urlsafe(16),
                                docs_needed=need_ids, docs_received=[], status="open", case_id=case.id, kind="info")
            s.add(req)
            s.flush()
        else:
            req.docs_needed = need_ids
            req.status = "open"
        return req

    @staticmethod
    def _fallback(kind: str, v: dict[str, Any], reasons: list[str], needs: list[str]) -> tuple[str, str]:
        number = v["invoice_number"] or "(no number)"
        if kind == "rejection":
            subject = f"Invoice {number}: not accepted"
            body = f"Hello {v['recipient_name']},\n\nInvoice {number} for {v['currency']} {v['total']} was not accepted:\n" + "\n".join(
                f"- {r}" for r in reasons) + f"\n\nKind regards,\n{v['signature']}"
        else:
            subject = f"Invoice {number}: information needed"
            asked = [r.removeprefix(REVIEWER_PREFIX) for r in reasons if r.startswith(REVIEWER_PREFIX)]  # the reviewer's words
            body = f"Hello {v['recipient_name']},\n\nBefore we can process invoice {number} for {v['currency']} {v['total']} we need:\n" + "\n".join(
                f"- {n}" for n in [*asked, *needs]) + f"\n\nKind regards,\n{v['signature']}"
        return subject, body
