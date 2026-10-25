"""Deploy smoke test: /healthz, the auth wall, then run one scenario end-to-end and compare it with its
`expect`.

    uv run python -m scripts.smoke_deploy https://ledgerflow-api.onrender.com [--scenario S01_happy]
        [--token <a Supabase access token>]   (or SMOKE_TOKEN)

Waits out a Render cold start (the free plan sleeps after 15 min). Exits 1 if the health check is not
db=ok, storage=ok with a probed llm_path, if an API route answers without a token, or if the case finishes
with a different decision than expected. Without a token it stops after the auth-wall check.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import httpx

WAITING = {"received", "processing"}  # anything else means the workflow has stopped or is paused for a person
LLM_PATHS = {"responses", "chat"}


def wait_healthy(client: httpx.Client, timeout_s: float, *, allow_fake: bool) -> dict:
    deadline = time.monotonic() + timeout_s
    health: dict = {}
    while time.monotonic() < deadline:
        try:
            health = client.get("/healthz").json()
            paths = LLM_PATHS | ({"fake"} if allow_fake else set())
            if health.get("ok") and health.get("llm_path") in paths:
                return health
        except (httpx.HTTPError, ValueError) as e:  # cold start: connection errors or a 502 page
            health = {"error": type(e).__name__}
        time.sleep(5)
    raise SystemExit(f"FAIL /healthz never became healthy within {timeout_s:.0f}s: {health}")


def run_scenario(client: httpx.Client, scenario_id: str, timeout_s: float) -> tuple[dict, dict]:
    fixtures = {f["id"]: f for f in client.get("/api/fixtures").raise_for_status().json()}
    if scenario_id not in fixtures:
        raise SystemExit(f"FAIL unknown scenario {scenario_id}")
    case = client.post(f"/api/fixtures/{scenario_id}/run").raise_for_status().json()
    print(f"started case {case['id']} ({case.get('ref')}) for {scenario_id}")
    deadline = time.monotonic() + timeout_s
    while case["status"] in WAITING and time.monotonic() < deadline:
        time.sleep(3)
        case = client.get(f"/api/cases/{case['id']}").raise_for_status().json()
        print(f"  status={case['status']} stage={case.get('current_stage')}")
    return fixtures[scenario_id]["expect"], case


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base_url", help="API origin, e.g. https://ledgerflow-api.onrender.com")
    ap.add_argument("--scenario", default="S01_happy")
    ap.add_argument("--health-timeout", type=float, default=180, help="seconds to wait out a cold start")
    ap.add_argument("--run-timeout", type=float, default=300)
    ap.add_argument("--allow-fake-llm", action="store_true", help="accept llm_path=fake (local checks only)")
    ap.add_argument("--token", default=os.environ.get("SMOKE_TOKEN"), help="bearer token of a signed-in user")
    args = ap.parse_args()

    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=60) as client:
        health = wait_healthy(client, args.health_timeout, allow_fake=args.allow_fake_llm)
        print(f"OK /healthz db={health['db']} storage={health['storage']} llm_path={health['llm_path']} "
              f"auth={health.get('auth_mode')}")
        wall = client.get("/api/fixtures")
        if wall.status_code != 401:
            sys.exit(f"FAIL /api/fixtures answered {wall.status_code} without a token (expected 401)")
        print("OK auth wall: /api/fixtures needs a token")
        if not args.token:
            print("no --token / SMOKE_TOKEN: skipping the scenario run")
            return
        client.headers["Authorization"] = f"Bearer {args.token}"
        expect, case = run_scenario(client, args.scenario, args.run_timeout)

    want = expect.get("decision")
    if case["status"] in WAITING:
        sys.exit(f"FAIL {args.scenario} still {case['status']} after {args.run_timeout:.0f}s")
    if want and case.get("decision") != want:
        sys.exit(f"FAIL {args.scenario}: decision={case.get('decision')} status={case['status']}, expected {want}")
    print(f"OK {args.scenario}: decision={case.get('decision')} status={case['status']} "
          f"summary={case.get('decision_summary')!r}")


if __name__ == "__main__":
    main()
