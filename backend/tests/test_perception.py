"""Phase B2: intake, classifier, extraction and normaliser on the D1 fixtures, with FakeLLM."""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import pymupdf
from PIL import Image, ImageDraw
from sqlalchemy import select

from app import pdf
from app.agents.normaliser import normalise_invoice_number, parse_date
from app.models import Case, Document, Extraction, StageEvent, Vendor, VendorMemory
from tests.conftest import LLM_FIXTURES, Pipeline, patch_yaml

DATE_FORMATS = ["%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y"]
NUMERIC = {
    "ISO": ["%Y-%m-%d"],
    "MDY": ["%m/%d/%Y"],
    "DMY": ["%d/%m/%Y"],
}


def events(p: Pipeline, case_id: int, stage: str | None = None) -> list[StageEvent]:
    with p.sf() as s:
        q = select(StageEvent).where(StageEvent.case_id == case_id).order_by(StageEvent.seq)
        rows = s.scalars(q).all()
    return [e for e in rows if stage is None or e.stage_id == stage]


def done(p: Pipeline, case_id: int, stage: str) -> StageEvent:
    ev = [e for e in events(p, case_id, stage) if e.status in ("completed", "waiting")]
    assert ev, (
        f"stage {stage} did not complete: {[(e.stage_id, e.status, e.output) for e in events(p, case_id)]}"
    )
    return ev[-1]


def case(p: Pipeline, case_id: int) -> Case:
    with p.sf() as s:
        c = s.get(Case, case_id)
        assert c is not None
        return c


def fixture_output(scenario: str, agent: str, i: int = 0) -> dict[str, Any]:
    data = json.loads((LLM_FIXTURES / f"{scenario}__{agent}.json").read_text(encoding="utf-8"))
    return data["calls"][i]["output"]


async def test_s01_clean_pdf_end_to_end(pipeline: Pipeline):
    await pipeline.start()
    cid = await pipeline.run_fixture("S01_acme_INV-A-2207.pdf")
    assert case(pipeline, cid).status == "approved"  # downstream agents are still stubs

    intake = done(pipeline, cid, "intake")
    assert intake.outcome == "ok" and intake.output["documents"][0]["has_text_layer"] is True
    assert intake.reasoning and "1 page(s), text layer found" in intake.reasoning[0]

    classify = done(pipeline, cid, "classify")
    assert classify.outcome == "has_invoice"
    assert classify.output["documents"][0]["label"] == "invoice"
    assert classify.metrics["llm_calls"] and "tokens_in" in classify.metrics

    ext = done(pipeline, cid, "extract")
    assert ext.outcome == "ok"
    meta = ext.output["field_meta"]
    expected = fixture_output("S01_happy", "extraction")["location"]
    for field in ("invoice_number", "po_reference", "total", "subtotal", "tax_total", "invoice_date"):
        assert meta[field]["source"] == "text_layer", field
        assert meta[field]["page"] == 1
        for got, want in zip(meta[field]["bbox"], expected[field]["bbox"], strict=True):
            assert abs(got - want) < 0.02, (field, meta[field]["bbox"], expected[field]["bbox"])
    assert meta["shipping"]["confidence"] is None and meta["shipping"]["bbox"] is None

    norm = done(pipeline, cid, "normalise").output["invoice"]
    assert norm["invoice_number_normalised"] == "INVA2207"
    assert norm["invoice_date"] == "2026-10-02" and norm["due_date"] == "2026-11-01"
    assert (norm["subtotal"], norm["tax_total"], norm["total"]) == ("4415.00", "364.24", "4779.24")
    assert norm["currency"] == "USD" and norm["arithmetic_ok"] is True
    assert [ln["amount"] for ln in norm["lines"]] == ["325.00", "1640.00", "2450.00"]

    c = case(pipeline, cid)
    assert str(c.total) == "4779.24" and c.currency == "USD" and c.confidence and c.confidence >= 0.9
    with pipeline.sf() as s:
        row = s.scalars(select(Extraction).where(Extraction.case_id == cid)).one()
        assert row.version == 1 and row.data["invoice_number"] == "INV-A-2207"
    # renders are stored for the review workspace
    with pipeline.sf() as s:
        doc = s.scalars(select(Document).where(Document.case_id == cid)).one()
    png = pipeline.storage.get(f"renders/{doc.sha256}/pages/1.png")
    assert png.startswith(b"\x89PNG")


