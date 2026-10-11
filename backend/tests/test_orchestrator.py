from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config_loader import read_config_files
from app.models import Case, CaseStatus, ReviewActionRow, StageEvent
from app.orchestrator import OrchestratorError
from tests.conftest import CONFIG_DIR, Harness, patch_yaml

HAPPY_PATH = [
    "intake",
    "classify",
    "extract",
    "normalise",
    "vendor_check",
    "po_match",
    "policy",
    "decide",
    "template_final",
    "finalize",
    "notify_approved",
]


def events(h: Harness, case_id: int) -> list[StageEvent]:
    with h.sf() as s:
        return list(
            s.scalars(select(StageEvent).where(StageEvent.case_id == case_id).order_by(StageEvent.seq))
        )


def path(evts: list[StageEvent], statuses=("completed", "skipped")) -> list[str]:
    return [e.stage_id for e in evts if e.status in statuses]


async def test_stub_run_reaches_end_approved(harness: Harness):
    orch = await harness.start()
    case_id = harness.new_case()
    orch.enqueue(case_id)
    await orch.join()

    case = harness.case(case_id)
    assert case.status == CaseStatus.approved
    assert case.current_stage == "end:approved"
    assert case.config_hash == harness.cfg.hash
    evts = events(harness, case_id)
    assert path(evts) == HAPPY_PATH
    # every stage writes started -> completed, with reasoning and metrics
    assert [e.status for e in evts] == ["started", "completed"] * len(HAPPY_PATH)
    assert [e.seq for e in evts] == list(range(1, len(evts) + 1))
    done = [e for e in evts if e.status == "completed"]
    assert all(e.reasoning and "latency_ms" in e.metrics and "duration_ms" in e.metrics for e in done)


async def test_events_are_published_on_the_bus(harness: Harness):
    orch = await harness.start()
    case_id = harness.new_case()
    q = orch.bus.subscribe(case_id)
    orch.enqueue(case_id)
    await orch.join()
    msgs = []
    while not q.empty():
        msgs.append(q.get_nowait())
    kinds = {m["event"] for m in msgs}
    assert {"stage_event", "case_updated", "progress"} <= kinds
    assert msgs[-1] == {"event": "case_updated", "data": msgs[-1]["data"]}
    assert msgs[-1]["data"]["status"] == "approved"


async def test_needs_review_pauses_then_completes_after_approve(harness: Harness):
    orch = await harness.start({"decide": ["needs_review"]})
    case_id = harness.new_case()
    orch.enqueue(case_id)
    await orch.join()

    case = harness.case(case_id)
    assert (case.status, case.current_stage) == (CaseStatus.needs_review, "review")
    assert case.sla_due_at is not None
    last = events(harness, case_id)[-1]
    assert (last.stage_id, last.status) == ("review", "waiting")

    await orch.submit_review(case_id, "approve", None, "Rita Reviewer")
    await orch.join()
    case = harness.case(case_id)
    assert case.status == CaseStatus.approved
    tail = path(events(harness, case_id))[-4:]
    assert tail == ["template_draft", "review", "finalize", "notify_approved"]
    review_done = next(
        e for e in events(harness, case_id) if e.stage_id == "review" and e.status == "completed"
    )
    assert review_done.outcome == "approve"
    assert review_done.run_no == 2


async def test_remark_loops_through_reconcile_and_sticky_review(harness: Harness):
    # first decision needs review; after the remark the decision would auto-approve,
    # but sticky_review routes it back to template_draft -> review
    orch = await harness.start({"decide": ["needs_review", "auto_approve"]})
    case_id = harness.new_case()
    orch.enqueue(case_id)
    await orch.join()
    assert harness.case(case_id).status == CaseStatus.needs_review

    await orch.submit_review(case_id, "remark", "PO was amended to $10,500", "Rita Reviewer")
    await orch.join()
    case = harness.case(case_id)
    assert (case.status, case.current_stage) == (CaseStatus.needs_review, "review")
    run2 = [e for e in events(harness, case_id) if e.run_no == 2]
    assert path(run2) == ["review", "reconcile", "policy", "decide", "template_draft"]
    assert run2[-1].stage_id == "review" and run2[-1].status == "waiting"
    decide = next(e for e in run2 if e.stage_id == "decide" and e.status == "completed")
    assert decide.outcome == "auto_approve"
    assert any("Sticky review" in r for r in decide.reasoning)
    assert "template_final" not in path(run2)

    await orch.submit_review(case_id, "approve", None, "Rita Reviewer")
    await orch.join()
    assert harness.case(case_id).status == CaseStatus.approved
    with harness.sf() as s:
        acts = [
            a.action for a in s.scalars(select(ReviewActionRow).where(ReviewActionRow.case_id == case_id))
        ]
    assert acts == ["remark", "approve"]


