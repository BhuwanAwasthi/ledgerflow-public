# Build phases (one day)

Two Claude Code sessions run in parallel after Phase 1:
- **Session A (backend):** phases B1 → B5, in the main checkout.
- **Session B (frontend + data):** phases D1, then F1 → F3, in a git worktree (`git worktree add ../ledgerflow-fe -b fe`), merged back after each phase.

Each phase has a goal, the work, and **Done when**: checks Claude must run and show before committing. Start each phase with `/clear`, then `/run-phase <id>`.

| Phase | Session | Target time (IST) | Depends on |
| --- | --- | --- | --- |
| P0 setup (you) | – | 12:30–13:00 | – |
| B1 skeleton + config + orchestrator | A | 13:00–14:15 | P0 |
| D1 fixtures + seed data | B | 13:00–14:00 | P0 |
| B2 LLM adapter + perception agents | A | 14:15–15:45 | B1, D1 |
| F1 app shell, design system, dashboard, live run | B | 14:15–16:15 | B1 (OpenAPI) |
| B3 vendor, PO matching, policy, decision + edge-case tests | A | 15:45–17:30 | B2 |
| F2 review workspace | B | 16:15–18:15 | F1 |
| B4 template, exports, HITL, reconciler, comms, portal | A | 17:30–19:00 | B3 |
| F3 approved library, vendors, POs, Agent Studio, portal, launcher | B | 18:15–20:15 | F2, B4 |
| B5 deploy (Render + Supabase + Vercel) | A | 20:15–21:15 | all |
| P6 live scenario pass + polish + stretch | A+B | 21:15–23:30 | B5 |
| P7 record the 5-min video | you | 23:30–00:00 | P6 |

Cut line: if you are behind at 19:00, drop F3's Agent Studio editing (keep read-only), the what-if simulator and replay. Never cut: the live run view, dashboard, review workspace, approved library with bulk export, and scenarios S01, S02b, S03, S05.

---

## P0 setup (you, not Claude)
1. Create accounts (free): GitHub, Supabase, Render, Vercel, cron-job.org.
2. Supabase: new project → Storage → create a private bucket `documents` → Project Settings → Database → copy the **Session pooler** connection string (IPv4-friendly) → API → copy the project URL and service_role key.
3. `mkdir ledgerflow && cd ledgerflow && git init`, unzip this kit into it, `cp .env.example .env`, fill in the Azure values (endpoint, key, deployment name). Leave the Supabase values empty for local dev.
4. Open the folder in VS Code → Claude Code extension → run `/init`? **No**: CLAUDE.md is already written. Instead run: `check that you can see CLAUDE.md, docs/SPEC.md and config/; summarise the plan in 5 lines`.
5. Run the Azure smoke test yourself: `uv run --with openai --with python-dotenv python backend/scripts/smoke_azure.py`. All three checks must print OK before B2.

---

## B1: Backend skeleton, config loader, orchestrator (stub agents)
Goal: a case runs end-to-end through every stage in workflow.yaml with **stub agents** that return canned outcomes, with events streamed over SSE.
Work:
- `backend/` uv project; `app/settings.py`, `db.py`, `models.py` (all tables in SPEC §6, `create_all` at startup; no Alembic).
- `config_loader.py`: Pydantic models for every config file; validation (unmapped outcomes, unknown targets, unreachable stages, missing prompt files); DB overrides from `config_versions`; `resolved_config()` + `config_hash`.
- `agents/base.py`: Agent ABC, AgentContext, AgentResult, registry that imports handlers from the agent YAML. A `StubAgent` per agent id returning the first outcome (driven by the test fixture when provided).
- `orchestrator.py`: job runner (asyncio queue, concurrency from app.yaml), stage loop, retries and timeouts, `hitl` pause, `wait_for` pause + `fire_event(case_id, event)`, `sticky_review`, startup resume, an event bus feeding SSE.
- `api/`: `/healthz`, `POST /api/cases`, `GET /api/cases`, `GET /api/cases/{id}`, `GET /api/cases/{id}/events/stream`, `POST /api/cases/{id}/review`, `GET /api/config`.
- Makefile with the targets in CLAUDE.md; ruff config; pytest config with the `live` marker.
Done when:
- `make check` passes.
- `tests/test_workflow_config.py`: loads the real config; a broken copy (unmapped outcome) is rejected with a clear message.
- `tests/test_orchestrator.py`: stub run reaches `end:approved`; a needs_review path pauses at `review` then completes after `approve`; a `remark` loops through reconcile → policy → decide → review (sticky); a `wait_for` stage pauses and resumes on `fire_event`.
- `curl` an upload, then the SSE endpoint, and paste the event lines in the summary.

## D1: Synthetic data and fixtures
Goal: realistic PDFs and master data generated deterministically from `data/scenarios.yaml`.
Work:
- `backend/scripts/generate_fixtures.py` (reportlab): a distinct invoice layout per vendor (logo block, colours, table style, header positions) so extraction is non-trivial; computed subtotal/tax/total rounded half-up to cents; `render: scan` = rasterise at 120 dpi with PyMuPDF, rotate 0.8°, add Gaussian noise and slight blur, save as image-only PDF; S10 = a two-page bundle; S05 supporting W-9 and bank letter.
- Write `data/master/{vendors,purchase_orders,po_lines,grns,gl_accounts}.csv`.
- `backend/app/seed.py` + `make seed`; optional seeded history (flag `seed=true`).
- `GET /api/fixtures` and `POST /api/fixtures/{id}/run` (create a case from the fixture file, source=fixture).
- `backend/tests/llm_fixtures/<scenario>__<agent>.json`: the exact expected LLM outputs for every scenario, used by FakeLLM.
Done when: `make fixtures && make seed` succeed; render page 1 of S01, S04 and S10 to PNG and look at them (attach to the summary); totals in the PDFs equal the comments in scenarios.yaml (a test asserts this).

