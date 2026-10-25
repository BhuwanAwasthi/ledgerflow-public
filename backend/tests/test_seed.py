from __future__ import annotations

from decimal import Decimal

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, select

from app.baseline import Baseline
from app.config_loader import resolved_config
from app.db import Base, make_engine, make_session_factory
from app.main import create_app
from app.models import GRN, Case, GLAccount, POLine, PurchaseOrder, RuleResult, StageEvent, User, Vendor
from app.seed import SeedError, load_master, run_seed
from app.settings import REPO_ROOT, Settings
from app.storage import LocalStorage, OwnerStorage
from app.tenancy import bind_owner
from tests.conftest import ALICE, CONFIG_DIR, add_user, auth_client
from tests.test_api import wait_status


def _count(s, model, *where) -> int:
    return s.scalar(select(func.count()).select_from(model).where(*where))


def _baseline(tmp_path) -> Baseline:
    return Baseline(LocalStorage(tmp_path / "storage"), REPO_ROOT / "data", resolved_config(CONFIG_DIR))


def test_workspace_seed_loads_master_and_history(engine_factory, tmp_path):
    add_user(engine_factory, ALICE)
    baseline = _baseline(tmp_path)
    user = baseline.ensure_workspace(engine_factory, ALICE.id)
    assert user.baseline_version is not None and user.case_seq == 40  # seeded history took INV-...-0001..0040

    with bind_owner(engine_factory, ALICE.id)() as s:
        assert _count(s, Vendor) == 7 and _count(s, GLAccount) == 9 and _count(s, GRN) == 6
        assert _count(s, POLine) == 9
        po = s.scalar(select(PurchaseOrder).where(PurchaseOrder.po_number == "PO-1001"))
        assert po.amount == Decimal("10000.00") and po.status == "open"
        q77 = s.scalar(select(PurchaseOrder).where(PurchaseOrder.po_number == "PO-1004"))
        assert q77.notes == "Raised from quote Q-77"
        assert s.scalar(select(Vendor).where(Vendor.code == "V004")).non_po_allowed is True

        cases = s.scalars(select(Case)).all()
        assert len(cases) == 40 and all(c.seed and c.owner_id == ALICE.id for c in cases)
        assert {c.decision for c in cases} <= {"auto_approve", "needs_review", "on_hold", "reject"}
        assert {c.status for c in cases} <= {"approved", "rejected"}  # never pollutes the work queue
        assert all(c.ref and c.decision_summary.startswith("Seeded demo data") for c in cases)
        for c in cases:
            fails = _count(s, RuleResult, RuleResult.case_id == c.id)
            assert (fails == 0) == (c.decision == "auto_approve"), c.ref
            last = s.scalars(select(StageEvent).where(StageEvent.case_id == c.id).order_by(StageEvent.seq)).all()
            assert last[0].stage_id == "intake" and last[-1].stage_id == "decide"
            assert last[-1].outcome == c.decision

    # idempotent: a second sign-in seeds nothing more
    baseline.ensure_workspace(engine_factory, ALICE.id)
    with bind_owner(engine_factory, ALICE.id)() as s:
        assert _count(s, Vendor) == 7 and _count(s, Case) == 40

    # "Reset demo data": the same counts again, not doubles, and the numbering restarts
    with bind_owner(engine_factory, ALICE.id)() as s:
        s.scalars(select(PurchaseOrder).where(PurchaseOrder.po_number == "PO-1001")).one().amount = Decimal("1.00")
        s.commit()
    rep = baseline.reset_workspace(engine_factory, ALICE.id, OwnerStorage(baseline.storage, ALICE.id))
    assert (rep.vendors, rep.purchase_orders, rep.po_lines, rep.grns, rep.gl_accounts) == (7, 7, 9, 6, 9)
    assert rep.history_cases == 40
    with bind_owner(engine_factory, ALICE.id)() as s:
        assert _count(s, Vendor) == 7 and _count(s, Case) == 40
        po = s.scalars(select(PurchaseOrder).where(PurchaseOrder.po_number == "PO-1001")).one()
        assert po.amount == Decimal("10000.00")
        assert s.get(User, ALICE.id).case_seq == 40


def test_make_seed_resets_to_an_empty_migrated_database(test_settings: Settings):
    run_seed(test_settings)
    engine = make_engine(test_settings.database_url)
    sf = make_session_factory(engine)
    add_user(sf, ALICE)
    with bind_owner(sf, ALICE.id)() as s:
        load_master(s, REPO_ROOT / "data" / "master")
        s.commit()
    run_seed(test_settings, reset=False)  # only migrates: data stays
    with sf() as s:
        assert _count(s, Vendor) == 7 and _count(s, User) == 1
    run_seed(test_settings)  # reset: empty again
    with sf() as s:
        assert _count(s, Vendor) == 0 and _count(s, User) == 0
    engine.dispose()


def test_seed_needs_generated_csvs(session_factory, tmp_path):
    with session_factory() as s, pytest.raises(SeedError, match="make fixtures"):
        load_master(s, tmp_path / "empty")


def test_fixtures_api_lists_and_runs_scenarios(test_settings: Settings):
    with auth_client(create_app(test_settings)) as client:
        fixtures = client.get("/api/fixtures").json()
        assert len(fixtures) == 11
        by_id = {f["id"]: f for f in fixtures}
        assert all(f["available"] for f in fixtures)
        assert by_id["S01_happy"]["run_order"] == 1 and by_id["S09_missing_fields"]["run_order"] == 11
        assert by_id["S02b_split_part2"]["demo_remark"].startswith("PO was amended")
        assert [f["kind"] for f in by_id["S05_unknown_vendor"]["supporting_files"]] == ["tax_form", "bank_letter"]

        r = client.post("/api/fixtures/S01_happy/run")
        assert r.status_code == 201, r.text
        case = r.json()
        assert case["source"] == "fixture" and case["seed"] is False
        assert case["submitter_email"] == "billing@acme-office.example"
        detail = wait_status(client, case["id"], {"approved"})
        doc = detail["documents"][0]
        assert doc["filename"] == "S01_acme_INV-A-2207.pdf" and doc["mime"] == "application/pdf"

        bundle = client.post("/api/fixtures/S10_bundle/run").json()
        assert bundle["submitter_email"] == "ap@democo.example"

        assert client.post("/api/fixtures/S99_nope/run").status_code == 404

        w9 = client.get("/api/fixtures/S05_unknown_vendor/files/S05_novaprint_W9.pdf")
        assert w9.status_code == 200 and w9.content.startswith(b"%PDF")
        assert client.get("/api/fixtures/S05_unknown_vendor/files/S01_acme_INV-A-2207.pdf").status_code == 404
        assert client.get("/api/fixtures/S05_unknown_vendor/files/..%2F..%2Fscenarios.yaml").status_code == 404


def test_migrations_match_the_models(engine_factory):
    """Tests and the app build the schema through the Alembic migrations: they must produce exactly the
    tables, columns, types, indexes and constraints the models declare."""
    with engine_factory.kw["bind"].connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        assert compare_metadata(ctx, Base.metadata) == []
