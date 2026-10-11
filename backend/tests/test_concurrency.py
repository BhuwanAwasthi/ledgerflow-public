"""SSE keeps streaming while the pipeline is stuck in a slow database call.

The orchestrator (and every agent) runs on its own event loop thread and API routes do their DB work in the
threadpool, so a slow query in the pipeline never blocks the loop that serves SSE and other requests.
Runs a real uvicorn server: httpx's ASGI transport buffers whole responses, so it can't observe streaming.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import uvicorn
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.agents.base import StubScript
from app.main import create_app
from app.settings import Settings

SLOW_S = 0.8  # every stage_event insert (only the pipeline writes them) takes this long


def _slow_pipeline_writes(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
    if statement.lstrip().upper().startswith("INSERT INTO STAGE_EVENTS"):
        time.sleep(SLOW_S)  # a blocking driver call, like a slow Postgres round trip


@pytest.fixture
def server(test_settings: Settings) -> Iterator[str]:
    # classify -> no_invoice ends the run quickly: intake + classify = 4 slow inserts
    app = create_app(test_settings, script=StubScript({"classify": ["no_invoice"]}))
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not srv.started:
        assert time.monotonic() < deadline and thread.is_alive(), "uvicorn did not start"
        time.sleep(0.02)
    port = srv.servers[0].sockets[0].getsockname()[1]
    event.listen(Engine, "before_cursor_execute", _slow_pipeline_writes)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        event.remove(Engine, "before_cursor_execute", _slow_pipeline_writes)
        srv.should_exit = True
        thread.join(20)


def test_sse_keeps_streaming_during_a_slow_db_call(server: str):
    t0 = time.perf_counter()
    with httpx.Client(base_url=server, timeout=30) as client:
        r = client.post("/api/cases", files={"files": ("inv.pdf", b"%PDF-1.4 test", "application/pdf")})
        assert r.status_code == 201, r.text
        case_id = r.json()["id"]

        health: list[float] = []
        stop = threading.Event()

        def poll_health() -> None:  # another request served by the same API loop meanwhile
            with httpx.Client(base_url=server, timeout=30) as c:
                while not stop.is_set():
                    t0 = time.perf_counter()
                    assert c.get("/healthz").status_code == 200
                    health.append(time.perf_counter() - t0)
                    time.sleep(0.05)

        poller = threading.Thread(target=poll_health, daemon=True)
        poller.start()
        arrivals: list[tuple[float, str, dict[str, Any]]] = []
        try:
            with client.stream("GET", f"/api/cases/{case_id}/events/stream") as resp:
                kind = ""
                for line in resp.iter_lines():
                    if line.startswith("event:"):
                        kind = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        data = json.loads(line.split(":", 1)[1])
                        arrivals.append((time.perf_counter() - t0, kind, data))
                        if kind == "case_updated" and data["status"] == "unsupported":
                            break
                        assert time.perf_counter() - t0 < 20, "run did not finish"
        finally:
            stop.set()
            poller.join(10)

    # the API loop stayed responsive the whole time
    assert len(health) >= 10
    assert max(health) < SLOW_S / 2, f"healthz stalled for {max(health):.2f}s during a slow pipeline write"
    stage_events = [(t, d) for t, k, d in arrivals if k == "stage_event"]
    assert [(d["stage_id"], d["status"]) for _, d in stage_events] == [
        ("intake", "started"), ("intake", "completed"), ("classify", "started"), ("classify", "completed"),
    ]
    # the pipeline really sat in slow DB calls, and the stream delivered events as they happened, not at the end
    assert arrivals[-1][0] >= 3 * SLOW_S
    assert stage_events[-1][0] - stage_events[0][0] >= 2 * SLOW_S
