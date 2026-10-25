"""PO matching (hybrid): find the PO, pair invoice lines to PO lines and GRNs; code non-PO invoices.

`match`: an explicit PO reference must exist, be open and belong to the vendor. Otherwise the vendor's open
POs are scored in code: amount closeness, LLM line similarity and a date window, weighted by
params.candidate_weights. A top score >= implied_min_score with a gap >= implied_min_gap to the next is an
implied (pre-linked) match. The LLM only judges line similarity and proposes line pairs; quantities, prices,
GRN quantities and balances are computed here.

`suggest_gl`: GL account per line from the LLM, restricted to gl_accounts (falls back to the vendor default).
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from typing import Any

from rapidfuzz import fuzz
from rapidfuzz.utils import default_process
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import po_ledger
from app.agents.base import Agent, AgentContext, AgentResult
from app.llm import sum_metrics, text_part
from app.llm_schemas import GLCodingOutput, POInferenceOutput
from app.models import GRN, Case, Document, GLAccount, POLine, PurchaseOrder, StageEvent, Vendor
from app.rules.engine import dec, fmt_money
from app.tenancy import require_owner, scoped


def _norm_po(v: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (v or "").upper())


def _invoice_amount(inv: dict[str, Any]) -> Decimal | None:
    sub = dec(inv.get("subtotal"))
    if sub is not None:
        return sub
    amounts = [dec(ln.get("amount")) for ln in inv.get("lines") or []]
    known = [a for a in amounts if a is not None]
    return sum(known, Decimal(0)) if amounts and len(known) == len(amounts) else dec(inv.get("total"))


def llm_context(ctx: AgentContext, s: Session) -> dict[str, Any]:
    """FakeLLM / cache keys: the invoice's file name and its number as extracted."""
    doc_id = ctx.state.get("extraction", {}).get("document_id")
    doc = s.get(Document, doc_id) if doc_id else None
    raw = ctx.state.get("extraction", {}).get("invoice") or {}
    return {"document": doc.filename if doc else None, "invoice_number": raw.get("invoice_number")}


def invoice_for_prompt(inv: dict[str, Any]) -> str:
    keep = ("vendor_name", "invoice_number", "invoice_date", "currency", "po_reference", "other_references",
            "lines", "subtotal", "tax_total", "total")
    return json.dumps({k: inv.get(k) for k in keep}, indent=1, default=str)


