---
name: live-check
description: Run every demo scenario against a running LedgerFlow instance (local or deployed) and compare results to data/scenarios.yaml expectations. Use before the demo or after any change to agents, rules or prompts.
disable-model-invocation: true
---
Target base URL: $ARGUMENTS (default http://localhost:8000).

1. `curl -s <base>/healthz` must report db, storage and llm_path OK.
2. Reset and seed the target if it is local (`make seed`). For a deployed target, ask before reseeding.
3. For each scenario id in `run_order` of data/scenarios.yaml: `POST /api/fixtures/<id>/run`, poll `GET /api/cases/<case>` until it is not processing (timeout 180 s), and record the decision, failed rules, PO remaining, duration and LLM cost.
4. For S02b, also post the `demo_remark` as a review remark, wait, then approve; for S05, upload the supporting files through the portal endpoints, approve the vendor, and wait for the resume.
5. Print a table: scenario | expected | actual | match | seconds | cost. For any mismatch, open the case's stage_events and explain which stage diverged and why.
