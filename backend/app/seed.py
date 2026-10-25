"""Workspace seed data, and `make seed` (reset the DB to an empty, migrated schema).

    uv run python -m app.seed [--no-reset]

Every user's workspace is seeded on first sign-in (app.baseline.Baseline.ensure_workspace) with the rows
below, written through an owner-bound session so each row gets that user's owner_id:
- master data from data/master/*.csv (vendors, POs, PO lines, GRNs, GL accounts)
- sample history (data/scenarios.yaml `seed_history`), so the dashboard has something to show on first
  open: already-decided cases flagged `is_sample=true`, each with its failed rule rows and ONE summary
  stage_event (the decision stage, carrying the whole run's latency, tokens and cost; no per-stage payloads).
  That keeps a new workspace small (tests/test_seed.py measures it). They never touch PO balances, so the
  live demo scenarios start from the exact numbers in scenarios.yaml.
"""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.cases import case_ref
from app.config_loader import ResolvedConfig
from app.db import Base, init_db, make_engine
from app.models import (
    GRN,
    Case,
    CaseStatus,
    GLAccount,
    POLine,
    PurchaseOrder,
    RuleResult,
    StageEvent,
    StageStatus,
    Vendor,
)
from app.scenarios import Scenarios, money
from app.settings import Settings, get_settings
from app.tenancy import require_owner, scoped

SEED_RNG = 20261011
# Decision outcome -> rule severity that produces it (mirrors rules.yaml decision_precedence).
DECISION_SEVERITY = {"needs_review": "review", "on_hold": "hold", "reject": "reject"}


class SeedError(RuntimeError):
    pass


@dataclass
class SeedReport:
    vendors: int = 0
    purchase_orders: int = 0
    po_lines: int = 0
    grns: int = 0
    gl_accounts: int = 0
    history_cases: int = 0

    def __str__(self) -> str:
        return (f"vendors={self.vendors} purchase_orders={self.purchase_orders} po_lines={self.po_lines} "
                f"grns={self.grns} gl_accounts={self.gl_accounts} seeded_history_cases={self.history_cases}")


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise SeedError(f"{path} not found: run `make fixtures` first")
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _day(s: str) -> datetime | None:
    return datetime.combine(datetime.fromisoformat(s).date(), time(9, 0), UTC) if s else None


def load_master(s: Session, master_dir: Path) -> SeedReport:
    """Insert master data from the generated CSVs into the session owner's (empty) workspace."""
    rep = SeedReport()
    for r in _rows(master_dir / "gl_accounts.csv"):
        s.add(GLAccount(code=r["code"], name=r["name"], category=r["category"] or None))
        rep.gl_accounts += 1
    vendor_ids: dict[str, int] = {}
    for r in _rows(master_dir / "vendors.csv"):
        v = Vendor(
            code=r["code"], legal_name=r["legal_name"], aliases=[a for a in r["aliases"].split("|") if a],
            tax_id=r["tax_id"] or None, country=r["country"] or None, email_domain=r["email_domain"] or None,
            bank_last4=r["bank_last4"] or None, iban_hash=r["iban_hash"] or None, category=r["category"] or None,
            status=r["status"], default_gl=r["default_gl"] or None, non_po_allowed=r["non_po_allowed"] == "true",
        )
        s.add(v)
        s.flush()
        vendor_ids[v.code] = v.id
        rep.vendors += 1
    po_ids: dict[str, int] = {}
    for r in _rows(master_dir / "purchase_orders.csv"):
        po = PurchaseOrder(
            po_number=r["po_number"], vendor_id=vendor_ids[r["vendor_code"]], currency=r["currency"],
            amount=Decimal(r["amount"]), status=r["status"], created_at=_day(r["created_at"]),
            approved_by=r["approved_by"] or None, notes=r["notes"] or None,
        )
        s.add(po)
        s.flush()
        po_ids[po.po_number] = po.id
        rep.purchase_orders += 1
    for r in _rows(master_dir / "po_lines.csv"):
        s.add(POLine(
            po_id=po_ids[r["po_number"]], line_no=int(r["line_no"]), description=r["description"],
            qty=Decimal(r["qty"]), unit_price=Decimal(r["unit_price"]), amount=Decimal(r["amount"]),
            gl_code=r["gl_code"] or None,
        ))
        rep.po_lines += 1
    for r in _rows(master_dir / "grns.csv"):
        s.add(GRN(po_id=po_ids[r["po_number"]], line_no=int(r["line_no"]), qty_received=Decimal(r["qty_received"]),
                  received_at=_day(r["received_at"])))
        rep.grns += 1
    s.flush()
    return rep


def _history_path(cfg: ResolvedConfig) -> list[tuple[str, str | None, str | None, str]]:
    """(stage_id, agent, action, outcome) from the workflow start to the decision stage, following each
    stage's first (happy-path) outcome."""
    wf = cfg.workflow
    path: list[tuple[str, str | None, str | None, str]] = []
    stage = wf.stage(wf.start)
    while True:
        outcome, target = next(iter(stage.next.items()))
        path.append((stage.id, stage.agent, stage.action, outcome))
        if stage.agent == "decision" or target.startswith("end:") or len(path) > len(wf.stages):
            return path
        stage = wf.stage(target)


