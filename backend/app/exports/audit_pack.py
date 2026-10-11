"""Audit pack (reportlab): decision report, rule results, reviewer actions, template versions and the full
stage-event trail of one case, as a PDF a controller can file."""

from __future__ import annotations

import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Case, Document, ReviewActionRow, RuleResult, StageEvent, TemplateVersion, Vendor
from app.rules.engine import fmt_money

_STYLES = getSampleStyleSheet()
_GRID = TableStyle(
    [
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF1F5")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
    ]
)


_SMALL = _STYLES["BodyText"].clone("small", fontSize=7.5, leading=9)


def _p(text: object) -> Paragraph:
    return Paragraph(escape("" if text is None else str(text)), _SMALL)


def _table(header: list[str], rows: list[list[object]], widths: list[float]) -> Table:
    t = Table([[_p(h) for h in header], *[[_p(c) for c in r] for r in rows]], colWidths=[w * mm for w in widths], repeatRows=1)
    t.setStyle(_GRID)
    return t


def build_audit_pack(s: Session, case: Case, app_name: str) -> bytes:
    vendor = s.get(Vendor, case.vendor_id) if case.vendor_id else None
    last_run = s.scalar(select(func.max(RuleResult.run_no)).where(RuleResult.case_id == case.id))
    rules = s.scalars(
        select(RuleResult).where(RuleResult.case_id == case.id, RuleResult.run_no == last_run).order_by(RuleResult.id)
    ).all() if last_run else []
    events = s.scalars(select(StageEvent).where(StageEvent.case_id == case.id).order_by(StageEvent.seq)).all()
    actions = s.scalars(select(ReviewActionRow).where(ReviewActionRow.case_id == case.id).order_by(ReviewActionRow.id)).all()
    templates = s.scalars(select(TemplateVersion).where(TemplateVersion.case_id == case.id).order_by(TemplateVersion.version)).all()
    docs = s.scalars(select(Document).where(Document.case_id == case.id).order_by(Document.id)).all()

    story: list[object] = [
        Paragraph(escape(f"{app_name} audit pack: {case.ref}"), _STYLES["Title"]),
        _table(
            ["Field", "Value"],
            [
                ["Vendor", vendor.legal_name if vendor else "unknown"],
                ["Invoice", case.invoice_number or "(no number)"],
                ["Invoice date", case.invoice_date],
                ["Total", f"{case.currency or ''} {fmt_money(case.total)}"],
                ["Status", case.status],
                ["Decision", case.decision],
                ["Approved by", f"{case.approved_by or '-'} {case.approved_at.isoformat() if case.approved_at else ''}"],
                ["Config hash", case.config_hash],
                ["Documents", "; ".join(f"{d.filename} ({d.label or 'unlabelled'}, sha256 {d.sha256[:12]})" for d in docs)],
            ],
            [35, 140],
        ),
        Spacer(1, 4 * mm),
        Paragraph("Decision", _STYLES["Heading2"]),
        _p(case.decision_summary or "No decision yet."),
        Spacer(1, 3 * mm),
        Paragraph("Rule results", _STYLES["Heading2"]),
        _table(["Rule", "Status", "Severity", "Explanation"], [[r.rule_id, r.status, r.severity, r.explanation] for r in rules], [35, 15, 17, 108]),
        Spacer(1, 3 * mm),
        Paragraph("Reviewer actions", _STYLES["Heading2"]),
        _table(["When", "Who", "Action", "Text"], [[a.created_at.strftime("%Y-%m-%d %H:%M"), a.actor, a.action, a.text or ""] for a in actions] or [["-", "-", "-", "none"]], [28, 30, 20, 97]),
        Spacer(1, 3 * mm),
        Paragraph("Template versions", _STYLES["Heading2"]),
        _table(
            ["Version", "Profile", "Status", "Rows", "By", "Cells changed"],
            [[f"v{t.version}", t.erp_profile, t.status, len(t.rows), t.created_by, len((t.diff_from_prev or {}).get("changed") or [])] for t in templates]
            or [["-", "-", "-", "-", "-", "-"]],
            [18, 40, 20, 15, 25, 57],
        ),
        Spacer(1, 3 * mm),
        Paragraph("Stage events", _STYLES["Heading2"]),
        _table(
            ["#", "Run", "Stage", "Status", "Outcome", "Reasoning", "ms"],
            [
                [e.seq, e.run_no, e.stage_id, e.status, e.outcome or "", " ".join(e.reasoning or [])[:400], (e.metrics or {}).get("duration_ms", "")]
                for e in events
            ],
            [8, 9, 25, 17, 22, 82, 12],
        ),
    ]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm,
                            title=f"Audit pack {case.ref}", invariant=True)
    doc.build(story)
    return buf.getvalue()
