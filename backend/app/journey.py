"""Submitter journey tracker: folds a case's stage_events into the courier-style steps of app.yaml `ui.journey`."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.config_loader import END_PREFIX, ResolvedConfig
from app.models import Case, CaseStatus, StageEvent, StageStatus

ACTIVE = {CaseStatus.received, CaseStatus.processing, CaseStatus.needs_review, CaseStatus.on_hold}


def _label(cfg: ResolvedConfig, stage_id: str | None) -> str:
    if not stage_id or stage_id.startswith(END_PREFIX):
        return ""
    try:
        return cfg.workflow.stage(stage_id).status_label or stage_id
    except KeyError:
        return stage_id


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _hold_draft(events: list[StageEvent]) -> dict[str, Any] | None:
    """The comms draft of the latest waiting stage (what an on-hold case waits for)."""
    for ev in reversed(events):
        if ev.status == StageStatus.waiting:
            draft = (ev.output or {}).get("draft")
            return draft if isinstance(draft, dict) else None
    return None


def build_journey(cfg: ResolvedConfig, case: Case, events: list[StageEvent]) -> dict[str, Any]:
    status = CaseStatus(case.status)
    completed: dict[str, datetime] = {}
    for ev in events:
        if ev.status in (StageStatus.completed, StageStatus.skipped) and ev.ended_at:
            completed.setdefault(ev.stage_id, ev.ended_at)
    current = case.current_stage if status in ACTIVE else None
    failed_stage = case.current_stage if status == CaseStatus.failed else None
    status_at = {CaseStatus.approved: case.approved_at, CaseStatus.exported: case.exported_at}

    steps: list[dict[str, Any]] = []
    for step in cfg.app.ui.journey:
        times = [completed[s] for s in step.stages if s in completed]
        reached = status.value in step.statuses
        if current in step.stages:
            state = {CaseStatus.on_hold: "waiting", CaseStatus.needs_review: "waiting"}.get(status, "current")
        elif failed_stage in step.stages:
            state = "failed"
        elif reached or times:
            state = "done"
        else:
            state = "pending"
        at = min(times) if times else None
        if reached and at is None:
            at = status_at.get(status) or case.updated_at
        steps.append({"label": step.label, "state": state, "at": at})

    # a step the case went past without entering (e.g. "In review" on an auto-approve) is skipped, not pending
    last_reached = max((i for i, st in enumerate(steps) if st["state"] != "pending"), default=-1)
    for st in steps[:last_reached]:
        if st["state"] == "pending":
            st["state"] = "skipped"

    draft = _hold_draft(events) if status == CaseStatus.on_hold else None
    needs = [str(n) for n in (draft or {}).get("needs_text") or []]
    last_label = _label(cfg, case.current_stage) or next(
        (_label(cfg, ev.stage_id) for ev in reversed(events) if ev.status == StageStatus.completed), ""
    )
    template = cfg.app.ui.status_text.get(status.value, "{label}")
    headline = template.format(label=last_label, needs=_join(needs) or "more information")
    return {
        "case_id": case.id,
        "ref": case.ref,
        "status": status.value,
        "status_label": last_label or None,
        "headline": headline,
        "needs": needs,
        "portal_url": (draft or {}).get("portal_link"),
        "steps": steps,
    }
