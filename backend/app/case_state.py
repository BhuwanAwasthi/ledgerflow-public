"""Case state: completed stage outputs folded per agent id, plus the reviewer's changes on top.

The reconciler never edits another agent's output (stage events are append-only). It stores its validated
ChangeSet effects under its own key, and `effective()` overlays them for the agents that run after it
(policy, decision, template) and for exports:
- `invoice`        the canonical invoice with `set_field` edits applied (replaces normaliser.invoice)
- `po_matching`    a replacement match after `link_po` / `unlink_po`
- `gl_overrides`   {line_index: {gl_code, department}} from `set_gl`
- `rule_overrides` / `po_amendments` are read by the policy engine directly
Overlays are stamped with the normaliser event they were based on (`based_on_seq`); after a re-extraction
(e.g. a corrected invoice from the portal) they are dropped and the reviewer reviews the new invoice.
"""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import StageEvent, StageStatus

SEQ = "_seq"


def fold_state(s: Session, case_id: int) -> dict[str, Any]:
    """Outputs of completed/waiting stages, merged per agent id in order."""
    state: dict[str, Any] = {}
    rows = s.scalars(
        select(StageEvent)
        .where(
            StageEvent.case_id == case_id,
            StageEvent.status.in_([StageStatus.completed, StageStatus.waiting]),
            StageEvent.agent_id.is_not(None),
        )
        .order_by(StageEvent.seq)
    ).all()
    for ev in rows:
        if ev.agent_id is None:
            continue
        state.setdefault(ev.agent_id, {}).update(ev.output or {})
        state[ev.agent_id][SEQ] = ev.seq  # which event wrote it last (overlay staleness)
    return state


def is_stale(state: dict[str, Any]) -> bool:
    """True when the invoice was extracted/normalised again after the reviewer's changes were made: those
    changes describe the old invoice (line indexes, amounts, PO), so they no longer apply."""
    rec = state.get("reconciler") or {}
    based_on = rec.get("based_on_seq")
    current = (state.get("normaliser") or {}).get(SEQ)
    return based_on is not None and current is not None and current > based_on


def effective(state: dict[str, Any]) -> dict[str, Any]:
    """A copy of `state` with the reconciler's invoice / PO-match overlays applied."""
    rec = state.get("reconciler") or {}
    if not rec:
        return state
    out = dict(state)
    if is_stale(state):
        out["reconciler"] = {}
        return out
    if rec.get("invoice") is not None:
        out["normaliser"] = {**(state.get("normaliser") or {}), "invoice": copy.deepcopy(rec["invoice"])}
    if rec.get("po_matching") is not None:
        pm = {**(state.get("po_matching") or {}), **copy.deepcopy(rec["po_matching"])}
        out["po_matching"] = pm
    return out


def gl_for_line(state: dict[str, Any], index: int) -> tuple[str | None, str | None]:
    """(gl_code, department) for an invoice line: reviewer override > PO line GL > suggest_gl > vendor default."""
    over = ((state.get("reconciler") or {}).get("gl_overrides") or {}).get(str(index))
    if over:
        return over.get("gl_code"), over.get("department")
    pm = state.get("po_matching") or {}
    for ln in pm.get("lines") or []:
        if ln.get("index") == index and ln.get("gl_code"):
            return ln["gl_code"], None
    for ln in (pm.get("gl") or {}).get("lines") or []:
        if ln.get("line_index") == index and ln.get("gl_code"):
            return ln["gl_code"], ln.get("department")
    vendor = (state.get("vendor") or {}).get("vendor") or {}
    return vendor.get("default_gl"), None
