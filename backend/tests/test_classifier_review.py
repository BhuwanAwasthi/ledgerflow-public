"""Classifier edge cases: a probable invoice below min_confidence goes to review (not unsupported), and pages
no classified range covers get a reasoning line."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.models import Document, RuleResult
from tests.conftest import Pipeline
from tests.test_perception import case, done


def set_classifier_output(p: Pipeline, scenario: str, documents: list[dict[str, Any]]) -> None:
    fx = next(f for f in p.llm.fixtures if f["agent"] == "classifier" and f["scenario"] == scenario)
    fx["calls"][0]["output"]["documents"] = documents


def entry(label: str, confidence: float, page_from: int = 1, page_to: int = 1) -> dict[str, Any]:
    return {"label": label, "confidence": confidence, "page_from": page_from, "page_to": page_to, "reason": "test"}


async def test_low_confidence_invoice_goes_to_review_not_unsupported(pipeline: Pipeline):
    await pipeline.start()
    set_classifier_output(pipeline, "S01_happy", [entry("invoice", 0.55)])
    cid = await pipeline.run_fixture("S01_acme_INV-A-2207.pdf")

    c = case(pipeline, cid)
    assert (c.status, c.decision) == ("needs_review", "needs_review")
    classify = done(pipeline, cid, "classify")
    assert classify.outcome == "low_confidence"
    assert classify.output["uncertain_invoice"]["confidence"] == 0.55
    assert any("flagging it for review" in r for r in classify.reasoning)
    assert done(pipeline, cid, "extract").outcome == "ok"  # the probable invoice was read

    with pipeline.sf() as s:
        doc = s.scalars(select(Document).where(Document.case_id == cid)).one()
        assert (doc.label, doc.label_confidence) == ("invoice", 0.55)
        rule = s.scalars(
            select(RuleResult).where(RuleResult.case_id == cid, RuleResult.rule_id == "document_classification")
        ).one()
    assert (rule.status, rule.severity) == ("fail", "review")
    assert "55%" in rule.explanation and "70%" in rule.explanation


async def test_confident_invoice_passes_the_classification_rule(pipeline: Pipeline):
    await pipeline.start()
    cid = await pipeline.run_fixture("S01_acme_INV-A-2207.pdf")
    assert case(pipeline, cid).status == "approved"
    with pipeline.sf() as s:
        rule = s.scalars(
            select(RuleResult).where(RuleResult.case_id == cid, RuleResult.rule_id == "document_classification")
        ).one()
    assert rule.status == "pass"


async def test_low_confidence_non_invoice_is_still_unsupported(pipeline: Pipeline):
    await pipeline.start()
    set_classifier_output(pipeline, "S01_happy", [entry("statement", 0.5)])
    cid = await pipeline.run_fixture("S01_acme_INV-A-2207.pdf")
    assert case(pipeline, cid).status == "unsupported"
    assert done(pipeline, cid, "classify").outcome == "no_invoice"


async def test_uncovered_pages_get_a_reasoning_line(pipeline: Pipeline):
    await pipeline.start()
    set_classifier_output(pipeline, "S10_bundle", [entry("invoice", 0.97, 1, 1)])  # page 2 not returned
    cid = await pipeline.run_fixture("S10_bundle_two_invoices.pdf")
    classify = done(pipeline, cid, "classify")
    assert classify.outcome == "has_invoice"
    assert any("page(s) 2 matched no document type" in r for r in classify.reasoning), classify.reasoning
