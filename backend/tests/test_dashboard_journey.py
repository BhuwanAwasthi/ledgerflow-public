"""F1 backend support: dashboard metrics, the submitter journey, rerun (Retry) and SSE reconnect by `after`."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app import baseline
from app.agents.base import StubScript
from app.config_loader import ConfigError, build_config
from app.dashboard import RangeError, metrics
from app.journey import build_journey
from app.main import create_app
from app.models import Case, StageEvent
from tests.conftest import Pipeline, auth_client, patch_yaml
from tests.test_api import parse_sse, wait_status
from tests.test_loops import _today, run  # noqa: F401  (_today pins the policy date for these runs too)


def _journey(p: Pipeline, cid: int) -> dict:
    with p.sf() as s:
        case = s.get(Case, cid)
        assert case is not None
        evs = list(s.query(StageEvent).filter(StageEvent.case_id == cid).order_by(StageEvent.seq))
        return build_journey(p.cfg, case, evs)


async def test_journey_for_approved_and_on_hold_cases(pipeline: Pipeline):
    await pipeline.start()
    s01 = await run(pipeline, "S01_happy")
    j = _journey(pipeline, s01)
    states = {st["label"]: st["state"] for st in j["steps"]}
    assert j["status"] == "approved" and j["headline"] == "Approved, ready for payment run"
    assert states["Received"] == states["Decided"] == states["Approved"] == "done"
    assert states["In review"] == "skipped"  # auto-approved: went past review without entering it
    assert states["Exported"] == "pending"
    assert all(st["at"] is not None for st in j["steps"] if st["state"] == "done")

    s06 = await run(pipeline, "S06_bank_change")
    j = _journey(pipeline, s06)
    states = {st["label"]: st["state"] for st in j["steps"]}
    assert j["status"] == "on_hold" and states["In review"] == "waiting"
    assert j["headline"].startswith("Waiting for a signed letter on bank letterhead")
    assert j["portal_url"] and "/portal/" in j["portal_url"]
    assert states["Approved"] == "pending"


async def test_dashboard_metrics(pipeline: Pipeline):
    await pipeline.start()
    s01 = await run(pipeline, "S01_happy")
    s06 = await run(pipeline, "S06_bank_change")
    s04 = await run(pipeline, "S04_duplicate_scan")
    with pipeline.sf() as s:
        totals = {c.id: c.total for c in s.query(Case)}
        m = metrics(s, pipeline.cfg, "7d")
        with pytest.raises(RangeError):
            metrics(s, pipeline.cfg, "5d")
    k = m["kpis"]
    assert (m["range"], len(m["series"])) == ("7d", 7)
    assert m["series"][-1]["cases"] == 3 and m["series"][-1]["auto_approve"] == 1
    assert k["cases"] == 3 and k["decided"] == 3
    assert k["straight_through_pct"] == pytest.approx(33.3)
    assert (k["on_hold"], k["in_review"]) == (1, 0)
    assert k["exceptions_caught_cases"] == 2
    assert k["exceptions_caught_amount"] == totals[s06] + totals[s04]
    assert k["median_time_to_decision_s"] is not None and k["median_time_to_decision_s"] >= 0
    rules = {r["rule_id"]: r for r in m["exceptions_by_rule"]}
    assert rules["bank_details_match"]["cases"] == 1 and rules["bank_details_match"]["amount"] == totals[s06]
    assert "duplicate_exact" in rules
    mix = {d["decision"]: d["cases"] for d in m["decision_mix"]}
    assert mix == {"auto_approve": 1, "needs_review": 0, "on_hold": 1, "reject": 1}
    assert totals[s01] == Decimal("4779.24")


def test_ui_config_is_validated(config_files: dict[str, str]):
    def bad(d: dict) -> None:
        d["ui"]["journey"][0]["stages"] = ["nope"]
        d["ui"]["stage_chips"]["ghost"] = ["x"]
        d["ui"]["dashboard"]["default_range"] = "1d"

    with pytest.raises(ConfigError) as e:
        build_config(patch_yaml(config_files, "app.yaml", bad))
    msg = str(e.value)
    assert "default_range '1d'" in msg

    def bad_refs(d: dict) -> None:
        d["ui"]["journey"][0]["stages"] = ["nope"]
        d["ui"]["stage_chips"]["ghost"] = ["x"]
        d["ui"]["status_text"]["limbo"] = "?"

    with pytest.raises(ConfigError) as e:
        build_config(patch_yaml(config_files, "app.yaml", bad_refs))
    msg = str(e.value)
    assert "unknown stage 'nope'" in msg and "unknown agent 'ghost'" in msg and "unknown case status 'limbo'" in msg


def test_rerun_retries_a_failed_stage_and_sse_after(test_settings):
    app = create_app(test_settings, script=StubScript({"policy": ["!error", "evaluated"], "decide": ["needs_review"]}))
    with auth_client(app) as client:
        case = client.post("/api/cases", files=[("files", ("inv.pdf", b"%PDF-1.4 x", "application/pdf"))]).json()
        failed = wait_status(client, case["id"], {"failed"})
        assert failed["current_stage"] == "policy" and failed["events"][-1]["status"] == "failed"
        last_seq = failed["events"][-1]["seq"]

        assert client.post(f"/api/cases/{case['id']}/rerun", json={"from_stage": "nope"}).status_code == 409
        r = client.post(f"/api/cases/{case['id']}/rerun", json={})
        assert r.status_code == 200, r.text
        done = wait_status(client, case["id"], {"needs_review"})
        retried = [e for e in done["events"] if e["stage_id"] == "policy" and e["status"] == "completed"]
        assert retried and retried[0]["run_no"] > failed["events"][-1]["run_no"]

        # a reconnecting client passes the last seq it has and gets only newer events
        evts = parse_sse(client.get(f"/api/cases/{case['id']}/events/stream?follow=false&after={last_seq}").text)
        seqs = [d["seq"] for e, d in evts if e == "stage_event"]
        assert seqs and min(seqs) == last_seq + 1

        assert client.post(f"/api/cases/{case['id']}/rerun", json={}).status_code == 200  # paused cases rerun too
        wait_status(client, case["id"], {"needs_review"})

        j = client.get(f"/api/cases/{case['id']}/journey").json()
        assert j["status"] == "needs_review" and j["headline"] == "With the AP team for review"

        dash = client.get("/api/dashboard/metrics", params={"range": "7d"})
        assert dash.status_code == 200, dash.text
        assert dash.json()["kpis"]["in_review"] == 1 and dash.json()["recent"][0]["id"] == case["id"]
        assert client.get("/api/dashboard/metrics", params={"range": "2d"}).status_code == 422
        assert client.get("/api/cases", params={"submitter_email": "nobody@x.com"}).json()["total"] == 0


def test_status_text_placeholders_and_exception_decisions_are_validated(config_files: dict[str, str]):
    def bad(d: dict) -> None:
        d["ui"]["status_text"]["on_hold"] = "Waiting for {label.__class__} {vendor}"
        d["ui"]["dashboard"]["exception_decisions"] = ["needs_review", "rejekt"]

    with pytest.raises(ConfigError) as e:
        build_config(patch_yaml(config_files, "app.yaml", bad))
    msg = str(e.value)
    assert "uses '{label.__class__}'" in msg and "uses '{vendor}'" in msg
    assert "exception_decisions 'rejekt'" in msg


def test_rerun_refused_for_final_cases_and_edge_filters(test_settings, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(baseline, "seed_history", lambda *a, **k: 0)  # a workspace with no demo history
    app = create_app(test_settings, script=StubScript({"intake": ["!error"]}))
    with auth_client(app) as client:
        empty = client.get("/api/dashboard/metrics", params={"range": "7d"}).json()
        assert empty["kpis"]["cases"] == 0 and empty["kpis"]["straight_through_pct"] is None
        assert empty["kpis"]["median_time_to_decision_s"] is None and all(p["cases"] == 0 for p in empty["series"])

        failed = client.post(
            "/api/cases",
            files=[("files", ("a.pdf", b"%PDF-1.4 a", "application/pdf"))],
            data={"submitter_email": "Sam@Example.com"},
        ).json()
        wait_status(client, failed["id"], {"failed"})
        j = client.get(f"/api/cases/{failed['id']}/journey").json()
        states = {st["label"]: st["state"] for st in j["steps"]}
        assert states["Received"] == "failed" and states["Classified"] == "pending"

        listing = client.get("/api/cases", params={"submitter_email": "sam@example.COM"}).json()
        assert listing["total"] == 1 and listing["items"][0]["id"] == failed["id"]

    # an approved case is final: rerun is refused
    app = create_app(test_settings.model_copy(update={"database_url": test_settings.database_url.replace("api.db", "api2.db")}))
    with auth_client(app) as client:
        ok = client.post("/api/cases", files=[("files", ("b.pdf", b"%PDF-1.4 b", "application/pdf"))]).json()
        wait_status(client, ok["id"], {"approved"})
        r = client.post(f"/api/cases/{ok['id']}/rerun", json={})
        assert r.status_code == 409 and "cannot be rerun" in r.text
