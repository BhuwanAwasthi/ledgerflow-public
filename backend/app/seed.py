"""`make seed`: reset the DB, load data/master/*.csv, and optionally add seeded demo history.

    uv run python -m app.seed [--no-history] [--no-reset]

Seeded history (data/scenarios.yaml `seed_history`) gives the dashboard something to show on first open:
already-decided cases flagged `seed=true`, each with a short stage_event trail and its failed rule rows.
They never touch PO balances, so the live demo scenarios start from the exact numbers in scenarios.yaml.
"""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.config_loader import ResolvedConfig, resolved_config
from app.db import Base, init_db, make_engine, make_session_factory
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
from app.scenarios import Scenarios, load_scenarios, money
from app.settings import Settings, get_settings

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
    """Insert master data from the generated CSVs (expects empty tables)."""
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
    vendors = s.scalars(select(Vendor).where(Vendor.status == "approved").order_by(Vendor.id)).all()
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
            source="upload", seed=True,
            submitter_name=vendor.legal_name, submitter_email=f"billing@{vendor.email_domain}",
            status=CaseStatus.rejected if decision == "reject" else CaseStatus.approved,
            current_stage="end:rejected" if decision == "reject" else "end:approved",
            decision=decision, vendor_id=vendor.id, currency="USD", total=total,
            confidence=round(rng.uniform(0.9, 0.99), 2), config_hash=cfg.hash, created_at=created,
        )
        s.add(case)
        s.flush()
        case.ref = f"{cfg.app.app.case_ref_prefix}{case.id:04d}"

        failed = []
        if decision in DECISION_SEVERITY:
            pool = rules_by_sev.get(DECISION_SEVERITY[decision], [])
            failed = rng.sample(pool, k=min(len(pool), 1 + (rng.random() < 0.25)))
        case.decision_summary = (
            "Seeded demo data: auto-approved, every check passed." if not failed
            else "Seeded demo data: " + "; ".join(r.name.lower() for r in failed) + " failed."
        )
        for r in failed:
            s.add(RuleResult(case_id=case.id, run_no=1, rule_id=r.id, status="fail", severity=r.severity,
                             explanation=f"Seeded demo data: {r.name} failed.", evidence={"seed": True}))

        t = created
        for seq, (stage_id, agent_id, action, outcome) in enumerate(path, start=1):
            agent = cfg.agents.get(agent_id or "")
            uses_llm = bool(agent and action and agent.actions[action].prompt)
            latency = rng.randint(4000, 22000) if uses_llm else rng.randint(40, 600)
            metrics: dict = {"latency_ms": latency, "cache_hit": False}
            if uses_llm:
                tin, tout = rng.randint(1500, 9000), rng.randint(200, 1800)
                cost = (tin * pricing.get("input", 0.0) + tout * pricing.get("output", 0.0)) / 1_000_000
                metrics.update(tokens_in=tin, tokens_out=tout, cost_usd=round(cost, 6))
            end = t + timedelta(milliseconds=latency)
            s.add(StageEvent(
                case_id=case.id, run_no=1, seq=seq, stage_id=stage_id, agent_id=agent_id, action=action,
                status=StageStatus.completed, outcome=decision if agent_id == "decision" else outcome,
                input_summary={"seed": True}, output={"seed": True}, reasoning=["Seeded demo data."],
                metrics=metrics, started_at=t, ended_at=end,
            ))
            t = end
        case.updated_at = t + (timedelta(hours=rng.uniform(0.5, 30)) if decision != "auto_approve" else timedelta())
        if case.status == CaseStatus.approved:
            case.approved_at = case.updated_at
            case.approved_by = "auto" if decision == "auto_approve" else "AP Reviewer"
    s.flush()
    return spec.cases


def reset_db(engine: Engine) -> None:
    from app import models  # noqa: F401  (register tables)

    Base.metadata.drop_all(engine)
    init_db(engine)


def run_seed(settings: Settings, *, history: bool = True, reset: bool = True) -> SeedReport:
    engine = make_engine(settings.database_url)
    try:
        if reset:
            reset_db(engine)
        else:
            init_db(engine)
        sf = make_session_factory(engine)
        with sf() as s:
            if not reset and s.scalar(select(func.count()).select_from(Vendor)):
                raise SeedError("master data already loaded; drop --no-reset to start over")
            rep = load_master(s, settings.data_dir / "master")
            if history:
                cfg = resolved_config(settings.config_dir, s)
                rep.history_cases = seed_history(s, cfg, load_scenarios(settings.data_dir / "scenarios.yaml"))
            s.commit()
        return rep
    finally:
        engine.dispose()


def main() -> None:
    ap = argparse.ArgumentParser(description="Reset the DB and load master data (+ seeded history).")
    ap.add_argument("--no-history", action="store_true", help="skip the seeded demo history")
    ap.add_argument("--no-reset", action="store_true", help="keep existing tables (fails if master data exists)")
    args = ap.parse_args()
    settings = get_settings()
    try:
        rep = run_seed(settings, history=not args.no_history, reset=not args.no_reset)
    except SeedError as e:
        raise SystemExit(f"seed failed: {e}") from e
    print(f"Seeded {settings.database_url.split('://', 1)[0]} database: {rep}")


if __name__ == "__main__":
    main()
