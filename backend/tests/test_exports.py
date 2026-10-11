"""Phase B4: ERP template rendering units, and exports through the API (batch file, ZIP, re-export guard,
downloads, audit pack) on cases run by the real agents with LLM_BACKEND=fake."""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date

import pytest
import yaml
from fastapi.testclient import TestClient

from app.agents import policy
from app.config_loader import ErpProfile, build_config, read_config_files
from app.db import init_db, make_engine, make_session_factory
from app.exports.render import diff_rows, money, render_rows, to_bytes
from app.main import create_app
from app.seed import load_master
from tests.conftest import CONFIG_DIR, MASTER_DIR
from tests.test_api import wait_status


def profile(pid: str) -> ErpProfile:
    return build_config(read_config_files(CONFIG_DIR)).erp_profiles[pid]


CTX = {
    "case": {"ref": "INV-2026-0001", "decision": "auto_approve", "approved_by": None},
    "vendor": {"code": "V001", "legal_name": "Acme Office Supplies Inc"},
    "inv": {"invoice_number": "INV-A-2207", "invoice_date": "2026-10-02", "due_date": None, "payment_terms": None, "currency": "USD"},
    "po": None,
    "lines": [
        {"description": "Copy paper", "qty": "50", "unit_price": "6.5", "amount": "325", "gl_code": "6200", "department": None, "tax_amount": None},
        {"description": "Chair", "qty": "10", "unit_price": "245", "amount": "2450", "gl_code": None, "department": None, "tax_amount": None},
    ],
    "version": 1,
}


def test_filters_rows_and_required_validation():
    assert (money("1234.5"), money(None), money("")) == ("1234.50", "", "")
    r = render_rows(profile("sage_intacct"), CTX)
    assert len(r.rows) == 2
    row = r.rows[0]
    assert (row["BILL_NO"], row["VENDOR_ID"], row["DATE_CREATED"], row["DATE_DUE"]) == ("INV-A-2207", "V001", "10/02/2026", "")
    assert (row["AMOUNT"], row["LINE_NO"], row["PONUMBER"], row["TERMNAME"], row["TAX_AMOUNT"]) == ("325.00", "1", "", "", "")
    assert r.missing == [{"row": 1, "column": "ACCT_NO"}] and not r.valid  # no GL on line 2
    generic = render_rows(profile("generic_xlsx"), CTX).rows[0]
    assert (generic["Invoice Date"], generic["PO"], generic["Approved By"]) == ("2026-10-02", "NON-PO", "auto")


def test_header_grain_and_files():
    p = profile("quickbooks_online").model_copy(update={"grain": "header"})
    assert len(render_rows(p, CTX).rows) == 1
    data, mime = to_bytes(profile("quickbooks_online"), render_rows(profile("quickbooks_online"), CTX).rows)
    assert mime == "text/csv" and data.decode().splitlines()[0].startswith("Bill No,Supplier,Bill Date")
    xdata, xmime = to_bytes(profile("generic_xlsx"), render_rows(profile("generic_xlsx"), CTX).rows)
    assert xmime.endswith("spreadsheetml.sheet") and xdata[:2] == b"PK"


def test_cell_diff():
    a = [{"A": "1", "B": "x"}, {"A": "2", "B": "y"}]
    b = [{"A": "1", "B": "x"}, {"A": "2", "B": "z"}, {"A": "3", "B": "w"}]
    assert diff_rows(None, a) is None
    assert diff_rows(a, b) == {
        "changed": [{"row": 1, "column": "B", "old": "y", "new": "z"}],
        "added_rows": [2],
        "removed_rows": [],
        "unchanged": False,
    }
    assert diff_rows(a, a)["unchanged"] is True


def test_sandbox_blocks_unsafe_template():
    p = profile("sage_intacct")
    cols = [*p.columns]
    cols[1] = cols[1].model_copy(update={"value": "{{ inv.__class__.__mro__ }}"})
    from jinja2.exceptions import SecurityError

    with pytest.raises(SecurityError):
        render_rows(p.model_copy(update={"columns": cols}), CTX)