async def test_wait_for_pauses_and_resumes_on_fire_event(harness: Harness):
    orch = await harness.start({"vendor_check": ["new_vendor", "approved_vendor"]})
    case_id = harness.new_case()
    orch.enqueue(case_id)
    await orch.join()

    case = harness.case(case_id)
    assert (case.status, case.current_stage) == (CaseStatus.on_hold, "await_vendor")
    last = events(harness, case_id)[-1]
    assert (last.stage_id, last.status, last.outcome) == ("await_vendor", "waiting", "drafted")
    assert last.input_summary == {"waiting_for": "vendor_approved"}

    assert await orch.fire_event(case_id, "info_received") is False  # not what it waits for
    assert harness.case(case_id).status == CaseStatus.on_hold

    assert await orch.fire_event(case_id, "vendor_approved") is True
    await orch.join()
    assert harness.case(case_id).status == CaseStatus.approved
    run2 = path([e for e in events(harness, case_id) if e.run_no == 2])
    assert run2[:2] == ["await_vendor", "vendor_check"]
    assert run2[-1] == "notify_approved"


async def test_request_info_holds_until_info_received(harness: Harness):
    orch = await harness.start({"decide": ["needs_review", "auto_approve"]})
    case_id = harness.new_case()
    orch.enqueue(case_id)
    await orch.join()
    await orch.submit_review(case_id, "request_info", "Need the delivery note", "Rita Reviewer")
    await orch.join()
    assert (harness.case(case_id).status, harness.case(case_id).current_stage) == (CaseStatus.on_hold, "hold")
    assert await orch.fire_event(case_id, "info_received")
    await orch.join()
    # resumes at extract; the case was reviewed, so auto_approve is sticky -> review again
    case = harness.case(case_id)
    assert (case.status, case.current_stage) == (CaseStatus.needs_review, "review")


async def test_retry_then_success(harness: Harness):
    orch = await harness.start({"extract": ["!error", "ok"]})  # extract stage has retries: 1
    case_id = harness.new_case()
    orch.enqueue(case_id)
    await orch.join()
    assert harness.case(case_id).status == CaseStatus.approved
    ext = next(e for e in events(harness, case_id) if e.stage_id == "extract" and e.status == "completed")
    assert ext.metrics["attempts"] == 2
    assert "Attempt 1 of 2 failed" in ext.reasoning[0]


async def test_failure_after_retries_marks_case_failed(harness: Harness):
    orch = await harness.start({"intake": ["!error"]})  # intake retries: 0
    case_id = harness.new_case()
    orch.enqueue(case_id)
    await orch.join()
    case = harness.case(case_id)
    assert (case.status, case.current_stage) == (CaseStatus.failed, "intake")
    last = events(harness, case_id)[-1]
    assert last.status == "failed" and "failed on purpose" in last.output["error"]


async def test_timeout_fails_the_stage(harness: Harness):
    files = patch_yaml(
        read_config_files(CONFIG_DIR), "agents/intake.yaml", lambda d: d.update(timeout_s=0.05)
    )
    orch = await harness.start({"intake": ["!sleep:2"]}, files=files)
    case_id = harness.new_case()
    orch.enqueue(case_id)
    await orch.join()
    last = events(harness, case_id)[-1]
    assert last.status == "failed" and "timed out after 0.05s" in last.output["error"]


async def test_disabled_agent_is_skipped(harness: Harness):
    files = patch_yaml(
        read_config_files(CONFIG_DIR), "agents/po_matching.yaml", lambda d: d.update(enabled=False)
    )
    files = patch_yaml(
        files,
        "workflow.yaml",
        lambda d: next(s for s in d["stages"] if s["id"] == "gl_coding")["next"].update(skipped="policy"),
    )
    orch = await harness.start(files=files)
    case_id = harness.new_case()
    orch.enqueue(case_id)
    await orch.join()
    evts = events(harness, case_id)
    po = [e for e in evts if e.stage_id == "po_match"]
    assert [(e.status, e.outcome) for e in po] == [("skipped", "skipped")]
    assert harness.case(case_id).status == CaseStatus.approved


