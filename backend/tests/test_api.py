from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from app.agents.base import StubScript
from app.main import create_app
from tests.conftest import auth_client


def wait_status(client: TestClient, case_id: int, statuses: set[str], timeout: float = 15) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/cases/{case_id}").json()
        if body["status"] in statuses:
            return body
        time.sleep(0.05)
    raise AssertionError(f"case {case_id} stuck in {body['status']}")


def parse_sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        event, data = None, None
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if event:
            out.append((event, data))
    return out


def test_upload_run_stream_and_review(test_settings):
    app = create_app(test_settings, script=StubScript({"decide": ["needs_review"]}))
    with auth_client(app) as client:
        health = client.get("/healthz").json()
        assert health["ok"] and health["db"] == "ok" and health["storage"] == "ok"
        assert health["llm_path"] == "unconfigured"

        cfg = client.get("/api/config").json()
        assert cfg["workflow"]["start"] == "intake" and len(cfg["hash"]) == 64

        r = client.post(
            "/api/cases",
            files=[("files", ("inv.pdf", b"%PDF-1.4 hello", "application/pdf"))],
            data={"submitter_name": "Sam", "submitter_email": "sam@example.com", "note": "urgent"},
        )
        assert r.status_code == 201, r.text
        case = r.json()
        assert case["ref"].startswith("INV-2026-") and case["status"] in {"received", "processing"}

        detail = wait_status(client, case["id"], {"needs_review"})
        assert detail["status_label"] == "In review"
        assert detail["documents"][0]["filename"] == "inv.pdf"
        assert detail["events"][-1]["status"] == "waiting"

        listing = client.get("/api/cases", params={"status": "needs_review"}).json()
        assert listing["total"] == 1 and listing["items"][0]["id"] == case["id"]

        sse = client.get(f"/api/cases/{case['id']}/events/stream", params={"follow": "false"})
        assert sse.headers["content-type"].startswith("text/event-stream")
        evts = parse_sse(sse.text)
        stage_events = [d for e, d in evts if e == "stage_event"]
        assert [d["seq"] for d in stage_events] == list(range(1, len(stage_events) + 1))
        assert stage_events[0]["stage_id"] == "intake" and stage_events[-1]["stage_id"] == "review"
        assert evts[-1][0] == "case_updated" and evts[-1][1]["status"] == "needs_review"

        # Last-Event-ID resumes after the given seq
        resumed = parse_sse(
            client.get(
                f"/api/cases/{case['id']}/events/stream",
                params={"follow": "false"},
                headers={"Last-Event-ID": str(len(stage_events) - 2)},
            ).text
        )
        assert [d["seq"] for e, d in resumed if e == "stage_event"] == [
            len(stage_events) - 1,
            len(stage_events),
        ]

        assert client.post(f"/api/cases/{case['id']}/review", json={"action": "remark"}).status_code == 422
        r = client.post(f"/api/cases/{case['id']}/review", json={"action": "approve", "actor": "Rita"})
        assert r.status_code == 200, r.text
        assert wait_status(client, case["id"], {"approved"})["current_stage"] == "end:approved"
        assert client.post(f"/api/cases/{case['id']}/review", json={"action": "approve"}).status_code == 409
        assert client.get("/api/cases/999").status_code == 404


def test_fake_llm_backend_runs_real_perception_agents(test_settings):
    settings = test_settings.model_copy(update={"force_stub_agents": False, "llm_backend": "fake"})
    with auth_client(create_app(settings)) as client:  # first sign-in seeds the demo master data
        assert client.get("/healthz").json()["llm_path"] == "fake"
        case = client.post("/api/fixtures/S01_happy/run").json()
        body = wait_status(client, case["id"], {"approved", "failed"})
        assert body["status"] == "approved"
        events = parse_sse(client.get(f"/api/cases/{case['id']}/events/stream?follow=false").text)
        extract = [d for e, d in events if e == "stage_event" and d["stage_id"] == "extract" and d["status"] == "completed"]
        assert extract and extract[0]["output"]["invoice"]["invoice_number"] == "INV-A-2207"
        assert extract[0]["output"]["field_meta"]["total"]["source"] == "text_layer"
