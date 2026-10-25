"""Dashboard KPIs and series (SPEC §10.3). Read-only aggregation over cases, stage_events and rule_results."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config_loader import ResolvedConfig
from app.models import Case, CaseStatus, ReviewActionRow, RuleResult, StageEvent, StageStatus

EXCLUDED = (CaseStatus.split_bundle,)  # a bundle parent is not an invoice; its children are counted


class RangeError(ValueError):
    pass


def parse_range(cfg: ResolvedConfig, value: str | None) -> tuple[str, int]:
    dash = cfg.app.ui.dashboard
    value = value or dash.default_range
    if value not in dash.ranges:
        raise RangeError(f"range '{value}' is not one of {', '.join(dash.ranges)}")
    return value, int(value[:-1])


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def metrics(s: Session, cfg: ResolvedConfig, range_value: str | None, now: datetime | None = None) -> dict[str, Any]:
    dash = cfg.app.ui.dashboard
    label, days = parse_range(cfg, range_value)
    now = now or datetime.now(UTC)
    start_day = (now - timedelta(days=days - 1)).date()
    since = datetime.combine(start_day, datetime.min.time(), tzinfo=UTC)

    cases = s.scalars(
        select(Case).where(Case.created_at >= since, Case.status.not_in(EXCLUDED)).order_by(Case.id)
    ).all()
    ids = [c.id for c in cases]
    reviewed = set(
        s.scalars(select(ReviewActionRow.case_id).where(ReviewActionRow.case_id.in_(ids)).distinct()).all()
    ) if ids else set()

    events = s.scalars(select(StageEvent).where(StageEvent.case_id.in_(ids))).all() if ids else []
    first_decision: dict[int, datetime] = {}
    cost = Decimal(0)
    tokens = 0
    for ev in events:
        m = ev.metrics or {}
        cost += Decimal(str(m.get("cost_usd") or 0))
        tokens += int(m.get("tokens_in") or 0) + int(m.get("tokens_out") or 0)
        if ev.stage_id == dash.decision_stage and ev.status == StageStatus.completed and ev.ended_at:
            prev = first_decision.get(ev.case_id)
            if prev is None or ev.ended_at < prev:
                first_decision[ev.case_id] = ev.ended_at
    by_id = {c.id: c for c in cases}
    decision_secs = [
        max((_aware(t) - _aware(by_id[cid].created_at)).total_seconds(), 0.0) for cid, t in first_decision.items()
    ]

    decided = [c for c in cases if c.decision]
    straight = [
        c for c in decided
        if c.decision == "auto_approve" and c.id not in reviewed
        and c.status in (CaseStatus.approved, CaseStatus.exported)
    ]
    exceptions = [c for c in decided if c.decision in dash.exception_decisions]
    exceptions_amount = sum((c.total or Decimal(0) for c in exceptions), Decimal(0))

    # series: one point per day, cases created split by decision
    stage = cfg.workflow.stage(dash.decision_stage)  # decision keys = the decision stage's declared outcomes
    declared = cfg.agents[stage.agent].actions[stage.action].outcomes if stage.agent and stage.action else []
    decisions = [*declared, *sorted({c.decision for c in decided if c.decision} - set(declared))]
    per_day: dict[str, Counter[str]] = defaultdict(Counter)
    for c in cases:
        day = _aware(c.created_at).date().isoformat()
        per_day[day]["cases"] += 1
        if c.decision:
            per_day[day][c.decision] += 1
    series = []
    for i in range(days):
        day = (start_day + timedelta(days=i)).isoformat()
        point: dict[str, Any] = {"date": day, "cases": per_day[day]["cases"]}
        for d in decisions:
            point[d] = per_day[day][d]
        series.append(point)

    # exceptions by rule: cases whose latest policy run failed the rule
    rule_names = {r.id: r for r in cfg.rules.rules}
    by_rule: Counter[str] = Counter()
    amount_by_rule: dict[str, Decimal] = defaultdict(Decimal)
    if ids:
        latest = (
            select(RuleResult.case_id, func.max(RuleResult.run_no).label("run_no"))
            .where(RuleResult.case_id.in_(ids))
            .group_by(RuleResult.case_id)
            .subquery()
        )
        failed = s.execute(
            select(RuleResult.rule_id, RuleResult.case_id)
            .join(latest, (latest.c.case_id == RuleResult.case_id) & (latest.c.run_no == RuleResult.run_no))
            .where(RuleResult.status == "fail")
        ).all()
        for rule_id, cid in {(r, c) for r, c in failed}:
            by_rule[rule_id] += 1
            amount_by_rule[rule_id] += by_id[cid].total or Decimal(0)
    exceptions_by_rule = [
        {
            "rule_id": rid,
            "name": (rule_names[rid].fail_label or rule_names[rid].name) if rid in rule_names else rid,
            "severity": rule_names[rid].severity if rid in rule_names else None,
            "cases": n,
            "amount": amount_by_rule[rid],
        }
        for rid, n in by_rule.most_common()
    ]

    live: dict[str, int] = {str(st): int(cnt) for st, cnt in s.execute(select(Case.status, func.count()).group_by(Case.status))}
    n = len(cases)
    return {
        "range": label,
        "ranges": dash.ranges,
        "since": since,
        "currency": cfg.rules.currency,
        "kpis": {
            "cases": n,
            "decided": len(decided),
            "straight_through_pct": round(100 * len(straight) / len(decided), 1) if decided else None,
            "in_review": live.get(CaseStatus.needs_review.value, 0),
            "on_hold": live.get(CaseStatus.on_hold.value, 0),
            "processing": live.get(CaseStatus.processing.value, 0) + live.get(CaseStatus.received.value, 0),
            "median_time_to_decision_s": round(statistics.median(decision_secs), 1) if decision_secs else None,
            "exceptions_caught_amount": exceptions_amount,
            "exceptions_caught_cases": len(exceptions),
            "llm_cost_per_invoice_usd": float(round(cost / n, 6)) if n else None,
            "llm_cost_total_usd": float(round(cost, 6)),
            "llm_tokens": tokens,
        },
        "series": series,
        "decision_keys": decisions,
        "decision_mix": [{"decision": d, "cases": sum(1 for c in decided if c.decision == d)} for d in decisions],
        "exceptions_by_rule": exceptions_by_rule,
        "seeded_cases": sum(1 for c in cases if c.seed),
    }
