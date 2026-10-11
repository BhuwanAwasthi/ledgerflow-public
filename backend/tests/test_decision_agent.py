"""Phase B3: the decision agent's reservation guard and release, and the LLM fallback explanation."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import po_ledger
from app.agents.base import AgentContext
from app.agents.decision import DecisionAgent, contradicts, lead_sentence
from app.config_loader import build_config, read_config_files
from app.llm import FakeLLM
from app.models import Case, POConsumption, POLine, PurchaseOrder, RuleResult, StageEvent
from app.seed import load_master
from tests.conftest import CONFIG_DIR, LLM_FIXTURES, MASTER_DIR, Pipeline

CFG = build_config(read_config_files(CONFIG_DIR))


def _setup(sf: sessionmaker[Session]) -> tuple[int, int, int]:
    with sf() as s:
        load_master(s, MASTER_DIR)
        po = s.scalars(select(PurchaseOrder).where(PurchaseOrder.po_number == "PO-1001")).one()
        line = s.scalars(select(POLine).where(POLine.po_id == po.id)).one()
        cases = [Case(ref=f"INV-T-{i}", status="processing", vendor_id=po.vendor_id) for i in (1, 2)]
        s.add_all(cases)
        s.commit()
        return po.id, line.id, cases[1].id


def _state(po_id: int, line_id: int, results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "normaliser": {"invoice": {"vendor_name": "Brightline", "invoice_number": "BL-1", "currency": "USD", "subtotal": "4000.00", "total": "4000.00", "lines": []}},
        "extraction": {"field_meta": {f: {"confidence": 0.97} for f in ("invoice_number", "invoice_date", "total", "vendor_name")}},
        "vendor": {"vendor_id": 2, "vendor": {"status": "approved"}},
        "po_matching": {
            "po_id": po_id,
            "match_type": "explicit",
            "lines": [{"index": 0, "description": "licence", "qty": "40", "unit_price": "100.00", "amount": "4000.00", "po_line_id": line_id, "po_line_no": 1}],
        },
        "policy": {"results": results},
    }


def _ctx(sf: sessionmaker[Session], case_id: int, state: dict[str, Any]) -> AgentContext:
    return AgentContext(
        case_id=case_id, case_ref=None, stage_id="decide", run_no=1, config=CFG,
        agent_config=CFG.agents["decision"], state=state, session_factory=sf, llm=FakeLLM(LLM_FIXTURES),
    )


async def test_balance_rechecked_at_reservation(session_factory: sessionmaker[Session]):
    po_id, line_id, case_id = _setup(session_factory)
    passing = [{"rule_id": "po_balance", "name": "PO balance", "status": "pass", "severity": "review", "explanation": "ok"}]
    # policy saw the full 10,000 available; another split invoice reserved 7,000 before this decision ran
    with session_factory() as s:
        other = s.scalars(select(Case).where(Case.ref == "INV-T-1")).one()
        po_ledger.reserve(s, other.id, po_id, [po_ledger.Reservation(line_id, Decimal("7000.00"), Decimal(70))])
        s.commit()
    agent = DecisionAgent(CFG.agents["decision"])
    res = await agent.run("decide", _ctx(session_factory, case_id, _state(po_id, line_id, passing)))
    assert res.outcome == "needs_review"
    assert "po_balance" in res.data["failed_rules"]
    assert any("PO balance changed since the policy check" in r for r in res.reasoning)
    assert res.data["reservation"]["amount"] == "4000.00" and res.data["reservation"]["remaining_after"] == "-1000.00"
    # no fixture for this made-up invoice: the explanation falls back to the rule results
    assert any("LLM unavailable" in r for r in res.reasoning)
    with session_factory() as s:
        c = s.get(Case, case_id)
        assert c is not None and c.decision == "needs_review" and c.decision_summary
        assert "Every check passed" not in c.decision_summary and "PO-1001" in c.decision_summary
        row = s.scalars(select(RuleResult).where(RuleResult.case_id == case_id, RuleResult.rule_id == "po_balance")).one()
        assert row.status == "fail"


async def test_on_hold_releases_an_earlier_reservation(session_factory: sessionmaker[Session]):
    po_id, line_id, case_id = _setup(session_factory)
    agent = DecisionAgent(CFG.agents["decision"])
    ok = [{"rule_id": "po_balance", "name": "PO balance", "status": "pass", "severity": "review", "explanation": "ok"}]
    first = await agent.run("decide", _ctx(session_factory, case_id, _state(po_id, line_id, ok)))
    assert first.outcome == "auto_approve" and first.data["reservation"]["remaining_after"] == "6000.00"
    held = [*ok, {"rule_id": "bank_details_match", "name": "Bank", "status": "fail", "severity": "hold", "explanation": "bank changed"}]
    second = await agent.run("decide", _ctx(session_factory, case_id, _state(po_id, line_id, held)))
    assert second.outcome == "on_hold" and second.data["reservation"] is None
    with session_factory() as s:
        states = s.scalars(select(POConsumption.state).where(POConsumption.case_id == case_id)).all()
        po = s.get(PurchaseOrder, po_id)
        assert po is not None
        assert states == ["released"] and po_ledger.balance(s, po)["remaining"] == Decimal("10000.00")


async def test_s04_twice_summary_never_contradicts_the_decision(pipeline: Pipeline):
    """The first S04 is new (not a reject) while FakeLLM's only S04 explanation says "Rejected: duplicate";
    the second S04 is a duplicate of the first and is rejected. Both summaries lead with the code-built
    sentence for their own decision."""
    await pipeline.start()
    first = await pipeline.run_fixture("S04_acme_scan_duplicate.pdf")
    second = await pipeline.run_fixture("S04_acme_scan_duplicate.pdf")
    params = CFG.agents["decision"].params

    seen = {}
    for cid in (first, second):
        with pipeline.sf() as s:
            c = s.get(Case, cid)
            assert c is not None and c.decision and c.decision_summary
            evs = s.scalars(select(StageEvent).where(StageEvent.case_id == cid, StageEvent.status == "completed")).all()
        decide = [e for e in evs if e.stage_id == "decide"][-1]
        results = [e for e in evs if e.stage_id == "policy"][-1].output["results"]
        expl = decide.output["explanation"]
        lead = lead_sentence(params["decision_labels"][c.decision], results, decide.output["gate_failures"], CFG.rules)
        assert expl["lead"] == lead
        assert c.decision_summary == lead or c.decision_summary.startswith(lead + " ")
        assert contradicts(c.decision_summary, c.decision, params["decision_terms"]) is None
        seen[cid] = (c.decision, c.decision_summary, expl["source"])

    decision, summary, source = seen[first]
    assert decision != "reject"
    assert "rejected" not in summary.lower() and "duplicate" not in summary.lower()
    assert source == "rules"  # the model's "Rejected: ..." text was discarded
    decision, summary, source = seen[second]
    assert decision == "reject"
    assert summary.startswith("Rejected: Duplicate invoice")
    assert source == "llm" and summary.count("Rejected") == 1  # the model's restated "Rejected:" was dropped
