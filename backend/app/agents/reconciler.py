"""Reconciler (hybrid): a reviewer remark becomes a typed ChangeSet (LLM); code validates and applies it.

The LLM only proposes ops. Each op is checked against the case, the master data and the config (allowed
ops, rules that can never be waived, GL accounts, PO ownership). One invalid op or an `ask` means nothing
is applied and the case goes back to review with a question. Valid changes are stored under this agent's
state key (see app/case_state.py), overrides are logged as review_actions with the reviewer and reason,
reviewer field edits and `add_vendor_memory` hints become vendor memory, and the workflow reruns
policy -> decision -> template.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from rapidfuzz import fuzz
from rapidfuzz.utils import default_process
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import Agent, AgentContext, AgentResult
from app.agents.normaliser import normalise_invoice_number
from app.agents.po_matching import llm_context, pair_lines, po_view
from app.case_state import SEQ, effective
from app.llm import sum_metrics, text_part
from app.llm_schemas import (
    AddVendorMemoryOp,
    AskOp,
    ChangeSet,
    InvoiceExtraction,
    InvoiceLine,
    LinkPOOp,
    OverrideRuleOp,
    SetFieldOp,
    SetGLOp,
    SetPOAmountNoteOp,
    UnlinkPOOp,
)
from app.models import Case, GLAccount, POLine, PurchaseOrder, ReviewActionRow, TemplateVersion, VendorMemory
from app.rules.engine import fmt_money
from app.tenancy import require_owner, scoped

_PATH = re.compile(r"^(?P<field>[a-z_]+)(?:\[(?P<idx>\d+)\]\.(?P<sub>[a-z_]+)|\.(?P<bank>[a-z_0-9]+))?$")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_NOT_EDITABLE = {"confidence", "location", "lines", "remit_bank"}
_MONEY_FIELDS = {"subtotal", "tax_total", "shipping", "total"}
_LINE_NUMBERS = {"qty", "unit_price", "amount", "tax_rate"}


def edit_input(field: str, *, line: bool) -> str | None:
    """Input kind ('money' | 'number' | 'date' | 'text') for an invoice field `set_field` accepts, or None.
    `line=True` asks about a line field (`lines[i].<field>`). The review grid uses it to decide editable cells."""
    if line:
        if field not in InvoiceLine.model_fields:
            return None
        return ("money" if field in ("unit_price", "amount") else "number") if field in _LINE_NUMBERS else "text"
    if field not in InvoiceExtraction.model_fields or field in _NOT_EDITABLE:
        return None
    if field in _MONEY_FIELDS:
        return "money"
    return "date" if field in ("invoice_date", "due_date") else "text"


def grid_editable(path: str) -> bool:
    """A `set_field` path the review grid may send: an editable header field or `lines[i].<field>`."""
    m = _PATH.match(path.strip())
    if not m or m["bank"] is not None:
        return False
    if m["idx"] is not None:
        return m["field"] == "lines" and edit_input(m["sub"], line=True) is not None
    return edit_input(m["field"], line=False) is not None


def _norm_po(v: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (v or "").upper())


def _as_decimal_str(v: Any) -> str | None:
    if v is None or v == "":
        return None
    try:
        return str(Decimal(str(v).replace(",", "").replace("$", "").strip()))
    except InvalidOperation as e:
        raise ValueError(f"'{v}' is not a number") from e


class _Draft:
    """The cumulative reconciler state being built; written only when every op validated."""

    def __init__(self, prev: dict[str, Any], state: dict[str, Any]):
        eff = effective(state)
        self.invoice: dict[str, Any] = copy.deepcopy((eff.get("normaliser") or {}).get("invoice") or {})
        self.invoice_changed = prev.get("invoice") is not None
        self.po_matching: dict[str, Any] | None = copy.deepcopy(prev.get("po_matching"))
        self.pm: dict[str, Any] = copy.deepcopy(eff.get("po_matching") or {})
        self.gl_overrides: dict[str, Any] = dict(prev.get("gl_overrides") or {})
        self.rule_overrides: dict[str, str] = dict(prev.get("rule_overrides") or {})
        self.po_amendments: dict[str, str] = dict(prev.get("po_amendments") or {})
        self.changes: list[str] = []
        self.field_edits: list[tuple[str, Any, Any]] = []
        self.memory: list[str] = []
        self.overrides_log: list[tuple[str, dict[str, Any]]] = []
        self.case_po_id: int | None | bool = False  # False = unchanged
        self.based_on_seq: int | None = (state.get("normaliser") or {}).get(SEQ)

    def output(self) -> dict[str, Any]:
        return {
            "invoice": self.invoice if self.invoice_changed else None,
            "po_matching": self.po_matching,
            "gl_overrides": self.gl_overrides,
            "rule_overrides": self.rule_overrides,
            "po_amendments": self.po_amendments,
            "based_on_seq": self.based_on_seq,
        }


class ReconcilerAgent(Agent):
    async def run(self, action: str, ctx: AgentContext) -> AgentResult:
        with ctx.session_factory() as s:
            case = s.get(Case, ctx.case_id)
            assert case is not None
            remark = s.scalars(
                select(ReviewActionRow)
                .where(ReviewActionRow.case_id == case.id, ReviewActionRow.action == "remark")
                .order_by(ReviewActionRow.id.desc())
            ).first()
            grid = (remark.changeset or {}).get("source") == "grid" if remark is not None else False
            if remark is None or not (grid or (remark.text or "").strip()):
                return AgentResult(
                    "needs_clarification",
                    {"question": "There is no remark to apply. What should change?", "applied": False},
                    ["No reviewer remark found for this case."],
                )
            if grid:  # typed edits from the review grid: already ops, so no LLM call; same validation below
                ops = (remark.changeset or {}).get("ops") or []
                bad = [op.get("path") for op in ops if op.get("op") == "set_field" and not grid_editable(str(op.get("path")))]
                if bad:  # the grid only edits cells the workspace offers; bank details change via a verified bank letter
                    question = f"These fields can't be edited in the grid: {', '.join(map(str, bad))}."
                    remark.changeset = {**(remark.changeset or {}), "status": "needs_clarification", "errors": [question], "question": question}
                    s.commit()
                    return AgentResult("needs_clarification", {"question": question, "errors": [question], "applied": False},
                                       [f"Rejected grid edit(s): {question}"])
                out = ChangeSet.model_validate({"ops": ops, "summary": remark.text or f"{len(ops)} edit(s) in the review grid"})
                metrics: dict[str, Any] = {}
            else:
                await ctx.emit("reading the remark")
                out, call = await self.call_llm(
                    ctx,
                    action,
                    ChangeSet,
                    [text_part("Return the ChangeSet for this remark now.")],
                    prompt_vars={
                        "case_json": json.dumps(self._case_json(s, case, ctx.state), indent=1, default=str),
                        "reviewer": remark.actor,
                        "remark": remark.text,
                        "non_overridable": ctx.params.get("non_overridable_rules", []),
                    },
                    context={**llm_context(ctx, s), "remark": remark.text},
                )
                assert isinstance(out, ChangeSet)
                metrics = sum_metrics([call])
            draft = _Draft(effective(ctx.state).get("reconciler") or {}, ctx.state)
            errors, questions = self._validate_and_apply(s, ctx, case, out, draft, remark.text or "")
            changeset = {**out.model_dump(mode="json"), "source": "grid" if grid else "remark"}

            if errors or questions:
                question = " ".join(questions) if questions else (
                    "I could not apply this remark: " + "; ".join(errors) + ". Could you rephrase it?"
                )
                remark.changeset = {**changeset, "status": "needs_clarification", "errors": errors, "question": question}
                s.commit()
                return AgentResult(
                    "needs_clarification",
                    {"question": question, "errors": errors, "changeset": changeset, "applied": False},
                    [
                        f"{'Grid sent' if grid else 'Model proposed'} {len(out.ops)} change(s): {out.summary}",
                        *(f"Rejected: {e}." for e in errors[:3]),
                        f"Asked the reviewer: {question}",
                    ][:5],
                    metrics=metrics,
                )

            for text, op in draft.overrides_log:  # override log: who waived what, and why
                s.add(ReviewActionRow(case_id=case.id, actor=remark.actor, action="override", text=text, changeset=op))
            if draft.invoice_changed:  # keep the case columns (lists, duplicate checks) in step
                inv = draft.invoice
                case.invoice_number = inv.get("invoice_number")
                case.invoice_number_norm = inv.get("invoice_number_normalised")
                case.total = Decimal(inv["total"]) if inv.get("total") else None
                case.invoice_date = date.fromisoformat(inv["invoice_date"]) if inv.get("invoice_date") else None
            if draft.case_po_id is not False:
                case.po_id = draft.case_po_id or None
            if ctx.config.app.features.get("vendor_memory") and case.vendor_id:
                for path, old, new in draft.field_edits:
                    draft.memory.append(f"Reviewer corrected {path} from '{old}' to '{new}' on {case.ref}.")
                for hint in draft.memory:
                    s.add(VendorMemory(vendor_id=case.vendor_id, hint=hint, source_case_id=case.id))
            remark.changeset = {**changeset, "status": "applied", "changes": draft.changes}
            s.commit()

        return AgentResult(
            "changes_applied",
            {**draft.output(), "changes": draft.changes, "changeset": changeset, "summary": out.summary, "applied": True},
            [f"Applied {len(draft.changes)} change(s): {out.summary}", *draft.changes[:3],
             "Re-running policy, decision and template with the changes."][:5],
            confidence=1.0,
            metrics=metrics,
        )

    # ---------------- prompt context
    @staticmethod
    def _case_json(s: Session, case: Case, state: dict[str, Any]) -> dict[str, Any]:
        eff = effective(state)
        pm = eff.get("po_matching") or {}
        tv = s.scalars(
            select(TemplateVersion).where(TemplateVersion.case_id == case.id).order_by(TemplateVersion.version.desc())
        ).first()
        return {
            "invoice": (eff.get("normaliser") or {}).get("invoice"),
            "po_match": {
                "match_type": pm.get("match_type"),
                "po": {k: (pm.get("po") or {}).get(k) for k in ("po_number", "amount", "remaining", "lines")} if pm.get("po") else None,
                "lines": [
                    {k: ln.get(k) for k in ("index", "description", "amount", "po_line_no", "gl_code")}
                    for ln in pm.get("lines") or []
                ],
                "candidates": [c.get("po_number") for c in pm.get("candidates") or []],
            },
            "rule_results": [
                {k: r.get(k) for k in ("rule_id", "status", "severity", "explanation")}
                for r in (state.get("policy") or {}).get("results") or []
            ],
            "template_rows": tv.rows if tv else [],
        }

    # ---------------- validation + apply (in memory)
    def _validate_and_apply(
        self, s: Session, ctx: AgentContext, case: Case, cs: ChangeSet, d: _Draft, remark_text: str
    ) -> tuple[list[str], list[str]]:
        allowed = set(ctx.params.get("allowed_ops") or [])
        never = set(ctx.params.get("non_overridable_rules") or [])
        rule_ids = {r.id for r in ctx.config.rules.rules}
        errors: list[str] = []
        questions: list[str] = []
        if not cs.ops:
            questions.append("I didn't find a change to make in that remark. What should change?")
        for op in cs.ops:
            if op.op not in allowed:
                errors.append(f"operation '{op.op}' is not allowed by config")
                continue
            try:
                if isinstance(op, AskOp):
                    questions.append(op.question)
                elif isinstance(op, SetFieldOp):
                    self._set_field(op, d, ctx.config.agents["normaliser"].params["invoice_number_normalisation"])
                elif isinstance(op, SetGLOp):
                    self._set_gl(s, op, d)
                elif isinstance(op, LinkPOOp):
                    self._link_po(s, ctx, case, op, d)
                elif isinstance(op, UnlinkPOOp):
                    d.po_matching = {"match_type": "none", "po": None, "po_id": None, "score": None, "lines": [],
                                     "candidates": d.pm.get("candidates") or [], "explicit_issue": None, "linked_by": "reviewer"}
                    d.pm = d.po_matching
                    d.case_po_id = None
                    d.changes.append("Unlinked the PO: the invoice is now treated as non-PO.")
                elif isinstance(op, OverrideRuleOp):
                    if op.rule_id not in rule_ids:
                        raise ValueError(f"rule '{op.rule_id}' does not exist")
                    if op.rule_id in never:
                        raise ValueError(f"rule '{op.rule_id}' can never be waived by a remark")
                    d.rule_overrides[op.rule_id] = op.reason
                    d.overrides_log.append((f"Override {op.rule_id}: {op.reason}", op.model_dump(mode="json")))
                    d.changes.append(f"Waived rule {op.rule_id}: {op.reason}")
                elif isinstance(op, SetPOAmountNoteOp):
                    self._po_amount(op, d, remark_text)
                elif isinstance(op, AddVendorMemoryOp):
                    if not case.vendor_id:
                        raise ValueError("the vendor is unknown, so there is nowhere to store the hint")
                    d.memory.append(op.hint)
                    d.changes.append(f"Remembered for this vendor: {op.hint}")
            except ValueError as e:
                errors.append(f"{op.op}: {e}")
        return errors, questions

    @staticmethod
    def _set_field(op: SetFieldOp, d: _Draft, number_mode: str) -> None:
        m = _PATH.match(op.path.strip())
        if not m:
            raise ValueError(f"unknown field path '{op.path}'")
        field, idx, sub, bank = m["field"], m["idx"], m["sub"], m["bank"]
        if idx is not None:
            if field != "lines" or sub not in InvoiceLine.model_fields:
                raise ValueError(f"unknown field path '{op.path}'")
            lines = d.invoice.get("lines") or []
            i = int(idx)
            if not 0 <= i < len(lines):
                raise ValueError(f"line {i} does not exist (the invoice has {len(lines)} line(s))")
            value = _as_decimal_str(op.value) if sub in _LINE_NUMBERS else op.value
            old = lines[i].get(sub)
            lines[i][sub] = value
        elif bank is not None:
            if field != "remit_bank" or bank not in ("bank_name", "account_last4", "iban"):
                raise ValueError(f"unknown field path '{op.path}'")
            rb = d.invoice.setdefault("remit_bank", {}) or {}
            d.invoice["remit_bank"] = rb
            old, value = rb.get(bank), op.value
            rb[bank] = value
        else:
            if field not in InvoiceExtraction.model_fields or field in _NOT_EDITABLE:
                raise ValueError(f"field '{field}' can't be edited")
            value = _as_decimal_str(op.value) if field in _MONEY_FIELDS else op.value
            if field in ("invoice_date", "due_date") and value:
                try:
                    value = date.fromisoformat(str(value)).isoformat()
                except ValueError as e:
                    raise ValueError(f"{field} must be a date like 2026-10-05, got '{value}'") from e
            old = d.invoice.get(field)
            d.invoice[field] = value
            if field == "invoice_number":
                d.invoice["invoice_number_normalised"] = normalise_invoice_number(str(value) if value else None, number_mode)
        d.invoice_changed = True
        d.field_edits.append((op.path, old, value))
        d.changes.append(f"Set {op.path} from {old!r} to {value!r}.")

    @staticmethod
    def _set_gl(s: Session, op: SetGLOp, d: _Draft) -> None:
        n = len(d.invoice.get("lines") or [])
        if not 0 <= op.line_index < n:
            raise ValueError(f"line {op.line_index} does not exist (the invoice has {n} line(s))")
        acct = s.scalars(scoped(select(GLAccount).where(GLAccount.code == op.gl_code), require_owner(s))).first()
        if acct is None:
            raise ValueError(f"GL account {op.gl_code} is not in the chart of accounts")
        d.gl_overrides[str(op.line_index)] = {"gl_code": acct.code, "gl_name": acct.name, "department": op.department}
        desc = (d.invoice.get("lines") or [])[op.line_index].get("description") or f"line {op.line_index + 1}"
        d.changes.append(f"Coded '{desc}' to {acct.code} {acct.name}.")

    @staticmethod
    def _link_po(s: Session, ctx: AgentContext, case: Case, op: LinkPOOp, d: _Draft) -> None:
        pos = s.scalars(scoped(select(PurchaseOrder), require_owner(s))).all()
        po = next((p for p in pos if _norm_po(p.po_number) == _norm_po(op.po_number)), None)
        if po is None:
            raise ValueError(f"PO {op.po_number} does not exist")
        if po.status != "open":
            raise ValueError(f"PO {po.po_number} is {po.status}")
        if case.vendor_id is None or po.vendor_id != case.vendor_id:
            raise ValueError(f"PO {po.po_number} belongs to a different vendor")
        pm_params = ctx.config.agents["po_matching"].params
        po_lines = s.scalars(scoped(select(POLine).where(POLine.po_id == po.id), require_owner(s))).all()
        pairs: dict[int, int | None] = {}
        for i, ln in enumerate(d.invoice.get("lines") or []):
            scored = [
                (fuzz.token_set_ratio(ln.get("description") or "", pl.description, processor=default_process), pl.line_no)
                for pl in po_lines
            ]
            pairs[i] = max(scored)[1] if scored else None
        lines = pair_lines(s, case.id, po, d.invoice, pairs, bool(pm_params.get("use_grn", True)),
                           float(pm_params["pair_min_similarity"]))
        d.po_matching = {"match_type": "explicit", "po": po_view(s, po, case.id), "po_id": po.id, "score": None,
                         "lines": lines, "candidates": d.pm.get("candidates") or [], "explicit_issue": None,
                         "linked_by": "reviewer"}
        d.pm = d.po_matching
        d.case_po_id = po.id
        d.changes.append(f"Linked {po.po_number} (confirmed by the reviewer).")

    @staticmethod
    def _po_amount(op: SetPOAmountNoteOp, d: _Draft, remark_text: str) -> None:
        linked = (d.pm.get("po") or {}).get("po_number")
        if not linked or _norm_po(linked) != _norm_po(op.po_number):
            raise ValueError(f"PO {op.po_number} is not the PO linked to this invoice")
        if op.new_amount <= 0:
            raise ValueError("the amended PO amount must be positive")
        stated = {Decimal(n.replace(",", "")) for n in _NUMBER.findall(remark_text)}
        if op.new_amount not in stated:  # a money figure is never taken from the model alone
            raise ValueError(f"the amount {fmt_money(op.new_amount)} is not written in the remark")
        d.po_amendments[linked] = str(op.new_amount)
        d.overrides_log.append(
            (f"PO {linked} amount amended to {fmt_money(op.new_amount)} for this invoice: {op.reason}", op.model_dump(mode="json"))
        )
        d.changes.append(f"Raised {linked} to {fmt_money(op.new_amount)} for this invoice ({op.reason}).")