def test_batch_export_reexport_guard_and_downloads(test_settings, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(policy, "today", lambda: date(2026, 10, 11))
    settings = test_settings.model_copy(update={"force_stub_agents": False, "llm_backend": "fake"})
    engine = make_engine(settings.database_url)
    init_db(engine)
    with make_session_factory(engine)() as s:
        load_master(s, MASTER_DIR)
        s.commit()
    engine.dispose()

    with TestClient(create_app(settings)) as client:
        ids = []
        for sid in ("S01_happy", "S02a_split_part1"):
            case = client.post(f"/api/fixtures/{sid}/run").json()
            assert wait_status(client, case["id"], {"approved", "failed"}, timeout=60)["status"] == "approved"
            ids.append(case["id"])

        lib = client.get("/api/approved", params={"exported": "false"}).json()
        assert {c["id"] for c in lib["items"]} == set(ids) and all(c["approved_by"] == "auto" for c in lib["items"])

        detail = client.get(f"/api/cases/{ids[0]}").json()
        assert [(t["version"], t["status"]) for t in detail["templates"]] == [(1, "final")]
        single = client.get(f"/api/cases/{ids[0]}/template/1/download")
        assert single.status_code == 200 and single.headers["content-type"].startswith("text/csv")
        assert f'SI_APBILL_{detail["ref"]}_v1.csv' in single.headers["content-disposition"]

        r = client.post("/api/exports", json={"case_ids": ids, "erp_profile": "sage_intacct", "mode": "batch"})
        assert r.status_code == 201, r.text
        batch = r.json()
        assert batch["ref"] == "EXP-0001" and batch["filename"] == "SI_APBILL_BATCH_EXP-0001.csv"
        body = client.get(batch["download_url"]).content.decode()
        rows = list(csv.reader(io.StringIO(body)))
        assert rows[0][:3] == ["DONOTIMPORT", "BILL_NO", "VENDOR_ID"]
        assert sum(1 for r in rows if r[1] == "BILL_NO") == 1  # header once
        bills = [r[1] for r in rows[1:]]
        assert bills == ["INV-A-2207", "INV-A-2207", "INV-A-2207", "BL-0091"]  # both bills' lines
        amounts = [r[13] for r in rows[1:]]
        assert amounts == ["325.00", "1640.00", "2450.00", "6000.00"]

        assert client.get("/api/approved", params={"exported": "false"}).json()["total"] == 0
        assert client.get(f"/api/cases/{ids[0]}").json()["status"] == "exported"

        again = client.post("/api/exports", json={"case_ids": ids, "mode": "batch"})
        assert again.status_code == 409 and "force=true" in again.json()["detail"]

        forced = client.post("/api/exports", json={"case_ids": ids, "mode": "zip", "force": True})
        assert forced.status_code == 201, forced.text
        assert forced.json()["ref"] == "EXP-0002" and forced.json()["filename"] == "AP_EXPORT_EXP-0002_sage_intacct.zip"
        zf = zipfile.ZipFile(io.BytesIO(client.get(forced.json()["download_url"]).content))
        assert sorted(zf.namelist()) == sorted(f"SI_APBILL_{client.get(f'/api/cases/{i}').json()['ref']}_v1.csv" for i in ids)

        xlsx = client.post("/api/exports", json={"case_ids": [ids[1]], "erp_profile": "quickbooks_online", "mode": "batch", "force": True})
        assert xlsx.status_code == 201 and xlsx.json()["filename"] == "QBO_BILL_BATCH_EXP-0003.csv"
        qbo = list(csv.reader(io.StringIO(client.get(xlsx.json()["download_url"]).content.decode())))
        assert qbo[1][:3] == ["BL-0091", "Brightline Software Inc", "09/15/2026"]  # re-rendered in the other profile

        assert [b["ref"] for b in client.get("/api/exports").json()] == ["EXP-0003", "EXP-0002", "EXP-0001"]
        assert client.post("/api/exports", json={"case_ids": [999], "mode": "batch"}).status_code == 404
        assert client.post("/api/exports", json={"case_ids": ids, "erp_profile": "nope"}).status_code == 422

        pack = client.get(f"/api/cases/{ids[0]}/audit-pack")
        assert pack.status_code == 200 and pack.content[:4] == b"%PDF"
        doc = detail["documents"][0]
        assert client.get(f"/api/cases/{ids[0]}/documents/{doc['id']}/file").content[:4] == b"%PDF"
        assert client.get(f"/api/cases/{ids[0]}/documents/{doc['id']}/pages/1.png").content[:4] == b"\x89PNG"

        pos = {p["po_number"]: p for p in client.get("/api/pos").json()}
        assert (pos["PO-1002"]["consumed"], pos["PO-1002"]["remaining"]) == ("4415.00", "0.00")
        assert client.get("/api/notifications", params={"audience": "reviewer"}).json()


def test_portal_unknown_token_is_404(test_settings):
    with TestClient(create_app(test_settings)) as client:
        assert client.get("/portal/nope").status_code == 404
        assert client.post("/portal/nope/upload", files=[("files", ("a.pdf", b"%PDF-1.4", "application/pdf"))]).status_code == 404


def test_app_yaml_exports_section_is_loaded():
    cfg = build_config(read_config_files(CONFIG_DIR))
    raw = yaml.safe_load((CONFIG_DIR / "app.yaml").read_text(encoding="utf-8"))
    assert cfg.app.exports.batch_ref_prefix == raw["exports"]["batch_ref_prefix"]
