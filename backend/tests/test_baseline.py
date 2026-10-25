"""Shared baseline, independent interactions (N6): one stored copy of each fixture for every user, a private
copy of the master data per workspace, scenario prerequisites run automatically, reset demo data."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.agents import policy
from app.api import me as me_api
from app.baseline import Baseline, BaselineError
from app.config_loader import resolved_config
from app.main import create_app
from app.models import Case, Document
from app.settings import REPO_ROOT, Settings
from app.storage import LocalStorage, OwnerStorage, ReadOnlyKeyError
from app.tenancy import bind_owner
from tests.conftest import ALICE, BOB, CONFIG_DIR, FIXTURE_DIR, auth_client, auth_headers
from tests.test_api import wait_status

SETTLED = {"approved", "needs_review", "on_hold", "rejected", "failed"}


@pytest.fixture
def live_settings(test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setattr(policy, "today", lambda: date(2026, 10, 11))
    return test_settings.model_copy(update={"force_stub_agents": False, "llm_backend": "fake"})


def _files(root: Path, prefix: str) -> list[Path]:
    base = root / prefix
    return sorted(p for p in base.rglob("*") if p.is_file()) if base.exists() else []


def _remaining(client: TestClient, po_number: str) -> Decimal:
    return Decimal(next(p for p in client.get("/api/pos").json() if p["po_number"] == po_number)["remaining"])


def _failed_rules(client: TestClient, case_id: int) -> set[str]:
    return {r["rule_id"] for r in client.get(f"/api/cases/{case_id}").json()["rule_results"] if r["status"] == "fail"}


def test_two_users_split_po_independently_from_one_stored_copy(live_settings: Settings):
    app = create_app(live_settings)
    storage_root = live_settings.storage_dir
    with auth_client(app, ALICE) as alice:
        bob = TestClient(app, headers=auth_headers(BOB))
        keys, after = {}, {}
        for who, client in (("alice", alice), ("bob", bob)):
            assert _remaining(client, "PO-1001") == Decimal("10000.00")
            a = client.post("/api/fixtures/S02a_split_part1/run").json()
            assert a["prerequisites"] == []
            a = wait_status(client, a["id"], SETTLED, timeout=60)
            assert a["decision"] == "auto_approve", who
            assert _remaining(client, "PO-1001") == Decimal("4000.00")

            b = client.post("/api/fixtures/S02b_split_part2/run").json()
            assert b["prerequisites"] == []  # S02a already ran in this workspace
            b = wait_status(client, b["id"], SETTLED, timeout=60)
            assert b["decision"] == "needs_review", who
            assert {"po_balance", "unmatched_lines"} <= _failed_rules(client, b["id"])
            user = ALICE if who == "alice" else BOB
            with bind_owner(app.state.session_factory, user.id)() as s:
                keys[who] = s.scalars(
                    select(Document.storage_key).where(Document.case_id.in_([a["id"], b["id"]])).order_by(Document.id)
                ).all()
            after[who] = _remaining(client, "PO-1001")  # S02b's reservation counts too

        # the second user's runs did not touch the first user's balance
        assert _remaining(alice, "PO-1001") == after["alice"] == after["bob"]

    # one stored copy of each fixture, shared by both workspaces; nothing copied per user
    assert keys["alice"] == keys["bob"] and all(k.startswith("baseline/fixtures/") for k in keys["alice"])
    fixtures = _files(storage_root, "baseline/fixtures")
    sc = app.state.baseline.scenarios
    expected = sorted(sc.scenario(sid).file for sid in ("S02a_split_part1", "S02b_split_part2"))
    assert sorted(p.name.split("_", 1)[1] for p in fixtures) == expected  # <sha12>_<file>, once each
    for user in (ALICE, BOB):
        assert not [p for p in _files(storage_root, f"u/{user.id}") if p.suffix in (".pdf", ".png")]
    assert _files(storage_root, "baseline/renders")  # page renders live with the shared file


def test_prerequisites_run_first_when_the_workspace_has_none(live_settings: Settings):
    with auth_client(create_app(live_settings)) as client:
        r = client.post("/api/fixtures/S02b_split_part2/run")
        assert r.status_code == 201, r.text
        b = r.json()
        [pre] = b["prerequisites"]
        assert pre["scenario_id"] == "S02a_split_part1" and pre["id"] < b["id"]
        assert "Ran S02a_split_part1 first" in b["note"]
        a = wait_status(client, pre["id"], SETTLED, timeout=60)
        b = wait_status(client, b["id"], SETTLED, timeout=60)
        assert a["decision"] == "auto_approve" and "Run automatically before S02b_split_part2" in a["note"]
        assert b["decision"] == "needs_review"  # the same story as running them by hand
        assert {"po_balance", "unmatched_lines"} <= _failed_rules(client, b["id"])

        # S04 (a re-sent duplicate) needs the original S01 in the workspace
        d = client.post("/api/fixtures/S04_duplicate_scan/run").json()
        assert [p["scenario_id"] for p in d["prerequisites"]] == ["S01_happy"]
        s01 = wait_status(client, d["prerequisites"][0]["id"], SETTLED, timeout=60)
        d = wait_status(client, d["id"], SETTLED, timeout=60)
        assert s01["decision"] == "auto_approve" and d["decision"] == "reject"
        assert "duplicate_exact" in _failed_rules(client, d["id"])

        # once the prerequisite is there, nothing runs twice
        again = client.post("/api/fixtures/S02b_split_part2/run").json()
        assert again["prerequisites"] == []
        wait_status(client, again["id"], SETTLED, timeout=60)


def test_baseline_files_are_read_only(tmp_path: Path):
    root = LocalStorage(tmp_path)
    with pytest.raises(ReadOnlyKeyError):
        root.put("baseline/fixtures/x.pdf", b"x")
    with pytest.raises(ReadOnlyKeyError):
        root.delete_prefix("baseline/")
    with pytest.raises(ReadOnlyKeyError):
        OwnerStorage(root, ALICE.id).put("baseline/fixtures/x.pdf", b"x")

    baseline = Baseline(root, REPO_ROOT / "data", resolved_config(CONFIG_DIR))
    bf = baseline.fixture("S01_acme_INV-A-2207.pdf")
    assert bf.key.startswith("baseline/fixtures/") and root.get(bf.key) == (FIXTURE_DIR / bf.filename).read_bytes()
    assert OwnerStorage(root, ALICE.id).get(bf.key) == root.get(bf.key)  # readable from any workspace
    assert baseline.fixture(bf.filename) == bf  # published once
    with pytest.raises(BaselineError, match="not a scenario file"):
        baseline.fixture("../scenarios.yaml")

    # a new process finds the published copy instead of writing it again
    (tmp_path / bf.key).write_bytes(b"%PDF-marker")
    fresh = Baseline(root, REPO_ROOT / "data", resolved_config(CONFIG_DIR))
    assert fresh.fixture(bf.filename).key == bf.key and root.get(bf.key) == b"%PDF-marker"


def test_supporting_files_download_from_the_baseline(test_settings: Settings):
    with auth_client(create_app(test_settings)) as client:
        w9 = client.get("/api/fixtures/S05_unknown_vendor/files/S05_novaprint_W9.pdf")
        assert w9.status_code == 200 and w9.content == (FIXTURE_DIR / "S05_novaprint_W9.pdf").read_bytes()
        assert len(_files(test_settings.storage_dir, "baseline/fixtures")) == 1


def test_reset_workspace(live_settings: Settings):
    app = create_app(live_settings)
    with auth_client(app) as client:
        s01 = client.post("/api/fixtures/S01_happy/run").json()
        wait_status(client, s01["id"], SETTLED, timeout=60)
        assert _remaining(client, "PO-1002") == Decimal("0.00")
        assert _files(live_settings.storage_dir, f"u/{ALICE.id}")  # the approved invoice's template file
        baseline_files = _files(live_settings.storage_dir, "baseline")

        # refused while a run is in flight
        sf = bind_owner(app.state.session_factory, ALICE.id)
        with sf() as s:
            s.execute(update(Case).where(Case.id == s01["id"]).values(status="processing"))
            s.commit()
        busy = client.post("/api/me/reset-workspace")
        assert busy.status_code == 409 and s01["ref"] in busy.json()["detail"]
        with sf() as s:
            s.execute(update(Case).where(Case.id == s01["id"]).values(status="approved"))
            s.commit()

        r = client.post("/api/me/reset-workspace")
        assert r.status_code == 200, r.text
        assert (r.json()["vendors"], r.json()["history_cases"]) == (7, 40)
        assert _remaining(client, "PO-1002") > 0
        assert client.get("/api/cases").json()["total"] == 40
        assert client.get(f"/api/cases/{s01['id']}").status_code == 404
        assert not _files(live_settings.storage_dir, f"u/{ALICE.id}")
        assert _files(live_settings.storage_dir, "baseline") == baseline_files  # the shared files stay

        again = client.post("/api/fixtures/S01_happy/run").json()
        assert again["ref"] == s01["ref"]  # numbering restarts with the workspace
        wait_status(client, again["id"], SETTLED, timeout=60)


def test_new_demo_data_notice(test_settings: Settings, monkeypatch: pytest.MonkeyPatch):
    with auth_client(create_app(test_settings)) as client:
        me = client.get("/api/me").json()
        assert me["baseline_outdated"] is False
        monkeypatch.setattr(me_api, "BASELINE_VERSION", me["baseline_version"] + 1)
        assert client.get("/api/me").json()["baseline_outdated"] is True
