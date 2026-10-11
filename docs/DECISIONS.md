# Decisions and assumptions log

Claude appends here when the spec is silent or a phase defers something. State these in the demo.

- PS-1 chosen; vendor onboarding (PS-2) is a light mini-flow inside one edge case.
- No ERP posting: the approved ERP import file (single or batch) stands in for posting.
- No outbound email API: vendor/submitter messages are drafts with mailto links; missing documents arrive via a no-login vendor portal link.
- Demo has no real auth: a role switcher (Submitter / AP Reviewer / Admin).
- 2-way match by default; GRN quantity check when a GRN exists.
- Tolerance: unit price must exceed both 2% and $50 per unit to fail; PO balance tolerance 2%.
- Implied PO matches never auto-approve.
- Seeded dashboard history is labelled as seeded demo data in the UI.

## Phase B1
- Case status enum adds `unsupported`: workflow.yaml ends at `end:unsupported` (no invoice found) but SPEC §6 omitted it. Allowed terminal targets: approved, rejected, unsupported, failed.
- `workflow.yaml options.sticky_review_redirect: {auto_approve: template_draft}` names where sticky review sends an auto-approve once a reviewer has acted, so the stage id is not hard-coded. "Has been reviewed" = at least one review_action row exists.
- Pause statuses: a `hitl` stage sets the case to `needs_review`; a `wait_for` stage sets it to `on_hold` (with `sla_due_at` from `app.sla_hours`).
- Each case runs on the config snapshot taken at creation (hash + full files in `cases.config_snapshot`); later Agent Studio edits apply to new cases (and to reruns, once rerun exists).
- Case state is not a column: it is folded from completed/waiting stage_event outputs per agent id (append-only, replayable).
- `run_no` increments on every resume (review action, event, startup restart); stage_events `seq` is per case.
- Added `cases.note` (upload form note; not in SPEC §6) and `app.yaml limits.max_stages_per_run` (loop guard).
- Agents whose handler module does not exist yet run as `StubAgent`; `FORCE_STUB_AGENTS=true` forces stubs everywhere. Tests always force stubs and script outcomes per stage (`!error` / `!sleep:s` to test retries and timeouts).
- Sync SQLAlchemy sessions are used inside async routes and the orchestrator (queries are tiny; concurrency is 2). Revisit if Postgres latency on Render shows up in the live view.
- `make check` skips the frontend typecheck/build while `frontend/package.json` does not exist (F1 creates it); it uses make's `$(wildcard)` so it is shell-agnostic.
- SSE: `GET /api/cases/{id}/events/stream` replays stored events (honours `Last-Event-ID`), then streams live `stage_event`, `case_updated` and `progress` events; `?follow=false` closes after the replay.
- Accepting a review action or a `wait_for` event writes the paused stage's `completed` event and moves `current_stage` to the next target before the job is queued, so a restart (Render sleeps often) continues from there instead of losing the action.
- An orchestrator-level crash (loop guard, DB error) still appends a `failed` stage_event with the error text.
- Deferred: a review POST that arrives while the same case is mid-run waits for the per-case lock before getting its 409. That's harmless with stubs; revisit in B2 when LLM stages take seconds.

## Phase D1
- scenarios.yaml gained print-only realism data the spec left open: vendor `address` and `bank_name` (incl. the S05 new vendor), PO `created_at` / `approved_by` (needed by the 20% date-window score), GRN `received_at`. Totals and IDs are unchanged.
- Data model additions (SPEC §6 silent): `cases.seed` (seeded demo history flag, exposed as `CaseOut.seed`), `vendors.non_po_allowed` (from scenarios.yaml V004), `purchase_orders.notes` ("Raised from quote Q-77", the S03 implied-PO evidence). No Alembic: `make seed` recreates the tables.
- `make fixtures` runs `python -m scripts.generate_fixtures` from backend/ (so `app` imports without sys.path hacks). Output is byte-deterministic (reportlab invariant mode, seeded scan noise); a test regenerates into tmp and fails if committed fixtures are stale (scan JPEG bytes excluded, as they can differ across Pillow builds).
- Four layout families (classic, banner, ledger/Courier, sidebar) styled per vendor with different labels, date formats ("Oct 02, 2026", "2026-10-05", "08 Oct 2026", "October 01, 2026") and money formats ("$1,250.00" vs "1,250.00 USD"). Dates are always unambiguous (no 10/02/2026). Every PDF carries a tiny "synthetic test document, fictional companies" footer.
- LLM output schemas live in `app/llm_schemas.py` (InvoiceExtraction, ChangeSet, plus ClassifierOutput, POInferenceOutput, GLCodingOutput, DecisionExplanation, CommsDraft). Strict-mode shaped: `confidence` and `location` are fixed-key objects (strict mode forbids free-form maps); `location` covers the 7 header fields the prompt names. Money is `Decimal` in Python and `number` in the JSON schema. B2's llm.py should reuse these.
- FakeLLM fixture format (`tests/llm_fixtures/<scenario>__<agent>.json`): `{scenario, agent, documents, calls: [{action, schema, when, output}]}`. A call matches when all its `when` keys equal the caller's context (`document`, `pages`, `invoice_number`, `decision`, `remark`); empty `when` = default. Only LLM-calling actions have fixtures: classify (incl. the S05 W-9 and bank letter), extract, po_matching.match (explicit PO = 1 candidate for line pairing; no PO = all the vendor's open POs; none when the vendor has no POs), suggest_gl (S05, S07), decide, apply_remark (S02b), comms drafts (S04 rejection; S05/S06/S09 requests).
- Expected extraction confidences: clean 0.95-0.99; scans ≤ 0.8 (the prompt's rule), so a scan can never pass the 0.90 auto-approve gate. A field that is absent gets `null` confidence and `null` location (not 0).
- Comms fixture bodies say "through the secure link below" instead of containing a URL: the portal token only exists at run time, so the comms agent appends the link.
- Seeded history (40 cases over 21 days, deterministic RNG, dates relative to "now"): final status approved (or rejected for reject decisions) so it never fills the work queue; a completed stage_event per happy-path stage up to `decide` (path walked from workflow.yaml, LLM stages carry token metrics, cost from app.yaml pricing); one or two failed rule_results chosen from rules.yaml by the severity matching the decision. No PO consumption, so demo balances start exactly as in scenarios.yaml.
- Fixture cases are submitted as the vendor's billing contact (`billing@<domain>`), or the buyer's AP mailbox for the two-vendor S10 bundle. Supporting files (S05 W-9, bank letter) are downloadable from `GET /api/fixtures/{id}/files/{filename}` for the vendor-portal demo step; they are not attached to the case.
- Notes for B2/B3 from the D1 spec review: (1) `ChangeSet.ops` produces `oneOf` + `discriminator`; llm.py must rewrite to `anyOf` and drop `discriminator` for strict mode. (2) FakeLLM `when` values are exact: the S02b reconciler fixture keys on scenarios.yaml `demo_remark` verbatim; S10 keys on the extracted (not normalised) invoice number; S10 child cases must not re-classify by the parent filename. FakeLLM should fall back to the scenario file's first call for an action when nothing matches. (3) Running the full run_order cumulatively, PO-1003 (5,000, GRN qty 3) gets S08 + S10 CL-781 + S09, so S09 also fails `po_balance`/`quantity_vs_grn`, and S04 also fails PO-1002 balance/GRN after S01. Scenario tests should assert `failed_rules` as a subset and check the decision itself.
