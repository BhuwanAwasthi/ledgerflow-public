"""D1: generated fixtures match data/scenarios.yaml (the single source of truth)."""

from __future__ import annotations

import csv
import json
import re
from decimal import Decimal
from pathlib import Path

import pymupdf
import pytest

from app.llm_schemas import SCHEMAS, InvoiceExtraction
from app.scenarios import Scenarios, load_scenarios
from app.settings import BACKEND_DIR, REPO_ROOT
from scripts.generate_fixtures import generate, render_invoices

DATA = REPO_ROOT / "data"
SCENARIOS_YAML = DATA / "scenarios.yaml"
LLM_DIR = BACKEND_DIR / "tests" / "llm_fixtures"


@pytest.fixture(scope="module")
def sc() -> Scenarios:
    return load_scenarios(SCENARIOS_YAML)


def comment_totals() -> list[tuple[str, int, dict[str, Decimal]]]:
    """(scenario id, invoice index, {subtotal|tax|total: value}) from `# subtotal ..., tax ..., total ...`
    comments on tax_rate_pct lines in scenarios.yaml."""
    out: list[tuple[str, int, dict[str, Decimal]]] = []
    sid, idx = "", -1
    for line in SCENARIOS_YAML.read_text(encoding="utf-8").splitlines():
        if m := re.match(r"^  - id: (\S+)", line):
            sid, idx = m.group(1), -1
        if "tax_rate_pct:" in line and sid:
            idx += 1
            comment = line.partition("#")[2]
            vals = {k: Decimal(v) for k, v in re.findall(r"\b(subtotal|tax|(?<!sub)total) ([\d.]+)", comment)}
            if vals:
                out.append((sid, idx, vals))
    return out


def pdf_text(name: str) -> list[str]:
    with pymupdf.open(DATA / "fixtures" / name) as doc:
        return [page.get_text() for page in doc]


def test_comment_totals_are_parsed():
    found = {(sid, i) for sid, i, _ in comment_totals()}
    assert {("S01_happy", 0), ("S02b_split_part2", 0), ("S05_unknown_vendor", 0), ("S06_bank_change", 0),
            ("S10_bundle", 0)} <= found


def test_computed_totals_equal_yaml_comments(sc: Scenarios):
    for sid, idx, vals in comment_totals():
        inv = sc.invoices(sc.scenario(sid))[idx]
        computed = {"subtotal": inv.subtotal, "tax": inv.tax, "total": inv.total}
        for key, val in vals.items():
            assert computed[key] == val, f"{sid}[{idx}] {key}: computed {computed[key]} vs comment {val}"


def test_pdf_totals_equal_yaml_comments(sc: Scenarios):
    """Every comment value is printed on the right page of the clean PDF (the text layer is the truth a
    parser would see)."""
    checked = 0
    for sid, idx, vals in comment_totals():
        scenario = sc.scenario(sid)
        assert scenario.render == "clean"
        page = pdf_text(scenario.file)[idx]
        for key, val in vals.items():
            assert f"{val:,.2f}" in page, f"{sid} page {idx + 1}: {key} {val:,.2f} not printed"
            checked += 1
    assert checked >= 11


def test_every_clean_pdf_prints_its_totals_and_numbers(sc: Scenarios):
    for scenario in sc.scenarios:
        if scenario.render != "clean":
            continue
        pages = pdf_text(scenario.file)
        invoices = sc.invoices(scenario)
        assert len(pages) == len(invoices), scenario.id
        for page, inv in zip(pages, invoices, strict=True):
            for amount in (inv.subtotal, inv.tax, inv.total):
                assert f"{amount:,.2f}" in page, f"{scenario.id}: {amount} missing"
            assert inv.invoice_number and inv.invoice_number in page
            if inv.po_reference:
                assert inv.po_reference in page
            if inv.bank_last4:
                assert inv.bank_last4 in page


