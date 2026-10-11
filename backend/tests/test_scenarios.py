"""Phase B3: every scenario in data/scenarios.yaml `run_order`, run cumulatively on one database with FakeLLM,
asserting its `expect` block (decision, failed_rules, PO remaining, implied PO, GL, duplicate, onboarding).

Balances depend on the order (S02a reserves PO-1001 before S02b; S08 and S10 use PO-1003 before S09), so
this is one test. `failed_rules` is a subset check: cumulative runs add failures the spec does not list
(e.g. S04 also exhausts PO-1002 after S01), and the decision itself is asserted exactly.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from app import po_ledger
from app.agents import policy
from app.models import Case, PurchaseOrder, RuleResult, StageEvent, Vendor
from app.scenarios import Scenarios, load_scenarios
from app.settings import REPO_ROOT
from tests.conftest import Pipeline

TODAY = date(2026, 10, 11)
STATUS_FOR = {"auto_approve": "approved", "needs_review": "needs_review", "on_hold": "on_hold", "reject": "rejected"}


@pytest.fixture
def sc() -> Scenarios:
    return load_scenarios(REPO_ROOT / "data" / "scenarios.yaml")


def last_output(p: Pipeline, case_id: int, stage: str) -> StageEvent:
    with p.sf() as s:
        ev = s.scalars(
            select(StageEvent)
            .where(StageEvent.case_id == case_id, StageEvent.stage_id == stage, StageEvent.status.in_(["completed", "waiting"]))
            .order_by(StageEvent.seq.desc())
        ).first()
    assert ev is not None, f"case {case_id}: stage {stage} never completed"
    return ev


def get_case(p: Pipeline, case_id: int) -> Case:
    with p.sf() as s:
        c = s.get(Case, case_id)
        assert c is not None
        return c


def rules(p: Pipeline, case_id: int) -> dict[str, RuleResult]:
    with p.sf() as s:
        rows = s.scalars(select(RuleResult).where(RuleResult.case_id == case_id).order_by(RuleResult.run_no)).all()
    return {r.rule_id: r for r in rows}  # latest run wins


def remaining(p: Pipeline, po_number: str) -> Decimal:
    with p.sf() as s:
        po = s.scalars(select(PurchaseOrder).where(PurchaseOrder.po_number == po_number)).one()
        return po_ledger.balance(s, po)["remaining"]


def assert_decided(p: Pipeline, case_id: int, decision: str, failed: list[str], label: str) -> None:
    c = get_case(p, case_id)
    got = c.decision or ("on_hold" if c.status == "on_hold" else None)
    assert got == decision, f"{label}: decision {got}, expected {decision} (status {c.status})"
    assert c.status == STATUS_FOR[decision], f"{label}: status {c.status}"
    if c.decision:
        res = rules(p, case_id)
        failing = {k for k, r in res.items() if r.status == "fail"}
        assert set(failed) <= failing, f"{label}: failed {sorted(failing)}, expected at least {failed}"
        if decision == "auto_approve":
            assert not failing, f"{label}: {failing}"
        assert c.decision_summary, label


def print_table(p: Pipeline, ids: dict[str, int]) -> None:
    """Evidence for the phase report (`pytest -s`)."""
    with p.sf() as s:
        cases = list(s.scalars(select(Case).where(Case.id.in_(ids.values()))).all())
        cases += list(s.scalars(select(Case).where(Case.parent_case_id.in_(ids.values()))).all())
    names = {v: k for k, v in ids.items()}
    print(f"\n{'scenario':<22} {'ref':<15} {'status':<13} {'decision':<13} failed rules")
    for c in sorted(cases, key=lambda c: c.id):
        failing = sorted(k for k, r in rules(p, c.id).items() if r.status == "fail")
        label = names.get(c.id) or f"  child of {names.get(c.parent_case_id or 0)}"
        print(f"{label:<22} {c.ref or '':<15} {c.status:<13} {c.decision or '-':<13} {', '.join(failing)}")
    for po in ("PO-1001", "PO-1002", "PO-1003", "PO-1004", "PO-1005", "PO-1007"):
        print(f"{po} remaining {remaining(p, po)}")


async def test_every_scenario_in_run_order(pipeline: Pipeline, sc: Scenarios, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(policy, "today", lambda: TODAY)
    await pipeline.start()
    assert remaining(pipeline, "PO-1001") == Decimal("10000.00")
    ids: dict[str, int] = {}
    handled: set[tuple[str, str]] = set()

    for sid in sc.run_order:
        spec = sc.scenario(sid)
        inv = sc.invoices(spec)
        codes = {i.vendor for i in inv if i.vendor}
        if spec.new_vendor:
            email = f"billing@{spec.new_vendor.email_domain}"
        elif len(codes) == 1:
            email = f"billing@{sc.vendor(codes.pop()).email_domain}"
        else:
            email = sc.buyer.ap_email
        cid = await pipeline.run_fixture(spec.file, email=email)
        ids[sid] = cid
        exp: dict[str, Any] = spec.expect

        def done(key: str) -> None:
            handled.add((sid, key))  # noqa: B023

        if "child_cases" in exp:
            with pipeline.sf() as s:
                children = s.scalars(select(Case).where(Case.parent_case_id == cid).order_by(Case.id)).all()
            assert len(children) == exp["child_cases"]
            done("child_cases")
            for child, decision in zip(children, exp["decisions"], strict=True):
                assert_decided(pipeline, child.id, decision, [], f"{sid} child {child.ref}")
            done("decisions")
            continue

        decision = exp["decision"]
        assert_decided(pipeline, cid, decision, exp.get("failed_rules", []), sid)
        done("decision")
        done("failed_rules")

        if "po_remaining_after" in exp:
            po_no = spec.invoice.po_reference if spec.invoice else None
            assert po_no and remaining(pipeline, po_no) == Decimal(str(exp["po_remaining_after"]))
            done("po_remaining_after")
        if "implied_po" in exp:
            pm = last_output(pipeline, cid, "po_match")
            assert pm.outcome == "implied" and pm.output["po"]["po_number"] == exp["implied_po"]
            assert get_case(pipeline, cid).po_id == pm.output["po_id"]
            done("implied_po")
        if "duplicate_of" in exp:
            dup = rules(pipeline, cid)["duplicate_exact"]
            assert dup.evidence["other_case_id"] == ids[exp["duplicate_of"]]
            done("duplicate_of")
        if "gl" in exp:
            assert last_output(pipeline, cid, "gl_coding").output["gl"]["primary"] == exp["gl"]
            done("gl")
        if "vendor_status" in exp:
            assert last_output(pipeline, cid, "vendor_check").outcome == exp["vendor_status"]
            assert last_output(pipeline, cid, "await_vendor").status == "waiting"
            done("vendor_status")
        if "after_onboarding" in exp:
            # B4 builds the portal upload + admin approve; here the admin approval is simulated directly
            onboarding = last_output(pipeline, cid, "onboarding").output
            assert onboarding["request"]["token"] and onboarding["request"]["docs_needed"] == ["tax_form", "bank_letter"]
            with pipeline.sf() as s:
                v = s.get(Vendor, onboarding["vendor_id"])
                assert v is not None and v.status == "pending_docs"
                v.status = "approved"
                s.commit()
            assert pipeline.orch is not None
            assert await pipeline.orch.fire_event(cid, "vendor_approved")
            await pipeline.orch.join()
            after = exp["after_onboarding"]
            assert_decided(pipeline, cid, after["decision"], [], f"{sid} after onboarding")
            assert last_output(pipeline, cid, "gl_coding").output["gl"]["primary"] == after["gl"]
            assert "non_po_requires_review" in {k for k, r in rules(pipeline, cid).items() if r.status == "fail"}
            done("after_onboarding")

        # scenario-specific numbers from SPEC section 11
        res = rules(pipeline, cid)
        if sid == "S01_happy":
            assert remaining(pipeline, "PO-1002") == Decimal("0.00")  # 4,415.00 reserved (consumed at finalize, B4)
            assert last_output(pipeline, cid, "po_match").outcome == "matched"
        if sid == "S02b_split_part2":
            bal = res["po_balance"].evidence
            assert (bal["amount"], bal["remaining"], bal["over_pct"]) == ("4,300.00", "4,000.00", "7.5")
            assert res["unmatched_lines"].evidence["lines"] == ["Premium support add-on"]
            assert "4,000.00" in res["po_balance"].explanation and "7.5%" in res["po_balance"].explanation
        if sid == "S03_implied_po":
            cands = last_output(pipeline, cid, "po_match").output["candidates"]
            assert [c["po_number"] for c in cands] == ["PO-1004", "PO-1006"]
            assert cands[0]["score"] >= 0.85 and cands[0]["score"] - cands[1]["score"] >= 0.15
        if sid == "S04_duplicate_scan":
            assert "INV-A-2207" in res["duplicate_exact"].explanation or "INV A2207" in res["duplicate_exact"].explanation
        if sid == "S06_bank_change":
            assert "payment-fraud" in res["bank_details_match"].explanation
            assert (res["bank_details_match"].evidence["invoice_last4"], res["bank_details_match"].evidence["master_last4"]) == ("9910", "7788")
            assert last_output(pipeline, cid, "vendor_check").outcome == "bank_mismatch"
        if sid == "S07_non_po":
            assert last_output(pipeline, cid, "po_match").outcome == "no_po"
            assert "6100 Facilities Maintenance" in res["non_po_requires_review"].explanation
        if sid == "S08_price_variance":
            item = res["price_tolerance"].evidence["items"][0]
            assert (item["inv_price"], item["po_price"], item["pct"], item["abs_diff"]) == ("1,310.00", "1,250.00", "4.8", "60.00")
        if sid == "S09_missing_fields":
            assert res["required_fields"].evidence["missing"] == ["invoice_number", "invoice_date"]

    print_table(pipeline, ids)
    every = {(s.id, k) for s in sc.scenarios if s.id in sc.run_order for k in s.expect}
    assert every <= handled, f"unasserted expect keys: {sorted(every - handled)}"
    # needs_review and auto_approve reserve; on_hold and reject do not
    assert remaining(pipeline, "PO-1001") == Decimal("-300.00")  # S02a 6,000 + S02b 4,300 reserved
    with pipeline.sf() as s:
        s04 = s.get(Case, ids["S04_duplicate_scan"])
        assert s04 is not None and s04.decision == "reject"
