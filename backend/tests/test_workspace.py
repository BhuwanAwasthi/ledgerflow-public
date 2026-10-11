"""Phase F2: the review workspace view and typed grid edits.

- workspace payload: editable columns from the ERP profile, amber cells from failed rules and low confidence,
  PO balance live from the ledger, duplicate links (S04 -> S01)
- grid edits (`ReviewIn.ops`) go through the reconciler's validation without an LLM call, rerun policy ->
  decision -> template and produce v2 with a diff
- an invalid grid edit asks for clarification and changes nothing
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from app.agents import policy
from app.api.schemas import ReviewIn
from app.config_loader import ConfigError, ResolvedConfig, build_config, read_config_files
from app.llm import FakeLLM, LLMRequest
from app.models import Case, ReviewActionRow, RuleResult
from app.orchestrator import OrchestratorError
from app.workspace import build_workspace, cell_flags, column_specs
from tests.conftest import CONFIG_DIR, LLM_FIXTURES, Pipeline, patch_yaml
from tests.test_loops import case, events, latest_rules, run, templates


@pytest.fixture(autouse=True)
def _today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(policy, "today", lambda: date(2026, 10, 11))


class CountingLLM:
    """FakeLLM that records which agents called the model."""

    def __init__(self) -> None:
        self.inner = FakeLLM(LLM_FIXTURES)
        self.agents: list[str] = []

    async def structured(self, req: LLMRequest, cfg: ResolvedConfig) -> tuple[BaseModel, dict[str, Any]]:
        self.agents.append(req.agent)
        return await self.inner.structured(req, cfg)


def workspace(p: Pipeline, cid: int) -> dict[str, Any]:
    assert p.orch is not None
    with p.sf() as s:
        c = s.get(Case, cid)
        assert c is not None
        return build_workspace(s, p.orch.case_config(s, c), c)


def flags(ws: dict[str, Any]) -> dict[tuple[int, str], list[str]]:
    return {(f["row"], f["column"]): f["reasons"] for f in ws["cells"]}


async def test_s02b_workspace_columns_flags_and_balance(pipeline: Pipeline):
    await pipeline.start()
    await run(pipeline, "S02a_split_part1")
    cid = await run(pipeline, "S02b_split_part2")
    ws = workspace(pipeline, cid)

    cols = {c["header"]: c for c in ws["columns"]}
    assert ws["erp_profile"] == "sage_intacct" and ws["grain"] == "line"
    assert cols["AMOUNT"]["edit"] == {"op": "set_field", "field": "amount", "path": "lines[{i}].amount", "input": "money"}
    assert cols["ACCT_NO"]["edit"]["op"] == "set_gl"
    assert cols["DATE_CREATED"]["edit"]["input"] == "date"
    assert cols["VENDOR_ID"]["edit"] is None and cols["PONUMBER"]["binds"] == ["po"]
    assert cols["DESCRIPTION"]["edit"] is None  # two values in one cell: not editable
    assert cols["TAX_AMOUNT"]["edit"] is None  # computed, not an invoice field

    f = flags(ws)
    assert f[(0, "PONUMBER")] == f[(1, "PONUMBER")] == ["Over PO balance"]  # a header-level rule: every row
    assert f[(1, "MEMO")] == f[(1, "ACCT_NO")] == ["Lines not on the PO"]  # only the unmatched line
    assert (0, "MEMO") not in f and (0, "AMOUNT") not in f

    bal = ws["po_balance"]
    assert (bal["po_number"], bal["amount"], bal["committed_by_others"], bal["this_invoice"], bal["remaining_after"]) == (
        "PO-1001", Decimal("10000.00"), Decimal("6000.00"), Decimal("4300.00"), Decimal("-300.00")
    )
    assert bal["this_state"] == "reserved" and not bal["amended"]
    assert [ln["po_line_no"] for ln in ws["match"]["lines"]] == [1, None]
    assert ws["reviewable"] and ws["duplicates"] == [] and ws["draft"] is None


async def test_s04_workspace_links_the_duplicate(pipeline: Pipeline):
    await pipeline.start()
    s01 = await run(pipeline, "S01_happy")
    cid = await run(pipeline, "S04_duplicate_scan")
    ws = workspace(pipeline, cid)
    assert [(d["rule_id"], d["case_id"]) for d in ws["duplicates"]] == [("duplicate_exact", s01)]
    assert not ws["reviewable"]
    assert ws["draft"] is not None and ws["draft"]["kind"] == "rejection"


async def test_grid_edits_apply_without_llm_and_make_v2(pipeline: Pipeline):
    llm = CountingLLM()
    pipeline.llm = llm
    await pipeline.start()
    await run(pipeline, "S02a_split_part1")
    cid = await run(pipeline, "S02b_split_part2")
    before = len(llm.agents)
    ops = [
        {"op": "set_gl", "line_index": 1, "gl_code": "6410", "department": None},
        {"op": "set_field", "path": "payment_terms", "value": "Net 45"},
    ]
    assert pipeline.orch is not None
    await pipeline.orch.submit_review(cid, "remark", None, "Rita (AP)", ops)
    await pipeline.orch.join()

    assert "reconciler" not in llm.agents[before:]
    rec = events(pipeline, cid, "reconcile")[-1]
    assert rec.outcome == "changes_applied" and rec.output["changeset"]["source"] == "grid"
    # the PO is still over balance, so the case comes back to review with v2
    assert latest_rules(pipeline, cid)["po_balance"].status == "fail"
    c = case(pipeline, cid)
    assert (c.status, c.current_stage) == ("needs_review", "review")
    v2 = templates(pipeline, cid)[-1]
    assert v2.version == 2 and v2.created_by == "reviewer"
    changed = {(d["row"], d["column"]): (d["old"], d["new"]) for d in v2.diff_from_prev["changed"]}
    assert changed[(1, "ACCT_NO")] == ("6400", "6410")
    assert changed[(0, "TERMNAME")] == ("Net 30", "Net 45") and changed[(1, "TERMNAME")] == ("Net 30", "Net 45")
    ws = workspace(pipeline, cid)
    assert ws["invoice"]["payment_terms"] == "Net 45"
    with pipeline.sf() as s:
        remark = s.scalars(select(ReviewActionRow).where(ReviewActionRow.case_id == cid)).one()
    assert remark.changeset is not None and remark.changeset["status"] == "applied"


async def test_invalid_grid_edit_asks_and_changes_nothing(pipeline: Pipeline):
    await pipeline.start()
    await run(pipeline, "S02a_split_part1")
    cid = await run(pipeline, "S02b_split_part2")
    assert pipeline.orch is not None
    await pipeline.orch.submit_review(
        cid, "remark", None, "Rita (AP)", [{"op": "set_gl", "line_index": 1, "gl_code": "9999", "department": None}]
    )
    await pipeline.orch.join()
    rec = events(pipeline, cid, "reconcile")[-1]
    assert rec.outcome == "needs_clarification"
    assert any("9999 is not in the chart of accounts" in e for e in rec.output["errors"])
    assert len(templates(pipeline, cid)) == 1
    assert case(pipeline, cid).status == "needs_review"


async def test_ops_only_with_a_remark(pipeline: Pipeline):
    await pipeline.start()
    await run(pipeline, "S02a_split_part1")
    cid = await run(pipeline, "S02b_split_part2")
    assert pipeline.orch is not None
    with pytest.raises(OrchestratorError):
        await pipeline.orch.submit_review(cid, "approve", None, "x", [{"op": "set_field", "path": "notes", "value": "x"}])


def test_review_in_accepts_only_grid_ops():
    ok = ReviewIn.model_validate({"action": "remark", "ops": [{"op": "set_field", "path": "total", "value": "10"}]})
    assert ok.ops is not None and ok.ops[0].op == "set_field"
    with pytest.raises(ValidationError):  # a waiver or PO amendment needs a written remark
        ReviewIn.model_validate({"action": "remark", "ops": [{"op": "override_rule", "rule_id": "po_balance", "reason": "x"}]})


def test_low_confidence_and_evidence_flags():
    cfg = build_config(read_config_files(CONFIG_DIR))
    cols = column_specs(cfg.erp_profiles["quickbooks_online"])
    rows = [{c["header"]: "" for c in cols}, {c["header"]: "" for c in cols}]
    missing = RuleResult(rule_id="required_fields", status="fail", severity="hold", explanation="", evidence={"missing": ["invoice_date"]}, run_no=1)
    meta = {"invoice_number": {"confidence": 0.78}, "lines": {"confidence": 0.5}, "total": {"confidence": 0.99}}
    f = {(x["row"], x["column"]): (x["kind"], x["reasons"]) for x in cell_flags(cfg, cols, rows, "line", [missing], meta, set())}
    assert f[(0, "Bill No")] == ("low_confidence", ["Read with 78% confidence"])
    assert f[(1, "Bill Date")] == ("rule", ["Missing required fields"])
    assert f[(0, "Line Amount")][0] == "low_confidence"  # whole-lines confidence marks every line cell
    # a reviewer-edited field is no longer flagged for low confidence
    edited = {(x["row"], x["column"]) for x in cell_flags(cfg, cols, rows, "line", [], meta, {"invoice_number"})}
    assert (0, "Bill No") not in edited


def test_bad_highlight_is_a_config_error():
    files = patch_yaml(read_config_files(CONFIG_DIR), "rules.yaml",
                       lambda d: d["rules"][0].__setitem__("highlight", ["Not A Path"]))
    with pytest.raises(ConfigError):
        build_config(files)



def test_plain_request_draft_keeps_the_reviewers_ask():
    from app.agents.comms import CommsAgent

    v = {"invoice_number": "DF-1", "recipient_name": "Delta", "currency": "USD", "total": "10.00", "signature": "AP"}
    _, body = CommsAgent._fallback("info", v, ["Reviewer: Send the budget approval", "No PO found."], ["a corrected copy of the invoice"])
    assert "- Send the budget approval\n- a corrected copy of the invoice" in body


async def test_grid_cannot_edit_bank_details(pipeline: Pipeline):
    await pipeline.start()
    await run(pipeline, "S02a_split_part1")
    cid = await run(pipeline, "S02b_split_part2")
    assert pipeline.orch is not None
    await pipeline.orch.submit_review(
        cid, "remark", None, "Rita (AP)", [{"op": "set_field", "path": "remit_bank.account_last4", "value": "9999"}]
    )
    await pipeline.orch.join()
    rec = events(pipeline, cid, "reconcile")[-1]
    assert rec.outcome == "needs_clarification" and "remit_bank.account_last4" in rec.output["question"]
    assert len(templates(pipeline, cid)) == 1


def test_callout_rules_must_exist():
    files = patch_yaml(read_config_files(CONFIG_DIR), "app.yaml",
                       lambda d: d["ui"]["review"].__setitem__("callout_rules", ["no_such_rule"]))
    with pytest.raises(ConfigError, match="callout_rules names unknown rule 'no_such_rule'"):
        build_config(files)