## B2: LLM adapter and perception agents
Goal: real classification and extraction on the fixtures.
Work:
- `llm.py` per SPEC §8 (Responses API first, chat fallback, strict schema post-processing, incomplete retry, metrics, cost, optional cache).
- Agents: intake, classifier (bundles → child cases), extraction (vision + text layer; exact bboxes via `page.search_for` for text PDFs; vendor-memory hints), normaliser.
- Replace the stubs for these four in the registry.
Done when: `make test` passes with FakeLLM; `make test-live -k extraction` runs S01, S04, S08 and S09 against Azure and field accuracy on header fields is ≥ 95% (print a small table: field, expected, got); per-call latency and tokens are printed.

## B3: Decision core
Goal: correct decisions for every scenario, deterministically.
Work: vendor (check + request_onboarding + portal token), po_matching (explicit, implied scoring with the LLM line-similarity call, line pairing, GRN qty, consumption ledger with reservations; suggest_gl), rules/engine.py with every rule in rules.yaml rendering `explain` templates, decision (precedence, gates, reservation, LLM explanation).
Done when: `tests/test_scenarios.py` runs every scenario in `run_order` with FakeLLM and asserts `expect` from scenarios.yaml (decision, failed_rules, PO remaining, implied PO, GL); unit tests for each rule's boundary (exactly at tolerance passes, 0.01 above fails); `make check` passes.

## B4: Output, HITL and the loops
Goal: the reviewer can approve, remark, reject or ask the vendor; approved cases export.
Work: template agent + Jinja2 sandbox filters (`money`, `fmt_date`) + profile validation; template versions with a cell-level diff; reconciler (LLM → ChangeSet → validated apply → override log → rerun); comms drafts (subject/body, mailto URL, portal link) + notifications; vendor portal endpoints + `vendor_docs_received` → classification of uploads → verification checks → admin approve → `fire_event(vendor_approved)`; exports (single file, ZIP, batch import file via the profile's `batch`, export batches, re-export guard); audit-pack PDF (reportlab); vendor memory written by `add_vendor_memory` ops and by reviewer field edits.
Done when: tests cover S02b's remark loop end-to-end (v1 → remark → v2 diff → approve → approved), S05's onboarding resume, S06 drafting a bank-letter request, a batch export of S01 + S02a producing one CSV with both bills' lines and the header once, and a re-export attempt returning 409 without `force=true`.

## F1: App shell, design system, dashboard, live run view
Goal: the two graded screens look excellent.
Work: Vite + TS + Tailwind + shadcn/ui; typed API client from `/openapi.json`; layout (sidebar, header with role switcher, command palette, notification bell, cold-start banner); `src/lib/status.ts`; Dashboard (SPEC §10.3); Live run view with SSE + reconnect + replay; Submit page; My submissions journey tracker.
Done when: `npm run typecheck && npm run build` pass; with `make dev` running, open the app with the Playwright MCP or a Playwright script, run S01 from the launcher, and capture screenshots of the dashboard and the live run mid-run and at completion, in light and dark mode. Review them against SPEC §10 and fix spacing, alignment and contrast issues before committing.

## F2: Review workspace
Goal: the HITL screen (SPEC §10.6).
Work: split view (react-pdf left with bbox highlights and document tabs; right tabs Template / Rules / Match / History), editable template grid → `set_field` ops, decision bar, action bar with keyboard shortcuts, remark thread with diffs, ask-vendor modal (copy, open mailto, copy portal link), duplicate compare view.
Done when: screenshots of S02b before the remark, after v2 (diff highlighted) and after approval; S04 compare view; typecheck and build pass.

## F3: Library, master data, Agent Studio, portal, launcher
Work: Approved library (multi-select, ZIP, batch export, history, re-export confirm), Vendors (detail, verification, approve), Purchase orders (consumption bars), Agent Studio (agents list, YAML + prompt editor with validation errors, versions, reset, test-agent dry run, workflow graph with React Flow, rules table editor), Scenario launcher, Vendor portal page, What-if (stretch).
Done when: screenshots of each screen; editing `price_tolerance.pct` to 5 in Agent Studio and re-running S08 flips it to auto_approve (then set it back).

## B5: Deploy (free)
Work: `render.yaml` (web service, free plan, `uv sync`, start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, health check `/healthz`); env vars on Render (Azure, `DATABASE_URL` = Supabase session pooler, `STORAGE_BACKEND=supabase`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `CORS_ORIGINS`, `PUBLIC_APP_URL`); Vercel project for `frontend/` with `VITE_API_URL`; SPA rewrite in `vercel.json`; cron-job.org ping to `/healthz` every 10 min; `make seed` against Supabase from your laptop (`DATABASE_URL=... make seed`).
Done when: the public URL runs S01 end-to-end; `/healthz` shows db=ok, storage=ok, llm_path=responses or chat.

## P6: Live pass and polish
Run every scenario on the deployed URL in `run_order`, using the live LLM with the cache off. Fix anything that differs from `expect`. Then do the stretch items in this order: vendor memory UI, replay, what-if, audit pack polish.
