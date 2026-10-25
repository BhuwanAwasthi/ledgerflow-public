"""Review workspace view (SPEC §10.6): what the HITL screen needs beyond the case detail.

- `columns`: the ERP profile's columns with what each cell reads (`inv.<field>`, `line.<field>`, `po`, `vendor`)
  and, when the cell is a single invoice value the reconciler accepts, how to edit it (`set_field` / `set_gl`)
- `cells`: amber flags per template cell, from low extraction confidence (`app.yaml ui.review`) and from failed
  rules (`rules.yaml highlight`, line fields scoped to the lines named in the rule's evidence)
- `invoice`: the effective invoice (reviewer edits applied), so a grid edit starts from the raw value
- `match` / `po_balance`: invoice lines vs PO lines vs GRN, and the PO balance live from the ledger
- `draft`: the latest comms draft (ask-vendor modal); `duplicates`: earlier cases a failed rule points at
Nothing here decides anything: it only reads stage outputs, rule results and config.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import po_ledger
from app.agents.reconciler import edit_input
from app.case_state import effective, fold_state, is_stale
from app.config_loader import ErpProfile, ResolvedConfig
from app.exports.service import get_profile
from app.models import Case, Extraction, POConsumption, PurchaseOrder, RuleResult, StageEvent, TemplateVersion
from app.rules.engine import dec
from app.tenancy import scoped

_EXPR = re.compile(r"\{\{\s*(inv|line|vendor|po)\b(?:\.([a-z_]+))?[^}]*\}\}")
_ONE = re.compile(r"^\s*\{\{[^}]*\}\}\s*$")
LINE = "lines."


def column_specs(profile: ErpProfile) -> list[dict[str, Any]]:
    """Each column's bindings (`invoice_number`, `lines.amount`, `po`, `vendor`) and edit spec, if editable."""
    out = []
    for col in profile.columns:
        binds: list[str] = []
        for obj, attr in _EXPR.findall(col.value):
            b = obj if obj in ("po", "vendor") else (LINE + attr if obj == "line" else attr)
            if b and b not in binds and not b.endswith("."):
                binds.append(b)
        edit = None
        if _ONE.match(col.value) and len(binds) == 1:
            b = binds[0]
            if b in (LINE + "gl_code", LINE + "department"):
                edit = {"op": "set_gl", "field": b[len(LINE):], "path": None, "input": "text"}
            elif b.startswith(LINE):
                kind = edit_input(b[len(LINE):], line=True)
                if kind:
                    edit = {"op": "set_field", "field": b[len(LINE):], "path": f"lines[{{i}}].{b[len(LINE):]}", "input": kind}
            elif b not in ("po", "vendor"):
                kind = edit_input(b, line=False)
                if kind:
                    edit = {"op": "set_field", "field": b, "path": b, "input": kind}
        out.append({"header": col.header, "required": col.required, "binds": binds, "edit": edit})
    return out


def _line_indexes(evidence: dict[str, Any]) -> list[int] | None:
    """Lines a rule's evidence names (line_indexes, or items[].line_index); None = not line-scoped."""
    if isinstance(evidence.get("line_indexes"), list):
        return [int(i) for i in evidence["line_indexes"]]
    items = evidence.get("items")
    if isinstance(items, list):
        idx = [int(it["line_index"]) for it in items if isinstance(it, dict) and it.get("line_index") is not None]
        return idx or None
    return None


def cell_flags(
    cfg: ResolvedConfig,
    columns: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    grain: str,
    rule_results: list[RuleResult],
    field_meta: dict[str, Any],
    edited: set[str],
) -> list[dict[str, Any]]:
    """[{row, column, kind: low_confidence|rule, reasons[]}] for template cells to show amber."""
    threshold = cfg.app.ui.review.low_confidence_below
    rules = {r.id: r for r in cfg.rules.rules}
    # target binding -> [(reason, line indexes or None for every row)]
    targets: dict[str, list[tuple[str, str, list[int] | None]]] = {}
    for rr in rule_results:
        rule = rules.get(rr.rule_id)
        if rr.status != "fail" or rule is None:
            continue
        label = rule.fail_label or rule.name
        lines = _line_indexes(rr.evidence or {})
        for h in rule.highlight:
            names = [h]
            if h.startswith("evidence:"):
                names = [str(x) for x in (rr.evidence or {}).get(h.split(":", 1)[1]) or []]
            for name in names:
                targets.setdefault(name, []).append(("rule", label, lines if name.startswith(LINE) else None))
    for field, meta in (field_meta or {}).items():
        conf = (meta or {}).get("confidence")
        if conf is None or conf >= threshold or field in edited:
            continue
        name = LINE + "*" if field == "lines" else field
        targets.setdefault(name, []).append(("low_confidence", f"Read with {round(conf * 100)}% confidence", None))

    out: list[dict[str, Any]] = []
    for r in range(len(rows)):
        row_lines = {r} if grain == "line" else None  # header grain: one row stands for every line
        for col in columns:
            hits: list[tuple[str, str]] = []
            for b in col["binds"]:
                for name in (b, LINE + "*") if b.startswith(LINE) else (b,):
                    for kind, reason, lines in targets.get(name, []):
                        if lines is None or row_lines is None or row_lines & set(lines):
                            hits.append((kind, reason))
            if hits:
                kind = "rule" if any(k == "rule" for k, _ in hits) else "low_confidence"
                out.append({"row": r, "column": col["header"], "kind": kind,
                            "reasons": list(dict.fromkeys(reason for _, reason in hits))})
    return out


