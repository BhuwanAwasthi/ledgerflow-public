"""Workspace isolation (N5 data, PUBLIC-ADDENDUM §4): user B gets 404 (never 403) for every resource of user
A's, lists never mix workspaces, refs number per owner, and the session guard catches a forgotten filter."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agents import policy
from app.main import create_app
from app.models import Case, PurchaseOrder, Vendor
from app.seed import load_master
from app.settings import Settings
from app.storage import LocalStorage, OwnerStorage, ReadOnlyKeyError
from app.tenancy import TenancyError, bind_owner, scoped
from tests.conftest import ALICE, BOB, MASTER_DIR, add_user, auth_client, auth_headers
from tests.test_api import wait_status

SETTLED = {"approved", "needs_review", "on_hold", "rejected", "failed"}


@pytest.fixture
def live_settings(test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setattr(policy, "today", lambda: date(2026, 10, 11))
    return test_settings.model_copy(update={"force_stub_agents": False, "llm_backend": "fake"})


def test_user_b_gets_404_for_everything_of_user_a(live_settings: Settings):
    app = create_app(live_settings)
    with auth_client(app, ALICE) as alice:
        bob = TestClient(app, headers=auth_headers(BOB))

        # ---- Alice's workspace: an approved and exported invoice, an onboarding hold, a config edit
        s01 = alice.post("/api/fixtures/S01_happy/run").json()
        s01 = wait_status(alice, s01["id"], SETTLED, timeout=60)
        assert s01["status"] == "approved"
        s05 = alice.post("/api/fixtures/S05_unknown_vendor/run").json()
        s05 = wait_status(alice, s05["id"], SETTLED, timeout=60)
        assert s05["status"] == "on_hold" and s05["vendor_id"]
        batch = alice.post("/api/exports", json={"case_ids": [s01["id"]], "mode": "batch"}).json()
        doc = s01["documents"][0]
        po = next(p for p in alice.get("/api/pos").json() if p["po_number"] == "PO-1002")
        vendor = alice.get(f"/api/vendors/{s05['vendor_id']}").json()
        token = vendor["requests"][0]["token"]
        notification = alice.get("/api/notifications").json()[0]
        rules = alice.get("/api/config/rules.yaml").json()["content"]
        saved = alice.put("/api/config/rules.yaml", json={"content": rules.replace("pct: 2.0", "pct: 5.0")})
        assert saved.status_code == 200, saved.text

        a, d = s01["id"], doc["id"]
        reads = [
            f"/api/cases/{a}", f"/api/cases/{a}/journey", f"/api/cases/{a}/workspace",
            f"/api/cases/{a}/events/stream?follow=false", f"/api/cases/{a}/template/1/download",
            f"/api/cases/{a}/audit-pack", f"/api/cases/{a}/documents/{d}/file",
            f"/api/cases/{a}/documents/{d}/pages/1.png", f"/api/exports/{batch['id']}/download",
            f"/api/vendors/{s05['vendor_id']}", f"/api/pos/{po['id']}", f"/portal/{token}",
            f"/api/cases/by-ref/{s01['ref']}",
        ]
        for url in reads:
            assert alice.get(url).status_code == 200, url  # the owner can
            r = bob.get(url)
            assert r.status_code == 404, (url, r.status_code)  # never 403: other ids look like missing ones

        writes = [
            (f"/api/cases/{s05['id']}/review", {"action": "approve"}),
            (f"/api/cases/{s05['id']}/rerun", {}),
            (f"/api/cases/{a}/stream-token", None),
            (f"/api/vendors/{s05['vendor_id']}/approve", {"actor": "Bob"}),
            (f"/api/vendors/{s05['vendor_id']}/reject", {"actor": "Bob"}),
            (f"/api/notifications/{notification['id']}/read", None),
        ]
        for url, body in writes:
            r = bob.post(url, json=body)
            assert r.status_code == 404, (url, r.status_code, r.text)
        upload = bob.post(f"/portal/{token}/upload", files=[("files", ("w9.pdf", b"%PDF-1.4", "application/pdf"))])
        assert upload.status_code == 404
        assert bob.post("/api/exports", json={"case_ids": [a], "mode": "batch"}).status_code == 404
        assert bob.post("/api/config/test-agent",
                        json={"agent": "policy", "action": "evaluate", "case_id": a}).status_code == 404
        assert alice.get(f"/api/cases/{s05['id']}").json()["status"] == "on_hold"  # nothing Bob did landed

        # ---- lists never mix workspaces
        assert bob.get("/api/cases").json()["total"] == 40  # only his seeded history
        assert not {c["id"] for c in bob.get("/api/cases").json()["items"]} & {a, s05["id"]}
        assert bob.get("/api/exports").json() == [] and bob.get("/api/approved").json()["total"] == 0
        assert bob.get("/api/notifications").json() == []
        assert len(bob.get("/api/vendors").json()) == 7 and len(alice.get("/api/vendors").json()) == 8
        bob_po = next(p for p in bob.get("/api/pos").json() if p["po_number"] == "PO-1002")
        assert (Decimal(bob_po["consumed"]), Decimal(po["consumed"])) == (0, Decimal("4415.00"))
        assert bob.get("/api/dashboard/metrics").json()["kpis"]["in_review"] == 0
        bob_stats, alice_stats = bob.get("/api/config/agent-stats").json(), alice.get("/api/config/agent-stats").json()
        assert "intake" not in bob_stats and alice_stats["intake"]["runs"] >= 1  # sample history has no agent runs

        # ---- config: Alice's edit is hers alone
        assert alice.get("/api/config").json()["hash"] != bob.get("/api/config").json()["hash"]
        assert bob.get("/api/config").json()["hash"] == app.state.base_config.hash
        assert bob.get("/api/config/rules.yaml").json()["versions"] == []

        # ---- numbering is per workspace
        b01 = bob.post("/api/fixtures/S01_happy/run").json()
        assert b01["ref"] == s01["ref"]  # both are their workspace's first run
        assert bob.get(f"/api/cases/by-ref/{s01['ref']}").json()["id"] == b01["id"]  # same ref, his own case
        assert alice.get(f"/api/cases/by-ref/{s01['ref']}").json()["id"] == a
        wait_status(bob, b01["id"], SETTLED, timeout=60)
        assert bob.post("/api/exports", json={"case_ids": [b01["id"]], "mode": "batch"}).json()["ref"] == batch["ref"]


# ---------------------------------------------------------------- the session guard
@pytest.fixture
def two_workspaces(engine_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    for user in (ALICE, BOB):
        add_user(engine_factory, user)
        with bind_owner(engine_factory, user.id)() as s:
            load_master(s, MASTER_DIR)
            s.commit()
    return engine_factory


def test_owner_bound_session_hides_other_workspaces_even_without_scoped(two_workspaces):
    sf = two_workspaces
    with sf() as s:  # unbound (system) session: both workspaces
        assert s.scalar(select(func.count()).select_from(Vendor)) == 14
        alice_po = s.scalars(select(PurchaseOrder).where(PurchaseOrder.owner_id == ALICE.id)).first()
    with bind_owner(sf, BOB.id)() as s:
        assert s.scalar(select(func.count()).select_from(Vendor)) == 7  # no scoped(): the guard filters
        assert {v.owner_id for v in s.scalars(select(Vendor)).all()} == {BOB.id}
        assert s.get(PurchaseOrder, alice_po.id) is None
        joined = s.execute(select(PurchaseOrder.po_number, Vendor.code).join(Vendor)).all()
        assert len(joined) == 7


def test_session_guard_refuses_cross_workspace_writes(two_workspaces):
    sf = two_workspaces
    with bind_owner(sf, BOB.id)() as s:
        s.add(Vendor(code="V900", legal_name="Planted", owner_id=ALICE.id))
        with pytest.raises(TenancyError, match="another owner"):
            s.flush()
    with bind_owner(sf, BOB.id)() as s:
        v = s.scalars(select(Vendor)).first()
        v.owner_id = ALICE.id
        with pytest.raises(TenancyError, match="cannot change"):
            s.flush()
    with sf() as s:  # system session: a tenant row needs an explicit owner
        s.add(Vendor(code="V901", legal_name="Orphan"))
        with pytest.raises(TenancyError, match="without an owner"):
            s.flush()
    with bind_owner(sf, BOB.id)() as s:  # stamped from the session
        s.add(Vendor(code="V902", legal_name="Bob's new vendor"))
        s.flush()
        assert s.scalars(select(Vendor).where(Vendor.code == "V902")).one().owner_id == BOB.id


def test_scoped_filters_every_table_and_keeps_outer_joins(two_workspaces):
    sf = two_workspaces
    with sf() as s:  # unbound, so only scoped() filters here
        assert len(s.scalars(scoped(select(Vendor), ALICE.id)).all()) == 7
        assert s.scalar(scoped(select(func.count()).select_from(PurchaseOrder), BOB.id)) == 7
        orphan = Case(owner_id=ALICE.id, ref="X-1", source="upload", status="received")
        s.add(orphan)
        s.commit()
        rows = s.execute(scoped(select(Case.ref, Vendor.code).outerjoin(Vendor, Vendor.id == Case.vendor_id),
                                ALICE.id)).all()
        assert rows == [("X-1", None)]  # a case without a vendor survives the outer join
        assert s.execute(scoped(select(Case.ref), BOB.id)).all() == []


def test_owner_storage_is_confined_to_its_prefix(tmp_path: Path):
    root = LocalStorage(tmp_path)
    alice, bob = OwnerStorage(root, ALICE.id), OwnerStorage(root, BOB.id)
    key = alice.put("cases/1/a.pdf", b"%PDF-A")
    assert key == f"u/{ALICE.id}/cases/1/a.pdf" and alice.get(key) == b"%PDF-A" and alice.get("cases/1/a.pdf") == b"%PDF-A"
    for bad in (key, f"u/{ALICE.id}/../{BOB.id}/x", "../x", "cases/../../x"):
        with pytest.raises((ReadOnlyKeyError, ValueError)):
            bob.get(bad)
    with pytest.raises(ReadOnlyKeyError):
        bob.put(key, b"overwrite")
    with pytest.raises(ReadOnlyKeyError):
        bob.put("baseline/fixtures/x.pdf", b"x")
    assert bob.delete_prefix("") == 0 and alice.get(key) == b"%PDF-A"
    assert alice.delete_prefix("") == 1
    with pytest.raises(FileNotFoundError):
        alice.get(key)
