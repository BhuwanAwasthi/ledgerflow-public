"""ERP template (code): render the profile rows, validate required columns, version every change.

`generate_draft` stores template_version v1, v2... with a cell-level diff against the previous version.
`finalize` stamps the approver, freezes the latest version (a new final version if stamping changed a cell),
writes the import file to storage and turns the case's PO reservation into consumption.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import po_ledger
from app.agents.base import Agent, AgentContext, AgentResult
from app.case_state import effective
from app.config_loader import ErpProfile, ResolvedConfig
from app.exports.context import template_context
from app.exports.render import diff_rows, make_env, render_rows, render_string, to_bytes
from app.models import Case, PurchaseOrder, ReviewActionRow, TemplateVersion, utcnow
from app.rules.engine import fmt_money


def profile_for(cfg: ResolvedConfig, params: dict[str, Any]) -> ErpProfile:
    pid = params.get("erp_profile") or cfg.app.app.default_erp_profile
    if pid not in cfg.erp_profiles:
        raise ValueError(f"ERP profile '{pid}' is not in config/erp_profiles/")
    return cfg.erp_profiles[pid]


def latest_version(s: Session, case_id: int) -> TemplateVersion | None:
    return s.scalars(
        select(TemplateVersion).where(TemplateVersion.case_id == case_id).order_by(TemplateVersion.version.desc())
    ).first()


def file_name(profile: ErpProfile, case: Case, version: int) -> str:
    return render_string(make_env(profile), profile.filename_template, {"case": {"ref": case.ref, "id": case.id}, "version": version})


def _summary(tv: TemplateVersion, missing: list[dict[str, Any]]) -> dict[str, Any]:
    diff = tv.diff_from_prev or {}
    return {
        "version": tv.version,
        "erp_profile": tv.erp_profile,
        "status": tv.status,
        "rows": len(tv.rows),
        "valid": not missing,
        "missing": missing,
        "changed_cells": len(diff.get("changed") or []),
        "template_version_id": tv.id,
    }


class TemplateAgent(Agent):
    async def run(self, action: str, ctx: AgentContext) -> AgentResult:
        profile = profile_for(ctx.config, ctx.params)
        with ctx.session_factory() as s:
            case = s.get(Case, ctx.case_id)
            assert case is not None
            if action == "finalize":
                return self._finalize(s, ctx, case, profile)
            return self._draft(s, ctx, case, profile)

    def _draft(self, s: Session, ctx: AgentContext, case: Case, profile: ErpProfile) -> AgentResult:
        prev = latest_version(s, case.id)
        version = (prev.version if prev else 0) + 1
        rendered = render_rows(profile, template_context(s, case, ctx.state, version))
        diff = diff_rows(prev.rows if prev and prev.erp_profile == profile.id else None, rendered.rows)
        by_reviewer = prev is not None and bool(effective(ctx.state).get("reconciler"))
        tv = TemplateVersion(
            case_id=case.id,
            version=version,
            erp_profile=profile.id,
            rows=rendered.rows,
            status="draft",
            diff_from_prev=diff,
            created_by="reviewer" if by_reviewer else "agent",
        )
        s.add(tv)
        s.commit()
        reasoning = [f"Rendered {len(rendered.rows)} {profile.name} row(s) as v{version}."]
        if diff is not None:
            n = len(diff["changed"])
            cols = sorted({c["column"] for c in diff["changed"]})
            reasoning.append(
                f"{n} cell(s) changed since v{version - 1}" + (f" ({', '.join(cols)})." if cols else ".")
            )
        if rendered.missing:
            cols = sorted({m["column"] for m in rendered.missing})
            reasoning.append(f"Required column(s) empty: {', '.join(cols)}; fix before export.")
        else:
            reasoning.append("Every required column is filled.")
        return AgentResult("ok", {"template": _summary(tv, rendered.missing)}, reasoning, confidence=1.0)

    @staticmethod
    def _apply_po_amendments(s: Session, ctx: AgentContext, case: Case) -> list[str]:
        """On approval a reviewer's set_po_amount_note becomes the PO's master amount, with an audit row
        (amended_by, old/new amount, reason, case), like a verified bank change on vendor approval."""
        amendments = (effective(ctx.state).get("reconciler") or {}).get("po_amendments") or {}
        notes = [
            r for r in s.scalars(
                select(ReviewActionRow)
                .where(ReviewActionRow.case_id == case.id, ReviewActionRow.action == "override")
                .order_by(ReviewActionRow.id.desc())
            ).all()
            if (r.changeset or {}).get("op") == "set_po_amount_note"
        ]
        lines: list[str] = []
        for po_number, amount in amendments.items():
            po = s.scalars(select(PurchaseOrder).where(PurchaseOrder.po_number == po_number)).first()
            if po is None:
                continue
            new_amount = Decimal(str(amount))
            note = next((r for r in notes if Decimal(str(r.changeset["new_amount"])) == new_amount), None)
            old = po.amount
            row = po_ledger.amend(
                s, po, new_amount,
                amended_by=note.actor if note else (case.approved_by or "reviewer"),
                reason=(note.changeset or {}).get("reason") if note else None,
                case_id=case.id,
            )
            if row is not None:
                lines.append(
                    f"{po.po_number} amount amended from {fmt_money(old)} to {fmt_money(new_amount)} "
                    f"(by {row.amended_by}, recorded in the PO audit log)."
                )
        return lines

    def _finalize(self, s: Session, ctx: AgentContext, case: Case, profile: ErpProfile) -> AgentResult:
        approval = s.scalars(
            select(ReviewActionRow)
            .where(ReviewActionRow.case_id == case.id, ReviewActionRow.action == "approve")
            .order_by(ReviewActionRow.id.desc())
        ).first()
        case.approved_by = approval.actor if approval else "auto"
        case.approved_at = utcnow()
        prev = latest_version(s, case.id)
        version = prev.version if prev else 1
        rendered = render_rows(profile, template_context(s, case, ctx.state, version))
        reasoning: list[str] = []
        if prev is not None and prev.erp_profile == profile.id and prev.rows == rendered.rows:
            tv = prev
            reasoning.append(f"Froze v{tv.version} as the final template.")
        else:
            version = (prev.version if prev else 0) + 1
            rendered = render_rows(profile, template_context(s, case, ctx.state, version))
            tv = TemplateVersion(
                case_id=case.id,
                version=version,
                erp_profile=profile.id,
                rows=rendered.rows,
                diff_from_prev=diff_rows(prev.rows if prev else None, rendered.rows),
                created_by="agent",
            )
            s.add(tv)
            reasoning.append(f"Approval details changed the rows, so v{version} is the final template.")
        tv.status = "final"
        data, mime = to_bytes(profile, tv.rows)
        assert ctx.storage is not None, "template.finalize needs storage"
        tv.file_key = ctx.storage.put(f"exports/cases/{case.id}/{file_name(profile, case, tv.version)}", data, mime)
        po_ledger.consume(s, case.id)
        amended = self._apply_po_amendments(s, ctx, case)
        s.commit()
        reasoning.append(
            f"Approved by {case.approved_by}; import file {file_name(profile, case, tv.version)} is ready."
        )
        if case.po_id:
            reasoning.append("The PO reservation is now consumed.")
        reasoning += amended
        return AgentResult(
            "ok",
            {"template": _summary(tv, rendered.missing), "approved_by": case.approved_by},
            reasoning,
            confidence=1.0,
        )
