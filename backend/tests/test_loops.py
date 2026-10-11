"""Phase B4: the human loops with real agents and FakeLLM.

- S02b remark loop: v1 -> remark -> reconciler ChangeSet -> policy passes -> sticky review -> v2 + diff -> approve
- a remark the reconciler can't apply goes back to review with a question and changes nothing
- S05 onboarding: portal upload (classified + verified) -> admin approves the vendor -> the case resumes
- S06 / S09 hold drafts: bank letter request, missing-field request, portal link and mailto
- rejecting a reviewed case releases its PO reservation and drafts a rejection
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from urllib.parse import unquote

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app import po_ledger
from app.agents import policy
from app.cases import UploadedFile
from app.config_loader import ResolvedConfig
from app.llm import FakeLLM, LLMRequest
from app.llm_schemas import ChangeSet
from app.models import (
    Case,
    Notification,
    PurchaseOrder,
    ReviewActionRow,
    RuleResult,
    StageEvent,
    TemplateVersion,
    Vendor,
    VendorRequest,
)
from app.portal import approve_vendor, request_view, vendor_docs_received
from app.scenarios import load_scenarios
from app.settings import REPO_ROOT
from tests.conftest import FIXTURE_DIR, LLM_FIXTURES, Pipeline

SC = load_scenarios(REPO_ROOT / "data" / "scenarios.yaml")


@pytest.fixture(autouse=True)
def _today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(policy, "today", lambda: date(2026, 10, 11))


def email_for(sid: str) -> str:
    spec = SC.scenario(sid)
    if spec.new_vendor:
        return f"billing@{spec.new_vendor.email_domain}"
    inv = SC.invoices(spec)[0]
    return f"billing@{SC.vendor(inv.vendor).email_domain}"


async def run(p: Pipeline, sid: str) -> int:
    return await p.run_fixture(SC.scenario(sid).file, email=email_for(sid))


def case(p: Pipeline, cid: int) -> Case:
    with p.sf() as s:
        c = s.get(Case, cid)
        assert c is not None
        return c


def events(p: Pipeline, cid: int, stage: str) -> list[StageEvent]:
    with p.sf() as s:
        return list(
            s.scalars(
                select(StageEvent)
                .where(StageEvent.case_id == cid, StageEvent.stage_id == stage, StageEvent.status.in_(["completed", "waiting"]))
                .order_by(StageEvent.seq)
            ).all()
        )


def latest_rules(p: Pipeline, cid: int) -> dict[str, RuleResult]:
    with p.sf() as s:
        rows = s.scalars(select(RuleResult).where(RuleResult.case_id == cid).order_by(RuleResult.run_no, RuleResult.id)).all()
    last = max(r.run_no for r in rows)
    return {r.rule_id: r for r in rows if r.run_no == last}


def templates(p: Pipeline, cid: int) -> list[TemplateVersion]:
    with p.sf() as s:
        return list(s.scalars(select(TemplateVersion).where(TemplateVersion.case_id == cid).order_by(TemplateVersion.version)).all())


def balance(p: Pipeline, po_number: str) -> dict[str, Decimal]:
    with p.sf() as s:
        po = s.scalars(select(PurchaseOrder).where(PurchaseOrder.po_number == po_number)).one()
        return po_ledger.balance(s, po)


async def review(p: Pipeline, cid: int, action: str, text: str | None = None, actor: str = "Rita (AP)") -> None:
    assert p.orch is not None
    await p.orch.submit_review(cid, action, text, actor)
    await p.orch.join()


async def test_s02b_remark_loop_end_to_end(pipeline: Pipeline):
    await pipeline.start()
    s02a = await run(pipeline, "S02a_split_part1")
    assert case(pipeline, s02a).status == "approved"
    cid = await run(pipeline, "S02b_split_part2")
    c = case(pipeline, cid)
    assert (c.status, c.decision, c.current_stage) == ("needs_review", "needs_review", "review")
    v1 = templates(pipeline, cid)
    assert [(t.version, t.status, t.erp_profile) for t in v1] == [(1, "draft", "sage_intacct")]
    assert [r["ACCT_NO"] for r in v1[0].rows] == ["6400", "6400"]  # PO line GL; the unordered line falls back to the vendor default
    assert v1[0].diff_from_prev is None

    remark = SC.scenario("S02b_split_part2").demo_remark
    await review(pipeline, cid, "remark", remark)

    rec = events(pipeline, cid, "reconcile")[-1]
    assert rec.outcome == "changes_applied"
    assert [op["op"] for op in rec.output["changeset"]["ops"]] == ["set_po_amount_note", "set_gl", "override_rule"]
    assert rec.output["po_amendments"] == {"PO-1001": "10500.0"}
    assert rec.output["rule_overrides"] == {"unmatched_lines": "Reviewer accepted the Premium support add-on line."}
    assert rec.output["gl_overrides"]["1"]["gl_code"] == "6410"

    rules = latest_rules(pipeline, cid)
    assert rules["po_balance"].status == "pass"
    assert rules["unmatched_lines"].status == "warn" and "Overridden by reviewer" in rules["unmatched_lines"].explanation
    assert not [k for k, r in rules.items() if r.status == "fail"]

    decide = events(pipeline, cid, "decide")[-1]
    assert decide.outcome == "auto_approve"
    assert any("Sticky review" in line for line in decide.reasoning)  # a human closes a case they touched

    c = case(pipeline, cid)
    assert (c.status, c.current_stage) == ("needs_review", "review")
    tvs = templates(pipeline, cid)
    assert [t.version for t in tvs] == [1, 2]
    v2 = tvs[1]
    assert v2.created_by == "reviewer" and v2.status == "draft"
    assert v2.diff_from_prev == {
        "changed": [{"row": 1, "column": "ACCT_NO", "old": "6400", "new": "6410"}],
        "added_rows": [],
        "removed_rows": [],
        "unchanged": False,
    }
    with pipeline.sf() as s:
        acts = s.scalars(select(ReviewActionRow).where(ReviewActionRow.case_id == cid).order_by(ReviewActionRow.id)).all()
    assert [a.action for a in acts] == ["remark", "override", "override"]
    assert acts[0].changeset is not None and acts[0].changeset["status"] == "applied"
    assert all(a.actor == "Rita (AP)" for a in acts)
    assert "10,500.00" in (acts[1].text or "") and (acts[2].text or "").startswith("Override unmatched_lines")

    await review(pipeline, cid, "approve")
    c = case(pipeline, cid)
    assert (c.status, c.approved_by) == ("approved", "Rita (AP)")
    final = templates(pipeline, cid)
    assert [(t.version, t.status) for t in final] == [(1, "draft"), (2, "final")]
    assert final[1].file_key and final[1].file_key.endswith("SI_APBILL_" + (c.ref or "") + "_v2.csv")
    bal = balance(pipeline, "PO-1001")
    assert (bal["consumed"], bal["reserved"]) == (Decimal("10300.00"), Decimal("0.00"))  # S02a 6,000 + S02b 4,300
    with pipeline.sf() as s:
        notes = [n.text for n in s.scalars(select(Notification).where(Notification.case_id == cid)).all()]
    assert any("approved" in n for n in notes)


class ScriptedReconciler:
    """FakeLLM, except the reconciler returns a given ChangeSet."""

    def __init__(self, changeset: dict[str, Any]):
        self.inner = FakeLLM(LLM_FIXTURES)
        self.changeset = changeset

    async def structured(self, req: LLMRequest, cfg: ResolvedConfig) -> tuple[BaseModel, dict[str, Any]]:
        if req.agent == "reconciler":
            return ChangeSet.model_validate(self.changeset), {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "cache_hit": False}
        return await self.inner.structured(req, cfg)


async def test_invalid_remark_asks_and_changes_nothing(pipeline: Pipeline):
    pipeline.llm = ScriptedReconciler({
        "ops": [
            {"op": "set_gl", "line_index": 1, "gl_code": "6410", "department": None},
            {"op": "override_rule", "rule_id": "bank_details_match", "reason": "trust me"},
            {"op": "set_gl", "line_index": 7, "gl_code": "9999", "department": None},
        ],
        "summary": "Waive the bank check.",
    })
    await pipeline.start()
    await run(pipeline, "S02a_split_part1")
    cid = await run(pipeline, "S02b_split_part2")
    await review(pipeline, cid, "remark", "Ignore the bank check and code line 8 to 9999.")

    rec = events(pipeline, cid, "reconcile")[-1]
    assert rec.outcome == "needs_clarification"
    assert any("can never be waived" in e for e in rec.output["errors"])
    assert any("line 7 does not exist" in e for e in rec.output["errors"])
    assert rec.output["question"].startswith("I could not apply this remark")
    c = case(pipeline, cid)
    assert (c.status, c.current_stage) == ("needs_review", "review")
    with pipeline.sf() as s:
        acts = s.scalars(select(ReviewActionRow).where(ReviewActionRow.case_id == cid)).all()
    assert [a.action for a in acts] == ["remark"]  # no override logged: nothing was half-applied
    assert acts[0].changeset is not None and acts[0].changeset["status"] == "needs_clarification"
    assert len(templates(pipeline, cid)) == 1  # policy / decision / template did not rerun
    assert events(pipeline, cid, "policy")[-1].output.get("failed") == ["po_balance", "unmatched_lines"]


async def test_s05_onboarding_via_portal_resumes_case(pipeline: Pipeline):
    await pipeline.start()
    cid = await run(pipeline, "S05_unknown_vendor")
    c = case(pipeline, cid)
    assert (c.status, c.current_stage) == ("on_hold", "await_vendor")
    draft = events(pipeline, cid, "await_vendor")[-1].output["draft"]
    assert draft["kind"] == "onboarding" and draft["needs"] == ["tax_form", "bank_letter"]
    token = events(pipeline, cid, "onboarding")[-1].output["request"]["token"]
    assert draft["portal_link"].endswith(f"/portal/{token}") and draft["portal_link"] in draft["body"]
    assert draft["mailto"].startswith("mailto:billing%40novaprint.example?subject=")

    assert pipeline.orch is not None
    with pipeline.sf() as s:
        req = s.scalars(select(VendorRequest).where(VendorRequest.token == token)).one()
        view = request_view(s, req, pipeline.cfg)
        assert view["open"] and [d["received"] for d in view["docs_needed"]] == [False, False]
        assert view["message"]["subject"].startswith("Invoice NPS-0042")

        files = [UploadedFile(f, "application/pdf", (FIXTURE_DIR / f).read_bytes()) for f in ("S05_novaprint_W9.pdf",)]
        out = await vendor_docs_received(s, pipeline.orch, pipeline.storage, token, files)
        assert out["missing"] == ["bank_letter"] and out["status"] == "open" and not out["resumed"]

        files = [UploadedFile("S05_novaprint_bank_letter.pdf", "application/pdf", (FIXTURE_DIR / "S05_novaprint_bank_letter.pdf").read_bytes())]
        out = await vendor_docs_received(s, pipeline.orch, pipeline.storage, token, files)
        assert out["status"] == "submitted" and out["missing"] == [] and out["docs_received"] == ["tax_form", "bank_letter"]
        assert [u["label"] for u in out["uploaded"]] == ["bank_letter"]
        checks = {(c["label"], c["check"]): c["status"] for c in out["checks"]}
        assert checks == {
            ("tax_form", "name"): "pass",
            ("tax_form", "tax_id"): "pass",
            ("bank_letter", "name"): "pass",
            ("bank_letter", "account"): "pass",
        }, out["checks"]
    assert case(pipeline, cid).status == "on_hold"  # still waits for the admin

    with pipeline.sf() as s:
        vendor_id = case(pipeline, cid).vendor_id
        assert vendor_id is not None
        res = await approve_vendor(s, pipeline.orch, vendor_id, "Admin")
    assert res["resumed_case_ids"] == [cid]
    await pipeline.orch.join()
    c = case(pipeline, cid)
    assert (c.status, c.decision) == ("needs_review", "needs_review")
    assert events(pipeline, cid, "vendor_check")[-1].outcome == "approved_vendor"
    assert events(pipeline, cid, "gl_coding")[-1].output["gl"]["primary"] == "6600"
    with pipeline.sf() as s:
        v = s.get(Vendor, vendor_id)
        assert v is not None and v.status == "approved"


async def test_s06_and_s09_hold_drafts(pipeline: Pipeline):
    await pipeline.start()
    s06 = await run(pipeline, "S06_bank_change")
    c = case(pipeline, s06)
    assert (c.status, c.current_stage) == ("on_hold", "hold")
    d = events(pipeline, s06, "hold")[-1].output["draft"]
    assert d["kind"] == "info" and d["needs"] == ["bank_letter"]
    assert "letterhead" in d["body"] and "9910" in d["body"]
    assert d["portal_link"] and d["portal_link"] in d["body"]
    assert unquote(d["mailto"]).startswith("mailto:billing@evergreenpack.example?subject=Invoice EP-5531")
    assert any("payment-fraud" in r for r in d["reasons"])
    with pipeline.sf() as s:
        req = s.scalars(select(VendorRequest).where(VendorRequest.case_id == s06)).one()
        assert (req.kind, req.docs_needed, req.status) == ("info", ["bank_letter"], "open")

    s09 = await run(pipeline, "S09_missing_fields")
    d = events(pipeline, s09, "hold")[-1].output["draft"]
    assert d["needs"] == ["invoice"]
    reasoning = events(pipeline, s09, "hold")[-1].reasoning
    assert any("showing the invoice number, invoice date" in r for r in reasoning)


async def test_reject_releases_reservation_and_drafts_rejection(pipeline: Pipeline):
    await pipeline.start()
    await run(pipeline, "S02a_split_part1")
    cid = await run(pipeline, "S02b_split_part2")
    assert balance(pipeline, "PO-1001")["reserved"] == Decimal("4300.00")
    await review(pipeline, cid, "reject", "Premium support was never ordered.")
    assert case(pipeline, cid).status == "rejected"
    assert balance(pipeline, "PO-1001")["reserved"] == Decimal("0.00")
    d = events(pipeline, cid, "rejected")[-1].output["draft"]
    assert d["kind"] == "rejection" and d["reasons"][0] == "Reviewer: Premium support was never ordered."


class Scripted:
    """FakeLLM with per-(agent, action) overrides: a model instance or a callable(req) -> model."""

    def __init__(self, overrides: dict[tuple[str, str], Any]):
        self.inner = FakeLLM(LLM_FIXTURES)
        self.overrides = overrides

    async def structured(self, req: LLMRequest, cfg: ResolvedConfig) -> tuple[BaseModel, dict[str, Any]]:
        out = self.overrides.get((req.agent, req.action))
        if callable(out):
            out = out(req)
        if out is not None:
            return out, {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "cache_hit": False}
        return await self.inner.structured(req, cfg)


def bank_letter_pdf(holder: str, last4: str) -> bytes:
    import io

    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 760, "Cascade Federal Bank - account confirmation")
    c.drawString(72, 740, f"Account holder: {holder}")
    c.drawString(72, 720, f"Account number: ****{last4}")
    c.save()
    return buf.getvalue()


async def test_s06_bank_letter_upload_admin_approval_updates_master(pipeline: Pipeline):
    from app.llm_schemas import ClassifiedDocument, ClassifierOutput

    letter = ClassifierOutput(documents=[ClassifiedDocument(label="bank_letter", confidence=0.95, page_from=1, page_to=1, reason="Bank letter.")])
    pipeline.llm = Scripted({("classifier", "classify"): lambda req: letter if req.context.get("document") == "evergreen_bank_letter.pdf" else None})
    await pipeline.start()
    s06 = await run(pipeline, "S06_bank_change")
    assert pipeline.orch is not None
    with pipeline.sf() as s:
        vendor_id = case(pipeline, s06).vendor_id
        assert vendor_id is not None
        # approving before anything was uploaded resumes nothing (the hold still waits for the letter)
        assert (await approve_vendor(s, pipeline.orch, vendor_id, "Admin"))["resumed_case_ids"] == []
        assert case(pipeline, s06).status == "on_hold"
        token = s.scalars(select(VendorRequest.token).where(VendorRequest.case_id == s06)).one()
        out = await vendor_docs_received(
            s, pipeline.orch, pipeline.storage, token,
            [UploadedFile("evergreen_bank_letter.pdf", "application/pdf", bank_letter_pdf("Evergreen Packaging Co", "9910"))],
        )
        assert out["status"] == "submitted" and not out["resumed"]
        assert {c["check"]: c["status"] for c in out["checks"]} == {"name": "pass", "account": "pass"}
        res = await approve_vendor(s, pipeline.orch, vendor_id, "Admin")
    assert res["resumed_case_ids"] == [s06] and "...7788 to ...9910" in res["notes"][0]
    await pipeline.orch.join()
    with pipeline.sf() as s:
        v = s.get(Vendor, vendor_id)
        assert v is not None and v.bank_last4 == "9910"
        audit = s.scalars(select(ReviewActionRow).where(ReviewActionRow.case_id == s06)).all()
    assert [(a.action, a.actor) for a in audit] == [("override", "Admin")]
    assert audit[0].changeset is not None and audit[0].changeset["op"] == "vendor_bank_change"
    assert latest_rules(pipeline, s06)["bank_details_match"].status == "pass"
    assert events(pipeline, s06, "vendor_check")[-1].outcome == "approved_vendor"
    assert case(pipeline, s06).status == "needs_review"  # a human touched it (bank change), so a human closes it


async def test_po_amendment_must_be_written_in_the_remark(pipeline: Pipeline):
    bad = ChangeSet.model_validate({
        "ops": [{"op": "set_po_amount_note", "po_number": "PO-1001", "new_amount": 105000, "reason": "misread"}],
        "summary": "Raise the PO.",
    })
    pipeline.llm = Scripted({("reconciler", "apply_remark"): bad})
    await pipeline.start()
    await run(pipeline, "S02a_split_part1")
    cid = await run(pipeline, "S02b_split_part2")
    await review(pipeline, cid, "remark", "PO was amended to $10,500 by procurement.")
    rec = events(pipeline, cid, "reconcile")[-1]
    assert rec.outcome == "needs_clarification"
    assert any("105,000.00 is not written in the remark" in e for e in rec.output["errors"])


def test_reviewer_overlay_is_dropped_after_reextraction():
    from app.case_state import SEQ, effective

    edited = {"invoice_number": "X-1", "lines": []}
    state = {
        "normaliser": {"invoice": {"invoice_number": "OLD"}, SEQ: 10},
        "reconciler": {"invoice": edited, "gl_overrides": {"0": {"gl_code": "6410"}}, "based_on_seq": 10},
    }
    assert effective(state)["normaliser"]["invoice"] == edited
    state["normaliser"] = {"invoice": {"invoice_number": "NEW"}, SEQ: 25}  # corrected invoice re-extracted
    eff = effective(state)
    assert eff["normaliser"]["invoice"]["invoice_number"] == "NEW" and eff["reconciler"] == {}