async def test_s04_scan_uses_model_boxes_and_normalises_number(pipeline: Pipeline):
    await pipeline.start()
    cid = await pipeline.run_fixture("S04_acme_scan_duplicate.pdf")
    intake = done(pipeline, cid, "intake")
    assert intake.output["documents"][0]["has_text_layer"] is False
    assert "scanned" in intake.reasoning[0]
    ext = done(pipeline, cid, "extract")
    meta = ext.output["field_meta"]
    assert meta["invoice_number"]["source"] == "model"
    assert (
        meta["invoice_number"]["bbox"]
        == fixture_output("S04_duplicate_scan", "extraction")["location"]["invoice_number"]["bbox"]
    )
    assert any("Scanned document" in r for r in ext.reasoning)
    norm = done(pipeline, cid, "normalise").output["invoice"]
    assert (
        norm["invoice_number_normalised"] == "INVA2207"
    )  # equals S01's: the duplicate rule (B3) keys on this


async def test_s04_low_confidence_outcome_from_config(pipeline: Pipeline, config_files):
    files = patch_yaml(
        config_files, "agents/extraction.yaml", lambda d: d["params"].update(low_confidence_below=0.9)
    )
    await pipeline.start(files)
    cid = await pipeline.run_fixture("S04_acme_scan_duplicate.pdf")
    ext = done(pipeline, cid, "extract")
    assert ext.outcome == "low_confidence"
    assert any(r.startswith("Low confidence:") for r in ext.reasoning)
    assert done(pipeline, cid, "normalise").outcome == "ok"  # low_confidence still continues


async def test_s09_missing_fields_reported(pipeline: Pipeline):
    await pipeline.start()
    cid = await pipeline.run_fixture("S09_coastal_scan_missing.pdf")
    ext = done(pipeline, cid, "extract")
    assert ext.output["invoice"]["invoice_number"] is None
    assert any("Not found on the document: invoice_number, invoice_date" in r for r in ext.reasoning)
    norm = done(pipeline, cid, "normalise").output["invoice"]
    assert norm["invoice_number_normalised"] is None and norm["invoice_date"] is None


async def test_s10_bundle_splits_into_child_cases(pipeline: Pipeline):
    await pipeline.start()
    cid = await pipeline.run_fixture("S10_bundle_two_invoices.pdf")
    parent = case(pipeline, cid)
    assert parent.status == "split"
    cls = done(pipeline, cid, "classify")
    assert cls.outcome == "split" and len(cls.output["child_cases"]) == 2
    assert not events(pipeline, cid, "extract")

    with pipeline.sf() as s:
        children = s.scalars(select(Case).where(Case.parent_case_id == cid).order_by(Case.id)).all()
        docs = {c.id: s.scalars(select(Document).where(Document.case_id == c.id)).one() for c in children}
    assert len(children) == 2
    numbers = []
    for n, child in enumerate(children, start=1):
        assert child.status == "approved", child.status
        assert child.config_hash == parent.config_hash
        d = docs[child.id]
        assert (d.page_from, d.page_to, d.label) == (n, n, "invoice")
        ext = done(pipeline, child.id, "extract")
        assert ext.output["pages"] == [n, n]
        assert ext.output["field_meta"]["invoice_number"]["page"] == n
        assert ext.output["field_meta"]["invoice_number"]["source"] == "text_layer"
        numbers.append(done(pipeline, child.id, "normalise").output["invoice"]["invoice_number"])
        assert "already labelled" in done(pipeline, child.id, "classify").reasoning[0]
    assert numbers == ["INV-A-2240", "CL-781"]
    # the bundle was classified once (children reuse the labels)
    assert [c.agent for c in pipeline.llm.calls].count("classifier") == 1