def _edited_fields(state: dict[str, Any]) -> set[str]:
    """Header fields whose effective value differs from the normaliser's (the reviewer set them)."""
    rec = state.get("reconciler") or {}
    if not rec.get("invoice") or is_stale(state):
        return set()
    base = (state.get("normaliser") or {}).get("invoice") or {}
    return {k for k, v in rec["invoice"].items() if k != "lines" and base.get(k) != v}


def _po_balance(s: Session, case: Case, state: dict[str, Any], inv: dict[str, Any]) -> dict[str, Any] | None:
    pm = state.get("po_matching") or {}
    po = s.scalars(scoped(select(PurchaseOrder).where(PurchaseOrder.id == pm["po_id"]), case.owner_id)).first() if pm.get("po_id") else None
    if po is None:
        return None
    rec = {} if is_stale(state) else (state.get("reconciler") or {})
    amended = dec((rec.get("po_amendments") or {}).get(po.po_number))
    amount = amended if amended is not None and amended != po.amount else po.amount
    others = po_ledger.committed(s, po.id, exclude_case_id=case.id)
    subtotal, total = dec(inv.get("subtotal")), dec(inv.get("total"))
    this = subtotal if subtotal is not None else (total if total is not None else Decimal(0))
    states = set(s.scalars(select(POConsumption.state).where(POConsumption.case_id == case.id,
                                                            POConsumption.po_id == po.id)).all())
    return {
        "po_number": po.po_number,
        "currency": po.currency,
        "master_amount": po.amount,
        "amount": amount,
        "amended": amount != po.amount,
        "committed_by_others": others,
        "this_invoice": this,
        "remaining_before": amount - others,
        "remaining_after": amount - others - this,
        "this_state": "consumed" if "consumed" in states else ("reserved" if "reserved" in states else None),
    }


def build_workspace(s: Session, cfg: ResolvedConfig, case: Case) -> dict[str, Any]:
    raw = fold_state(s, case.id)
    state = effective(raw)
    inv: dict[str, Any] = (state.get("normaliser") or {}).get("invoice") or {}
    tv = s.scalars(
        select(TemplateVersion).where(TemplateVersion.case_id == case.id).order_by(TemplateVersion.version.desc())
    ).first()
    profile_id = tv.erp_profile if tv else None
    profile = get_profile(cfg, profile_id) if profile_id in cfg.erp_profiles or profile_id is None else None
    columns = column_specs(profile) if profile else [
        {"header": h, "required": False, "binds": [], "edit": None} for h in (tv.rows[0] if tv and tv.rows else {})
    ]
    last_run = max((r.run_no for r in s.scalars(select(RuleResult).where(RuleResult.case_id == case.id))), default=None)
    results = list(s.scalars(select(RuleResult).where(RuleResult.case_id == case.id, RuleResult.run_no == last_run)))
    ext = s.scalars(select(Extraction).where(Extraction.case_id == case.id).order_by(Extraction.version.desc())).first()
    cells = cell_flags(cfg, columns, tv.rows if tv else [], profile.grain if profile else "line", results,
                       ext.field_meta if ext else {}, _edited_fields(raw)) if tv else []

    pm = state.get("po_matching") or {}
    draft_ev = s.scalars(
        select(StageEvent)
        .where(StageEvent.case_id == case.id, StageEvent.agent_id == "comms", StageEvent.status.in_(["completed", "waiting"]))
        .order_by(StageEvent.seq.desc())
    ).all()
    draft = next(((ev.output or {}).get("draft") for ev in draft_ev if (ev.output or {}).get("draft")), None)
    duplicates = []
    for rr in results:
        other = (rr.evidence or {}).get("other_case_id")
        if rr.status == "fail" and other:
            oc = s.scalars(scoped(select(Case).where(Case.id == int(other)), case.owner_id)).first()
            duplicates.append({"rule_id": rr.rule_id, "case_id": int(other), "ref": oc.ref if oc else None,
                               "explanation": rr.explanation})
    return {
        "case_id": case.id,
        "erp_profile": profile_id,
        "grain": profile.grain if profile else None,
        "columns": columns,
        "cells": cells,
        "invoice": inv,
        "match": {k: pm.get(k) for k in ("match_type", "po", "lines", "candidates", "explicit_issue", "linked_by", "score", "gl")}
        if pm else None,
        "po_balance": _po_balance(s, case, state, inv),
        "draft": draft,
        "duplicates": duplicates,
        "reviewable": case.status == "needs_review",
    }