async def test_invalid_outcome_from_agent_fails_stage(harness: Harness):
    orch = await harness.start({"intake": ["banana"]})
    case_id = harness.new_case()
    orch.enqueue(case_id)
    await orch.join()
    last = events(harness, case_id)[-1]
    assert last.status == "failed" and "not one of" in last.output["error"]


async def test_startup_resume_restarts_processing_cases(harness: Harness):
    orch = await harness.start()
    case_id = harness.new_case()
    with harness.sf() as s:  # simulate a crash while the case was in `policy`
        case = s.get(Case, case_id)
        case.status, case.current_stage = CaseStatus.processing, "policy"
        s.commit()
    received = harness.new_case()  # never started
    assert await orch.resume_pending() == 2
    await orch.join()
    assert harness.case(case_id).status == CaseStatus.approved
    assert path(events(harness, case_id))[0] == "policy"
    assert harness.case(received).status == CaseStatus.approved


async def test_review_on_case_that_is_not_waiting_is_rejected(harness: Harness):
    orch = await harness.start()
    case_id = harness.new_case()
    orch.enqueue(case_id)
    await orch.join()
    with pytest.raises(OrchestratorError, match="not waiting for review"):
        await orch.submit_review(case_id, "approve", None, "x")


async def test_case_runs_on_its_config_snapshot(harness: Harness):
    """A config change after the case was created does not change how that case runs."""
    orch = await harness.start()
    case_id = harness.new_case()
    snapshot_hash = harness.case(case_id).config_hash
    changed = patch_yaml(
        read_config_files(CONFIG_DIR), "agents/po_matching.yaml", lambda d: d.update(enabled=False)
    )
    changed = patch_yaml(
        changed,
        "workflow.yaml",
        lambda d: next(s for s in d["stages"] if s["id"] == "gl_coding")["next"].update(skipped="policy"),
    )
    from app.config_loader import build_config

    new_cfg = build_config(changed)
    orch.config_provider = lambda: new_cfg
    orch.enqueue(case_id)
    await orch.join()
    assert harness.case(case_id).config_hash == snapshot_hash
    assert all(e.status != "skipped" for e in events(harness, case_id))


async def _restart(harness: Harness, outcomes: dict[str, list[str]]):
    """Stop the workers (jobs still queued are lost, like a process restart) and start a fresh orchestrator."""
    await harness.orch.stop()
    return await harness.start(outcomes)


async def test_review_accepted_before_restart_is_not_lost(harness: Harness):
    orch = await harness.start({"decide": ["needs_review"]})
    case_id = harness.new_case()
    orch.enqueue(case_id)
    await orch.join()
    await orch.stop()  # the job queued by the review below never runs in this process
    await orch.submit_review(case_id, "approve", None, "Rita Reviewer")
    case = harness.case(case_id)
    assert (case.status, case.current_stage) == (CaseStatus.processing, "finalize")

    orch2 = await _restart(harness, {"decide": ["needs_review"]})
    assert await orch2.resume_pending() == 1
    await orch2.join()
    assert harness.case(case_id).status == CaseStatus.approved
    assert path(events(harness, case_id))[-3:] == ["review", "finalize", "notify_approved"]


async def test_event_fired_before_restart_is_not_lost(harness: Harness):
    outcomes = {"vendor_check": ["new_vendor", "approved_vendor"]}
    orch = await harness.start(outcomes)
    case_id = harness.new_case()
    orch.enqueue(case_id)
    await orch.join()
    await orch.stop()
    assert await orch.fire_event(case_id, "vendor_approved")
    assert harness.case(case_id).current_stage == "vendor_check"

    orch2 = await _restart(harness, {"vendor_check": ["approved_vendor"]})
    await orch2.resume_pending()
    await orch2.join()
    assert harness.case(case_id).status == CaseStatus.approved
    drafts = [e for e in events(harness, case_id) if e.stage_id == "await_vendor" and e.status == "started"]
    assert len(drafts) == 1  # the draft_request action did not run twice


async def test_orchestrator_crash_writes_a_failed_event(harness: Harness):
    files = patch_yaml(
        read_config_files(CONFIG_DIR), "app.yaml", lambda d: d["limits"].update(max_stages_per_run=3)
    )
    orch = await harness.start(files=files)
    case_id = harness.new_case()
    orch.enqueue(case_id)
    await orch.join()
    assert harness.case(case_id).status == CaseStatus.failed
    last = events(harness, case_id)[-1]
    assert last.status == "failed" and "more than 3 stages" in last.output["error"]
