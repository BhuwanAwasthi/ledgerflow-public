"""Vendor / PO CSV export and import (/api/master/{entity}.csv, /api/master/{entity}/import)."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from tests.conftest import MASTER_DIR

ORDER = ("vendors", "purchase_orders", "po_lines")


def rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def upload(client: TestClient, entity: str, text: str, actor: str = "Priya (Procurement)"):
    return client.post(
        f"/api/master/{entity}/import",
        files={"file": (f"{entity}.csv", text.encode("utf-8"), "text/csv")},
        data={"actor": actor},
    )


def master(entity: str) -> str:
    return (MASTER_DIR / f"{entity}.csv").read_text(encoding="utf-8")


@pytest.fixture
def client(test_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(test_settings)) as c:
        for entity in ORDER:
            r = upload(c, entity, master(entity))
            assert r.status_code == 200, r.text
            assert r.json()["created"] == len(rows(master(entity)))
        yield c


def test_export_round_trips_the_master_files(client: TestClient):
    for entity in ORDER:
        r = client.get(f"/api/master/{entity}.csv")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
        key = (lambda d: (d["po_number"], int(d["line_no"]))) if entity == "po_lines" else (lambda d: next(iter(d.values())))
        assert sorted(rows(r.text), key=key) == sorted(rows(master(entity)), key=key)
        again = upload(client, entity, r.text).json()
        assert (again["created"], again["updated"], again["unchanged"]) == (0, 0, len(rows(master(entity))))


def test_po_amount_change_is_amended_with_an_audit_row(client: TestClient):
    text = client.get("/api/master/purchase_orders.csv").text
    edited = text.replace("PO-1001,V002,USD,10000.00", "PO-1001,V002,USD,12000.00")
    r = upload(client, "purchase_orders", edited)
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 1 and r.json()["notes"] == ["PO-1001 amount 10000.00 -> 12000.00"]

    po = next(p for p in client.get("/api/pos").json() if p["po_number"] == "PO-1001")
    detail = client.get(f"/api/pos/{po['id']}").json()
    assert detail["amount"] == "12000.00"
    [a] = detail["amendments"]
    assert (a["amended_by"], a["old_amount"], a["new_amount"], a["source"], a["case_id"]) == (
        "Priya (Procurement)", "10000.00", "12000.00", "import", None
    )


def test_invalid_file_is_rejected_whole(client: TestClient):
    header = master("vendors").splitlines()[0]
    valid = "V900,New Vendor Ltd,,99-0000000,US,new.example,1234,,services,approved,6500,false"  # fine on its own
    broken = "V901,Broken Vendor,,,US,,,,,maybe,,yes"
    bad = f"{header}\n{valid}\n{broken}\n"
    r = upload(client, "vendors", bad)
    assert r.status_code == 422
    errors = r.json()["detail"]["errors"]
    assert any("row 3" in e and "status 'maybe'" in e for e in errors)
    assert any("non_po_allowed" in e for e in errors)
    codes = {row["code"] for row in rows(client.get("/api/master/vendors.csv").text)}
    assert "V900" not in codes  # nothing written


def test_bank_details_of_an_existing_vendor_cannot_change_by_import(client: TestClient):
    text = client.get("/api/master/vendors.csv").text
    edited = text.replace("acme-office.example,4321,", "acme-office.example,9999,")
    r = upload(client, "vendors", edited)
    assert r.status_code == 422
    assert "verified bank letter" in r.json()["detail"]["errors"][0]


def test_unknown_header_and_missing_parent_are_explained(client: TestClient):
    r = upload(client, "po_lines", "po_number,line_no,description\nPO-1,1,x\n")
    assert r.status_code == 422 and "missing qty" in r.json()["detail"]["errors"][0]
    header = master("po_lines").splitlines()[0]
    r = upload(client, "po_lines", f"{header}\nPO-9999,1,Widget,2,5.00,10.00,6200\n")
    assert r.status_code == 422 and "PO PO-9999 does not exist" in r.json()["detail"]["errors"][0]
    assert client.get("/api/master/suppliers.csv").status_code == 422