def test_scans_have_no_text_layer(sc: Scenarios):
    scans = [s for s in sc.scenarios if s.render == "scan"]
    assert {s.id for s in scans} == {"S04_duplicate_scan", "S09_missing_fields"}
    for scenario in scans:
        with pymupdf.open(DATA / "fixtures" / scenario.file) as doc:
            assert doc.page_count == 1
            page = doc[0]
            assert page.get_text().strip() == "", f"{scenario.id} must be image-only"
            assert len(page.get_images()) == 1


def test_bundle_has_two_invoices(sc: Scenarios):
    pages = pdf_text(sc.scenario("S10_bundle").file)
    assert len(pages) == 2
    assert "INV-A-2240" in pages[0] and "CL-781" not in pages[0]
    assert "CL-781" in pages[1] and "INV-A-2240" not in pages[1]


def test_s05_supporting_files():
    w9 = pdf_text("S05_novaprint_W9.pdf")[0]
    assert "Nova Print Studio LLC" in w9 and "78-9012345" in w9
    letter = pdf_text("S05_novaprint_bank_letter.pdf")[0]
    assert "Nova Print Studio LLC" in letter and "6070" in letter


def _csv(name: str) -> list[dict[str, str]]:
    with (DATA / "master" / name).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_master_csvs(sc: Scenarios):
    vendors = {r["code"]: r for r in _csv("vendors.csv")}
    assert len(vendors) == 7 and "Nova" not in json.dumps(vendors)
    assert vendors["V004"]["non_po_allowed"] == "true" and vendors["V007"]["status"] == "inactive"
    pos = {r["po_number"]: r for r in _csv("purchase_orders.csv")}
    assert pos["PO-1001"]["amount"] == "10000.00" and pos["PO-1002"]["amount"] == "4415.00"
    assert pos["PO-1004"]["notes"] == "Raised from quote Q-77"
    lines = _csv("po_lines.csv")
    for po, row in pos.items():
        assert sum(Decimal(r["amount"]) for r in lines if r["po_number"] == po) == Decimal(row["amount"])
    assert len(_csv("grns.csv")) == len(sc.grns) and len(_csv("gl_accounts.csv")) == len(sc.gl_accounts)


def _llm(sid: str, agent: str) -> dict:
    return json.loads((LLM_DIR / f"{sid}__{agent}.json").read_text(encoding="utf-8"))


def test_llm_fixtures_validate_against_schemas(sc: Scenarios):
    files = sorted(LLM_DIR.glob("*.json"))
    ids = {s.id for s in sc.scenarios}
    for path in files:
        body = json.loads(path.read_text(encoding="utf-8"))
        sid, agent = path.stem.split("__")
        assert body["scenario"] == sid in ids and body["agent"] == agent
        assert body["calls"], path.name
        for call in body["calls"]:
            SCHEMAS[call["schema"]].model_validate(call["output"])
    for s in sc.scenarios:  # every scenario is classified, extracted and explained
        for agent in ("classifier", "extraction", "decision"):
            assert (LLM_DIR / f"{s.id}__{agent}.json").exists(), f"{s.id}__{agent}"


def test_llm_extractions_match_scenarios(sc: Scenarios):
    for scenario in sc.scenarios:
        calls = _llm(scenario.id, "extraction")["calls"]
        invoices = sc.invoices(scenario)
        assert len(calls) == len(invoices)
        for call, inv in zip(calls, invoices, strict=True):
            ext = InvoiceExtraction.model_validate(call["output"])
            assert (ext.subtotal, ext.tax_total, ext.total) == (inv.subtotal, inv.tax, inv.total)
            assert ext.po_reference == inv.po_reference
            assert ext.remit_bank and ext.remit_bank.account_last4 == inv.bank_last4
            assert sum(ln.amount or 0 for ln in ext.lines) == inv.subtotal
            if ext.invoice_number:
                assert ext.location.invoice_number is not None
            if scenario.render == "scan":  # extraction prompt: scanned values get at most 0.8
                assert all(v is None or v <= 0.8 for v in ext.confidence.model_dump().values())
    s04 = InvoiceExtraction.model_validate(_llm("S04_duplicate_scan", "extraction")["calls"][0]["output"])
    assert s04.invoice_number == "INV A2207"
    s09 = InvoiceExtraction.model_validate(_llm("S09_missing_fields", "extraction")["calls"][0]["output"])
    assert s09.invoice_number is None and s09.invoice_date is None


