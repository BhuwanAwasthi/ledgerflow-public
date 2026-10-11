"""Live: classification + extraction on real fixtures against Azure (`make test-live -k extraction`).

Expected header values are the recorded FakeLLM outputs (themselves checked against scenarios.yaml by
test_fixtures.py). Prints a field table and per-call latency/tokens; header accuracy must be >= 95%.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.config_loader import resolved_config
from app.llm import make_llm
from app.models import StageEvent
from app.settings import get_settings
from app.storage import LocalStorage
from tests.conftest import CONFIG_DIR, LLM_FIXTURES, Pipeline

pytestmark = pytest.mark.live

SCENARIOS = {
    "S01_happy": "S01_acme_INV-A-2207.pdf",
    "S04_duplicate_scan": "S04_acme_scan_duplicate.pdf",
    "S08_price_variance": "S08_coastal_CL-778.pdf",
    "S09_missing_fields": "S09_coastal_scan_missing.pdf",
}
HEADER = [
    "vendor_name",
    "invoice_number",
    "invoice_date",
    "due_date",
    "po_reference",
    "currency",
    "subtotal",
    "tax_total",
    "total",
]
DATES = {"invoice_date", "due_date"}
AMOUNTS = {"subtotal", "tax_total", "total"}


def _same(field: str, want: Any, got: Any) -> bool:
    if want is None or got is None:
        return want is None and got is None
    if field in AMOUNTS:
        return Decimal(str(want)) == Decimal(str(got))
    return " ".join(str(want).split()).casefold() == " ".join(str(got).split()).casefold()


async def test_live_extraction_accuracy(session_factory, tmp_path: Path):
    settings = get_settings()
    if not (
        settings.azure_openai_endpoint and settings.azure_openai_api_key and settings.azure_openai_deployment
    ):
        pytest.skip("AZURE_OPENAI_* not configured")
    cfg = resolved_config(CONFIG_DIR)
    llm = make_llm(settings.model_copy(update={"llm_backend": "azure"}), cfg, session_factory)
    p = Pipeline(session_factory, LocalStorage(tmp_path / "storage"), llm)
    await p.start()
    try:
        case_ids = {}
        for sid, filename in SCENARIOS.items():
            case_ids[sid] = await p.run_fixture(filename)
    finally:
        assert p.orch is not None
        await p.orch.stop()

    rows: list[tuple[str, str, Any, Any, bool]] = []
    calls: list[tuple[str, str, dict[str, Any]]] = []
    with session_factory() as s:
        for sid, cid in case_ids.items():
            evs = s.scalars(
                select(StageEvent).where(StageEvent.case_id == cid).order_by(StageEvent.seq)
            ).all()
            by_stage = {e.stage_id: e for e in evs if e.status == "completed"}
            assert "extract" in by_stage, f"{sid}: {[(e.stage_id, e.status, e.output) for e in evs]}"
            raw = by_stage["extract"].output["invoice"]
            norm = by_stage["normalise"].output["invoice"]
            expected = json.loads((LLM_FIXTURES / f"{sid}__extraction.json").read_text(encoding="utf-8"))
            want = expected["calls"][0]["output"]
            for f in HEADER:
                got = norm[f] if f in DATES or f in AMOUNTS or f == "currency" else raw[f]
                rows.append((sid, f, want[f], got, _same(f, want[f], got)))
            for stage in ("classify", "extract"):
                for c in by_stage[stage].metrics.get("llm_calls", []):
                    calls.append((sid, stage, c))

    print(
        "\n\nscenario            field           expected                    got                         ok"
    )
    for sid, f, want, got, ok in rows:
        print(f"{sid:<19} {f:<15} {want!s:<27} {got!s:<27} {'OK' if ok else 'MISS'}")
    print("\nscenario            stage     latency_ms  tokens_in  tokens_out  cost_usd  path")
    for sid, stage, c in calls:
        print(
            f"{sid:<19} {stage:<9} {c['latency_ms']:>10}  {c['tokens_in']:>9}  {c['tokens_out']:>10}"
            f"  {c['cost_usd']:>8}  {c['path']}"
        )
    accuracy = sum(r[4] for r in rows) / len(rows)
    print(f"\nheader-field accuracy: {accuracy:.1%} ({sum(r[4] for r in rows)}/{len(rows)})")
    assert accuracy >= 0.95