class POMatchingAgent(Agent):
    async def run(self, action: str, ctx: AgentContext) -> AgentResult:
        if action == "suggest_gl":
            return await self._suggest_gl(action, ctx)
        return await self._match(action, ctx)

    # ---------------- match
    async def _match(self, action: str, ctx: AgentContext) -> AgentResult:
        p = ctx.params
        inv: dict[str, Any] = ctx.state.get("normaliser", {}).get("invoice") or {}
        vendor_id = ctx.state.get("vendor", {}).get("vendor_id")
        reasoning: list[str] = []
        with ctx.session_factory() as s:
            case = s.get(Case, ctx.case_id)
            assert case is not None
            ref = inv.get("po_reference")
            explicit: PurchaseOrder | None = None
            explicit_issue: str | None = None
            if ref:
                explicit = next(
                    (
                        po
                        for po in s.scalars(scoped(select(PurchaseOrder), require_owner(s))).all()
                        if _norm_po(po.po_number) == _norm_po(ref)
                    ),
                    None,
                )
                if explicit is None:
                    explicit_issue = f"PO {ref} quoted on the invoice does not exist."
                elif explicit.status != "open":
                    explicit_issue = f"PO {explicit.po_number} quoted on the invoice is {explicit.status}."
                elif vendor_id is None:
                    explicit_issue = f"PO {explicit.po_number} quoted on the invoice can't be checked: the vendor is unknown."
                elif explicit.vendor_id != vendor_id:
                    explicit_issue = f"PO {explicit.po_number} quoted on the invoice belongs to a different vendor."
                if explicit_issue:
                    reasoning.append(explicit_issue + " Looking for another open PO of this vendor.")
                    explicit = None

            if explicit is not None:
                candidates = [explicit]
            elif vendor_id is not None:
                candidates = list(
                    s.scalars(scoped(
                        select(PurchaseOrder)
                        .where(PurchaseOrder.vendor_id == vendor_id, PurchaseOrder.status == "open")
                        .order_by(PurchaseOrder.po_number),
                        require_owner(s),
                    )).all()
                )
            else:
                candidates = []

            base: dict[str, Any] = {
                "match_type": "none",
                "po": None,
                "po_id": None,
                "score": None,
                "candidates": [],
                "lines": [],
                "explicit_issue": explicit_issue,
            }
            if not candidates:
                case.po_id = None
                s.commit()
                reasoning.append(
                    "No PO number on the invoice and the vendor has no open POs: treated as a non-PO invoice."
                    if not ref
                    else "No usable PO: treated as a non-PO invoice."
                )
                return AgentResult("no_po", base, reasoning, confidence=1.0)

            views = {po.po_number: po_view(s, po, ctx.case_id) for po in candidates}
            await ctx.emit(f"comparing the invoice with {len(candidates)} PO(s)")
            out, call = await self.call_llm(
                ctx,
                action,
                POInferenceOutput,
                [text_part("Return the judgement for every candidate PO.")],
                prompt_vars={
                    "invoice_json": invoice_for_prompt(inv),
                    "candidates_json": json.dumps(list(views.values()), indent=1, default=str),
                },
                context=llm_context(ctx, s),
            )
            assert isinstance(out, POInferenceOutput)
            judged = {j.po_number: j for j in out.candidates if j.po_number in views}

            amount = _invoice_amount(inv)
            inv_date = _day(inv.get("invoice_date"))
            weights = {k: float(v) for k, v in p["candidate_weights"].items()}
            scored: list[dict[str, Any]] = []
            for po in candidates:
                j = judged.get(po.po_number)
                remaining = Decimal(views[po.po_number]["remaining"])
                comp = {
                    "amount": _closeness(amount, remaining),
                    "lines": float(j.line_similarity) if j else 0.0,
                    "date": _date_score(inv_date, po.created_at.date() if po.created_at else None, int(p["date_window_days"])),
                }
                score = round(sum(weights[k] * comp[k] for k in weights), 4)
                scored.append(
                    {
                        "po_number": po.po_number,
                        "po_id": po.id,
                        "score": score,
                        "components": {k: round(v, 4) for k, v in comp.items()},
                        "reason": j.reason if j else "Not judged by the model.",
                    }
                )
            scored.sort(key=lambda c: c["score"], reverse=True)
            base["candidates"] = scored

            if explicit is not None:
                chosen, match_type, outcome = explicit, "explicit", "matched"
                reasoning.insert(0, f"Invoice quotes {explicit.po_number}: it exists, is open and belongs to the vendor.")
            else:
                top = scored[0]
                gap = top["score"] - (scored[1]["score"] if len(scored) > 1 else 0.0)
                listing = ", ".join(f"{c['po_number']} {c['score']:.2f}" for c in scored[:3])
                if top["score"] >= float(p["implied_min_score"]) and gap >= float(p["implied_min_gap"]):
                    chosen = next(po for po in candidates if po.po_number == top["po_number"])
                    match_type, outcome = "implied", "implied"
                    reasoning.append(
                        f"No PO number on the invoice. Scored the vendor's open POs ({listing}); "
                        f"{chosen.po_number} wins by {gap:.2f} and is pre-linked for review."
                    )
                    reasoning.append(top["reason"])
                else:
                    case.po_id = None
                    s.commit()
                    reasoning.append(
                        f"Scored the vendor's open POs ({listing}); none is a clear match "
                        f"(needs >= {p['implied_min_score']} and a gap >= {p['implied_min_gap']}). Treated as non-PO."
                    )
                    return AgentResult("no_po", base, reasoning, confidence=float(top["score"]))

            j = judged.get(chosen.po_number)
            pairs = {pr.invoice_line_index: pr.po_line_no for pr in (j.pairs if j else [])}
            lines = pair_lines(
                s, ctx.case_id, chosen, inv, pairs, bool(p.get("use_grn", True)), float(p["pair_min_similarity"])
            )
            case.po_id = chosen.id
            s.commit()

            view = views[chosen.po_number]
            score = next(c["score"] for c in scored if c["po_number"] == chosen.po_number)
            base.update(match_type=match_type, po=view, po_id=chosen.id, score=score, lines=lines)
            on_po = sum(1 for ln in lines if ln["po_line_no"] is not None)
            reasoning.append(
                f"{on_po} of {len(lines)} invoice line(s) map to {chosen.po_number} lines; "
                f"PO remaining {fmt_money(Decimal(view['remaining']))} of {fmt_money(chosen.amount)}."
            )
            if on_po < len(lines):
                off = [ln["description"] for ln in lines if ln["po_line_no"] is None]
                reasoning.append("Not on the PO: " + "; ".join(off) + ".")
            return AgentResult(outcome, base, reasoning[:5], confidence=score, metrics=sum_metrics([call]))

    # ---------------- suggest_gl
    async def _suggest_gl(self, action: str, ctx: AgentContext) -> AgentResult:
        inv: dict[str, Any] = ctx.state.get("normaliser", {}).get("invoice") or {}
        vendor_id = ctx.state.get("vendor", {}).get("vendor_id")
        with ctx.session_factory() as s:
            vendor = s.get(Vendor, vendor_id) if vendor_id else None
            accounts = {
                a.code: a for a in s.scalars(scoped(select(GLAccount).order_by(GLAccount.code), require_owner(s))).all()
            }
            history = self._history(s, vendor_id, ctx.case_id, int(ctx.params["gl_history_cases"])) if vendor_id else []
            await ctx.emit("suggesting GL coding")
            out, call = await self.call_llm(
                ctx,
                action,
                GLCodingOutput,
                [text_part("Return one suggestion per invoice line.")],
                prompt_vars={
                    "invoice_json": invoice_for_prompt(inv),
                    "vendor_category": (vendor.category if vendor else None) or "unknown",
                    "vendor_default_gl": vendor.default_gl if vendor else None,
                    "history_json": json.dumps(history),
                    "gl_accounts_json": json.dumps(
                        [{"code": a.code, "name": a.name, "category": a.category} for a in accounts.values()]
                    ),
                },
                context=llm_context(ctx, s),
            )
        assert isinstance(out, GLCodingOutput)
        n_lines = len(inv.get("lines") or [])
        by_line = {sug.line_index: sug for sug in out.lines if 0 <= sug.line_index < n_lines}
        fallback = vendor.default_gl if vendor and vendor.default_gl in accounts else None
        lines: list[dict[str, Any]] = []
        notes: list[str] = []
        for i in range(n_lines):
            sug = by_line.get(i)
            code = sug.gl_code if sug and sug.gl_code in accounts else fallback
            if sug and sug.gl_code not in accounts:
                notes.append(f"Line {i + 1}: model suggested unknown account {sug.gl_code}; used {code or 'none'}.")
            lines.append(
                {
                    "line_index": i,
                    "gl_code": code,
                    "gl_name": accounts[code].name if code else None,
                    "department": sug.department if sug else None,
                    "confidence": sug.confidence if sug and code == sug.gl_code else None,
                    "reason": sug.reason if sug else "No suggestion; vendor default used.",
                    "source": "llm" if sug and code == sug.gl_code else "vendor_default",
                }
            )
        codes = [ln["gl_code"] for ln in lines if ln["gl_code"]]
        primary = max(set(codes), key=codes.count) if codes else None
        data = {"gl": {"lines": lines, "primary": primary, "primary_name": accounts[primary].name if primary else None}}
        reasoning = [
            f"Suggested {primary} {accounts[primary].name} for {codes.count(primary)} of {n_lines} line(s)."
            if primary
            else "No GL account could be suggested."
        ]
        if lines and lines[0]["reason"]:
            reasoning.append(lines[0]["reason"])
        reasoning += notes[:2]
        conf = min((ln["confidence"] for ln in lines if ln["confidence"] is not None), default=None)
        return AgentResult("coded", data, reasoning, confidence=conf, metrics=sum_metrics([call]))

    @staticmethod
    def _history(s: Session, vendor_id: int, case_id: int, limit: int) -> list[dict[str, Any]]:
        """Recent approved non-PO codings for this vendor."""
        approved = s.scalars(scoped(
            select(Case.id)
            .where(Case.vendor_id == vendor_id, Case.id != case_id, Case.status.in_(["approved", "exported"]))
            .order_by(Case.id.desc())
            .limit(limit),
            require_owner(s),
        )).all()
        out: list[dict[str, Any]] = []
        for cid in approved:
            ev = s.scalars(
                select(StageEvent)
                .where(StageEvent.case_id == cid, StageEvent.action == "suggest_gl", StageEvent.status == "completed")
                .order_by(StageEvent.seq.desc())
            ).first()
            if ev and ev.output.get("gl"):
                out.append({"case_id": cid, "gl": ev.output["gl"].get("primary")})
        return out