def test_llm_fixture_edge_cases():
    s10 = _llm("S10_bundle", "classifier")["calls"][0]["output"]["documents"]
    assert [(d["page_from"], d["page_to"]) for d in s10] == [(1, 1), (2, 2)]
    s03 = _llm("S03_implied_po", "po_matching")["calls"][0]["output"]["candidates"]
    assert [c["po_number"] for c in s03] == ["PO-1004", "PO-1006"]
    assert s03[0]["line_similarity"] >= 0.85 > s03[1]["line_similarity"]
    remark = _llm("S02b_split_part2", "reconciler")["calls"][0]
    assert [op["op"] for op in remark["output"]["ops"]] == ["set_po_amount_note", "set_gl", "override_rule"]
    decisions = {c["when"]["decision"] for c in _llm("S02b_split_part2", "decision")["calls"]}
    assert decisions == {"needs_review", "auto_approve"}
    gl = _llm("S07_non_po", "po_matching")["calls"][0]["output"]["lines"]
    assert {ln["gl_code"] for ln in gl} == {"6100"}
    assert "9910" in _llm("S06_bank_change", "comms")["calls"][0]["output"]["body"]


def test_bboxes_point_at_printed_values(sc: Scenarios):
    """For text PDFs the recorded bbox must contain the printed value (what the review UI highlights)."""
    for sid in ("S01_happy", "S03_implied_po", "S05_unknown_vendor", "S08_price_variance"):
        scenario = sc.scenario(sid)
        ext = _llm(sid, "extraction")["calls"][0]["output"]
        with pymupdf.open(DATA / "fixtures" / scenario.file) as doc:
            page = doc[0]
            w, h = page.rect.width, page.rect.height
            for fld in ("invoice_number", "total"):
                b = ext["location"][fld]["bbox"]
                clip = pymupdf.Rect(b[0] * w - 1, b[1] * h - 1, b[2] * w + 1, b[3] * h + 1)
                assert page.get_textbox(clip).strip(), f"{sid}.{fld}: empty bbox"
                hits = page.search_for(ext[fld] if fld == "invoice_number" else f"{Decimal(str(ext[fld])):,.2f}")
                assert any(clip.contains(r) or clip.intersects(r) for r in hits), f"{sid}.{fld}"


def test_generation_is_deterministic_and_committed_files_are_current(tmp_path: Path):
    written = generate(SCENARIOS_YAML, tmp_path / "data", tmp_path / "llm", log=lambda _: None)
    assert len(written) > 40
    sc = load_scenarios(SCENARIOS_YAML)
    scans = {s.file for s in sc.scenarios if s.render == "scan"}
    for p in written:
        if p.parent.name == "llm":
            committed = LLM_DIR / p.name
        else:
            committed = DATA / p.parent.name / p.name
        if p.name in scans:  # JPEG bytes may differ across Pillow builds; content is checked above
            continue
        assert committed.read_bytes() == p.read_bytes(), f"{committed} is stale: run `make fixtures`"
    assert {p.name for p in (tmp_path / "llm").glob("*.json")} == {p.name for p in LLM_DIR.glob("*.json")}


def test_s04_scan_prints_the_reformatted_number(sc: Scenarios):
    """The scan is image-only, so check the layout it was rasterised from."""
    s04 = sc.scenario("S04_duplicate_scan")
    pdf, _ = render_invoices(sc, s04, s04.override["invoice_number_printed"])
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        text = doc[0].get_text()
    assert "INV A2207" in text and "INV-A-2207" not in text and "4,779.24" in text