async def test_vendor_memory_hints_are_injected(pipeline: Pipeline):
    await pipeline.start()
    with pipeline.sf() as s:
        v = s.scalars(select(Vendor).where(Vendor.code == "V001")).one()
        s.add(VendorMemory(vendor_id=v.id, hint="Toner is billed per cartridge, not per box."))
        s.commit()
    cid = await pipeline.run_fixture("S01_acme_INV-A-2207.pdf", email="billing@acme-office.example")
    req = next(r for r in pipeline.llm.calls if r.agent == "extraction")
    assert "Toner is billed per cartridge, not per box." in req.instructions
    ext = done(pipeline, cid, "extract")
    assert ext.output["vendor_hints"] == ["Toner is billed per cartridge, not per box."]
    assert any("learned hint" in r for r in ext.reasoning)


# ---------------------------------------------------------------- intake edge cases
def _png() -> bytes:
    img = Image.new("RGB", (400, 200), "white")
    ImageDraw.Draw(img).text((20, 20), "Invoice INV-1", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _docx(text: str) -> bytes:
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    xml = f'<w:document xmlns:w="{ns}"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def _pdf(pages: int) -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"Page {i + 1} of a long contract")
    return doc.tobytes()


async def test_intake_converts_image_and_docx(pipeline: Pipeline):
    await pipeline.start()
    cid = await pipeline.run(
        ("scan.png", _png(), "image/png"),
        ("letter.docx", _docx("Invoice number DOC-7 total 120.00 USD from Word"), None),
    )
    intake = done(pipeline, cid, "intake")
    assert intake.outcome == "ok"
    docs = intake.output["documents"]
    assert [d["pages"] for d in docs] == [1, 1]
    assert [d["has_text_layer"] for d in docs] == [False, True]
    assert sum("Converted" in r for r in intake.reasoning) == 2
    with pipeline.sf() as s:
        rows = s.scalars(select(Document).where(Document.case_id == cid).order_by(Document.id)).all()
    assert all(r.mime == "application/pdf" and r.storage_key.endswith(".pdf") for r in rows)
    assert pipeline.storage.get(rows[1].storage_key).startswith(b"%PDF")


async def test_intake_rejects_empty_and_too_many_pages(pipeline: Pipeline):
    await pipeline.start()
    empty = await pipeline.run(("blank.pdf", b"", "application/pdf"))
    assert done(pipeline, empty, "intake").outcome == "empty"
    assert case(pipeline, empty).status == "failed"

    long = await pipeline.run(("long.pdf", _pdf(9), "application/pdf"))
    ev = done(pipeline, long, "intake")
    assert ev.outcome == "too_large" and "limit is 8" in ev.reasoning[0]

    junk = await pipeline.run(("notes.txt", b"just text", "text/plain"))
    assert done(pipeline, junk, "intake").outcome == "empty"


# ---------------------------------------------------------------- normaliser units
def test_parse_date_variants():
    hint = ["MDY", "DMY", "ISO"]
    assert parse_date("Oct 02, 2026", DATE_FORMATS, NUMERIC, hint) == ("2026-10-02", False)
    assert parse_date("October 1, 2026", DATE_FORMATS, NUMERIC, hint) == ("2026-10-01", False)
    assert parse_date("08 Oct 2026", DATE_FORMATS, NUMERIC, hint) == ("2026-10-08", False)
    assert parse_date("2026-10-05", DATE_FORMATS, NUMERIC, hint) == ("2026-10-05", False)
    assert parse_date("10/02/2026", DATE_FORMATS, NUMERIC, hint) == (
        "2026-10-02",
        True,
    )  # ambiguous: MDY first
    assert parse_date("10/02/2026", DATE_FORMATS, NUMERIC, ["DMY", "MDY"]) == ("2026-02-10", True)
    assert parse_date("25/12/2026", DATE_FORMATS, NUMERIC, hint) == ("2026-12-25", False)  # only DMY fits
    assert parse_date("not a date", DATE_FORMATS, NUMERIC, hint) == (None, False)


def test_invoice_number_normalisation():
    assert normalise_invoice_number("INV A2207", "upper_alnum") == "INVA2207"
    assert normalise_invoice_number("inv-a-2207", "upper_alnum") == "INVA2207"
    assert normalise_invoice_number(None, "upper_alnum") is None


async def test_normaliser_tax_split_uom_currency(pipeline: Pipeline):
    """Drive the normaliser alone with a hand-made extraction state."""
    from app.agents.base import AgentContext
    from app.agents.normaliser import NormaliserAgent

    await pipeline.start()
    with pipeline.sf() as s:
        c = Case(ref="T-1", status="processing")
        s.add(c)
        s.commit()
        cid = c.id
    raw = {
        "invoice_number": "eu-77",
        "invoice_date": "05 Oct 2026",
        "due_date": None,
        "currency": "€",
        "tax_inclusive": True,
        "lines": [
            {
                "description": "Consulting",
                "qty": "10",
                "unit_price": "121.00",
                "amount": "1210.00",
                "tax_rate": "21",
                "uom": "hrs",
            },
            {
                "description": "Travel",
                "qty": "1",
                "unit_price": "50",
                "amount": None,
                "tax_rate": None,
                "uom": "ea",
            },
        ],
        "subtotal": None,
        "tax_total": None,
        "shipping": None,
        "total": "1260.00",
    }
    agent = NormaliserAgent(pipeline.cfg.agents["normaliser"])
    ctx = AgentContext(
        case_id=cid,
        case_ref="T-1",
        stage_id="normalise",
        run_no=1,
        config=pipeline.cfg,
        agent_config=pipeline.cfg.agents["normaliser"],
        state={"extraction": {"invoice": raw}},
        session_factory=pipeline.sf,
        storage=pipeline.storage,
    )
    res = await agent.run("normalise", ctx)
    inv = res.data["invoice"]
    assert inv["currency"] == "EUR" and inv["invoice_number_normalised"] == "EU77"
    assert inv["invoice_date"] == "2026-10-05"
    first, second = inv["lines"]
    assert (first["amount"], first["tax"], first["amount_incl_tax"], first["uom"]) == (
        "1000.00",
        "210.00",
        "1210.00",
        "hour",
    )
    assert (second["amount"], second["uom"]) == ("50.00", "each")
    assert (inv["subtotal"], inv["tax_total"], inv["total"]) == ("1050.00", "210.00", "1260.00")
    assert inv["arithmetic_ok"] is True
    assert any("embedded tax split" in ch for ch in res.data["changes"])
    assert str(case(pipeline, cid).total) == "1260.00"


async def test_bundle_split_is_idempotent_on_rerun(pipeline: Pipeline):
    """A restart between the split commit and the stage event must not create a second set of children."""
    from app.agents.base import AgentContext
    from app.agents.classifier import ClassifierAgent

    await pipeline.start()
    cid = await pipeline.run_fixture("S10_bundle_two_invoices.pdf")
    agent_cfg = pipeline.cfg.agents["classifier"]
    ctx = AgentContext(
        case_id=cid,
        case_ref=None,
        stage_id="classify",
        run_no=2,
        config=pipeline.cfg,
        agent_config=agent_cfg,
        state={},
        session_factory=pipeline.sf,
        llm=pipeline.llm,
        storage=pipeline.storage,
    )
    res = await ClassifierAgent(agent_cfg).run("classify", ctx)
    assert res.outcome == "split" and res.spawn == [] and len(res.data["child_cases"]) == 2
    with pipeline.sf() as s:
        assert len(s.scalars(select(Case).where(Case.parent_case_id == cid)).all()) == 2


def test_locate_matches_whole_values_only():
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((50, 100), "Freight 1,100.00")
    page.insert_text((50, 200), "Sales Tax (8.25%)")
    page.insert_text((50, 300), "Subtotal $100.00")
    page.insert_text((50, 400), "Tax 8.25")
    data = doc.tobytes()
    hit = pdf.locate(data, ["100.00"], [1])
    assert hit and 0.36 < hit[1][1] < 0.38  # the "$100.00" line at y=300, not inside "1,100.00"
    hit = pdf.locate(data, ["8.25"], [1])
    assert hit and 0.48 < hit[1][1] < 0.51  # "Tax 8.25", not the "(8.25%)" rate
    assert pdf.locate(data, ["Sales Tax"], [1]) is not None  # multi-word still uses search_for