def seed_history(s: Session, cfg: ResolvedConfig, sc: Scenarios, *, now: datetime | None = None) -> int:
    spec = sc.seed_history
    if not spec.enabled or spec.cases <= 0:
        return 0
    rng = random.Random(SEED_RNG)
    now = now or datetime.now(UTC)
    vendors = s.scalars(
        scoped(select(Vendor).where(Vendor.status == "approved").order_by(Vendor.id), require_owner(s))
    ).all()
    if not vendors:
        raise SeedError("seed history needs approved vendors: load master data first")
    rules_by_sev: dict[str, list] = {}
    for rule in cfg.rules.rules:
        if rule.enabled:
            rules_by_sev.setdefault(rule.severity, []).append(rule)
    decisions, weights = zip(*spec.decision_mix.items(), strict=True)
    pricing = cfg.app.llm.pricing_per_1m_usd
    path = _history_path(cfg)

    for _ in range(spec.cases):
        decision = rng.choices(decisions, weights)[0]
        vendor = rng.choice(vendors)
        created = now - timedelta(days=rng.uniform(0.3, spec.days_back), minutes=rng.randint(0, 59))
        total = money(rng.uniform(120, 9500))
        case = Case(
            source="upload", is_sample=True,
            submitter_name=vendor.legal_name, submitter_email=f"billing@{vendor.email_domain}",
            status=CaseStatus.rejected if decision == "reject" else CaseStatus.approved,
            current_stage="end:rejected" if decision == "reject" else "end:approved",
            decision=decision, vendor_id=vendor.id, currency="USD", total=total,
            confidence=round(rng.uniform(0.9, 0.99), 2), config_hash=cfg.hash, created_at=created,
        )
        s.add(case)
        s.flush()
        case.ref = case_ref(s, cfg, case)

        failed = []
        if decision in DECISION_SEVERITY:
            pool = rules_by_sev.get(DECISION_SEVERITY[decision], [])
            failed = rng.sample(pool, k=min(len(pool), 1 + (rng.random() < 0.25)))
        case.decision_summary = (
            "Sample history: auto-approved, every check passed." if not failed
            else "Sample history: " + "; ".join(r.name.lower() for r in failed) + " failed."
        )
        for r in failed:
            s.add(RuleResult(case_id=case.id, run_no=1, rule_id=r.id, status="fail", severity=r.severity,
                             explanation=f"Sample history: {r.name} failed.", evidence={"sample": True}))

        # one summary event per case: the decision stage, with the metrics of the whole (simulated) run.
        # agent_id stays empty so Agent Studio's per-agent stats only count real runs.
        latency = tokens_in = tokens_out = 0
        for _stage_id, agent_id, action, _outcome in path:
            agent = cfg.agents.get(agent_id or "")
            if agent and action and agent.actions[action].prompt:
                latency += rng.randint(4000, 22000)
                tokens_in += rng.randint(1500, 9000)
                tokens_out += rng.randint(200, 1800)
            else:
                latency += rng.randint(40, 600)
        cost = (tokens_in * pricing.get("input", 0.0) + tokens_out * pricing.get("output", 0.0)) / 1_000_000
        t = created + timedelta(milliseconds=latency)
        s.add(StageEvent(
            case_id=case.id, run_no=1, seq=1, stage_id=path[-1][0], agent_id=None, action=None,
            status=StageStatus.completed, outcome=decision, input_summary={}, output={},
            reasoning=["Sample history: a summary of a full run (per-stage events are not stored)."],
            metrics={"latency_ms": latency, "tokens_in": tokens_in, "tokens_out": tokens_out,
                     "cost_usd": round(cost, 6), "cache_hit": False, "sample": True},
            started_at=created, ended_at=t,
        ))
        case.updated_at = t + (timedelta(hours=rng.uniform(0.5, 30)) if decision != "auto_approve" else timedelta())
        if case.status == CaseStatus.approved:
            case.approved_at = case.updated_at
            case.approved_by = "auto" if decision == "auto_approve" else "AP Reviewer"
    s.flush()
    return spec.cases


def reset_db(engine: Engine) -> None:
    """Drop every table (and Alembic's version table), then migrate to head."""
    from app import models  # noqa: F401  (register tables)

    Base.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    init_db(engine)


def run_seed(settings: Settings, *, reset: bool = True) -> None:
    """Reset (or just migrate) the database. Workspaces are seeded per user on first sign-in."""
    engine = make_engine(settings.database_url)
    try:
        if reset:
            reset_db(engine)
        else:
            init_db(engine)
    finally:
        engine.dispose()


def main() -> None:
    ap = argparse.ArgumentParser(description="Reset the DB to an empty, migrated schema.")
    ap.add_argument("--no-reset", action="store_true", help="only run pending migrations; keep the data")
    args = ap.parse_args()
    settings = get_settings()
    run_seed(settings, reset=not args.no_reset)
    what = "migrated" if args.no_reset else "reset and migrated"
    print(f"{settings.database_url.split('://', 1)[0]} database {what}. Workspaces are seeded on first sign-in.")


if __name__ == "__main__":
    main()
