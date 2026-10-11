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
