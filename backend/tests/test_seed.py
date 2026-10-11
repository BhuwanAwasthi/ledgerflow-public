from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import make_engine, make_session_factory
from app.main import create_app
from app.models import GRN, Case, GLAccount, POLine, PurchaseOrder, RuleResult, StageEvent, Vendor
from app.seed import SeedError, run_seed
from app.settings import Settings
from tests.test_api import wait_status


def _count(s, model, *where) -> int:
    return s.scalar(select(func.count()).select_from(model).where(*where))


def test_seed_loads_master_and_history(test_settings: Settings):
    rep = run_seed(test_settings)
    assert (rep.vendors, rep.purchase_orders, rep.po_lines, rep.grns, rep.gl_accounts) == (7, 7, 9, 6, 9)
    assert rep.history_cases == 40

    engine = make_engine(test_settings.database_url)
    with make_session_factory(engine)() as s:
        assert _count(s, Vendor) == 7 and _count(s, GLAccount) == 9 and _count(s, GRN) == 6
        assert _count(s, POLine) == 9
        po = s.scalar(select(PurchaseOrder).where(PurchaseOrder.po_number == "PO-1001"))
        assert po.amount == Decimal("10000.00") and po.status == "open"
        q77 = s.scalar(select(PurchaseOrder).where(PurchaseOrder.po_number == "PO-1004"))
        assert q77.notes == "Raised from quote Q-77"
        assert s.scalar(select(Vendor).where(Vendor.code == "V004")).non_po_allowed is True

        cases = s.scalars(select(Case)).all()
        assert len(cases) == 40 and all(c.seed for c in cases)
        assert {c.decision for c in cases} <= {"auto_approve", "needs_review", "on_hold", "reject"}
        assert {c.status for c in cases} <= {"approved", "rejected"}  # never pollutes the work queue
        assert all(c.ref and c.decision_summary.startswith("Seeded demo data") for c in cases)
        for c in cases:
            fails = _count(s, RuleResult, RuleResult.case_id == c.id)
            assert (fails == 0) == (c.decision == "auto_approve"), c.ref
            last = s.scalars(select(StageEvent).where(StageEvent.case_id == c.id).order_by(StageEvent.seq)).all()
            assert last[0].stage_id == "intake" and last[-1].stage_id == "decide"
            assert last[-1].outcome == c.decision
    engine.dispose()

    # make seed resets: running it again gives the same counts, not doubles
    assert run_seed(test_settings, history=False).vendors == 7
    with make_session_factory(engine := make_engine(test_settings.database_url))() as s:
        assert _count(s, Vendor) == 7 and _count(s, Case) == 0
    engine.dispose()


def test_seed_without_reset_refuses_to_duplicate(test_settings: Settings):
    run_seed(test_settings, history=False)
    with pytest.raises(SeedError, match="already loaded"):
        run_seed(test_settings, history=False, reset=False)


def test_seed_needs_generated_csvs(test_settings: Settings, tmp_path):
    settings = test_settings.model_copy(update={"data_dir": tmp_path / "empty"})
    with pytest.raises(SeedError, match="make fixtures"):
        run_seed(settings)


def test_fixtures_api_lists_and_runs_scenarios(test_settings: Settings):
    run_seed(test_settings, history=False)
    with TestClient(create_app(test_settings)) as client:
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