def po_view(s: Session, po: PurchaseOrder, case_id: int) -> dict[str, Any]:
    lines = s.scalars(
        scoped(select(POLine).where(POLine.po_id == po.id).order_by(POLine.line_no), require_owner(s))
    ).all()
    committed = po_ledger.committed(s, po.id, exclude_case_id=case_id)
    return {
        "po_number": po.po_number,
        "id": po.id,
        "currency": po.currency,
        "amount": str(po.amount),
        "committed_by_others": str(committed),
        "remaining": str(po.amount - committed),
        "status": po.status,
        "created_at": po.created_at.date().isoformat() if po.created_at else None,
        "notes": po.notes,
        "lines": [
            {"line_no": ln.line_no, "description": ln.description, "qty": str(ln.qty), "unit_price": str(ln.unit_price), "gl_code": ln.gl_code}
            for ln in lines
        ],
    }

def pair_lines(
    s: Session,
    case_id: int,
    po: PurchaseOrder,
    inv: dict[str, Any],
    pairs: dict[int, int | None],
    use_grn: bool,
    min_similarity: float,
) -> list[dict[str, Any]]:
    """Validate the model's pairs against the PO (line exists, used once, descriptions at least
    `min_similarity` alike by rapidfuzz token_set_ratio) and add GRN quantities."""
    owner = require_owner(s)
    po_lines = {ln.line_no: ln for ln in s.scalars(scoped(select(POLine).where(POLine.po_id == po.id), owner)).all()}
    grn: dict[int, Decimal] = {}
    if use_grn:
        for g in s.scalars(scoped(select(GRN).where(GRN.po_id == po.id), owner)).all():
            grn[g.line_no] = grn.get(g.line_no, Decimal(0)) + g.qty_received
    before = po_ledger.committed_qty_by_line(s, po.id, exclude_case_id=case_id)
    used: set[int] = set()
    out: list[dict[str, Any]] = []
    for i, ln in enumerate(inv.get("lines") or []):
        no = pairs.get(i)
        pl = po_lines.get(no) if no is not None else None
        similar = pl is not None and fuzz.token_set_ratio(
            ln.get("description") or "", pl.description, processor=default_process
        ) >= min_similarity
        if pl is None or pl.line_no in used or not similar:
            pl = None
        else:
            used.add(pl.line_no)
        out.append(
            {
                "index": i,
                "description": ln.get("description"),
                "qty": ln.get("qty"),
                "unit_price": ln.get("unit_price"),
                "amount": ln.get("amount"),
                "po_line_no": pl.line_no if pl else None,
                "po_line_id": pl.id if pl else None,
                "po_description": pl.description if pl else None,
                "po_qty": str(pl.qty) if pl else None,
                "po_unit_price": str(pl.unit_price) if pl else None,
                "gl_code": pl.gl_code if pl else None,
                "grn_received": str(grn[pl.line_no]) if pl and pl.line_no in grn else None,
                "invoiced_before": str(before.get(pl.id, Decimal(0))) if pl else "0",
            }
        )
    return out


def _closeness(amount: Decimal | None, remaining: Decimal) -> float:
    if amount is None or amount <= 0 or remaining <= 0:
        return 0.0
    return float(max(Decimal(0), 1 - abs(amount - remaining) / max(amount, remaining)))


def _date_score(inv_date: date | None, po_date: date | None, window_days: int) -> float:
    """1 when the invoice is dated on or after the PO and within the window, else 0."""
    if inv_date is None or po_date is None:
        return 0.0
    return 1.0 if 0 <= (inv_date - po_date).days <= window_days else 0.0


def _day(v: Any) -> date | None:
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return None
