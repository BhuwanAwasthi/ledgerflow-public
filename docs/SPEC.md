# LedgerFlow — AP Invoice Decision Engine (Product Spec)

> Working name: **LedgerFlow**. Rename freely; it appears only in `config/app.yaml` and the UI header.
> Case study: Zamp ASA, PS-1 (Invoice processing: from PDF to decision), with light vendor onboarding (PS-2) inside one edge case.

## 0. One-paragraph pitch

A vendor or employee submits invoices on a portal. Eleven configurable agents classify the documents, extract the fields, match them to POs, apply procurement rules and return a **reasoned decision**: Auto-approve, Needs review, On hold or Reject. Every step is visible live. A reviewer sees the source documents beside the generated ERP template. They approve it, or type a remark that the Reconciler agent applies and re-presents with a diff. Approved invoices land in an **Approved library**, where they are downloaded individually or exported in bulk as one ERP import file. The model gathers evidence, and rules make the decision.

## 1. Hard constraints

| Constraint | Consequence |
| --- | --- |
| The only paid service is the Azure OpenAI GPT-5.2 deployment | One LLM adapter (`backend/app/llm.py`); no other model or paid OCR API |
| Free hosting | Frontend on Vercel Hobby (static Vite build), API on Render free web service, Postgres + file storage on Supabase free |
| No email-sending API | Outputs are **downloads** (single, bulk ZIP, batch import file). Vendor queries are **drafts** with a `mailto:` link and a no-login vendor upload link |
| Built in one day | Phased build; every phase ends with a runnable check (ruff + pytest + typecheck + build) |
| Configurable like the Power Automate Work Order system | Workflow, agents, prompts, rules, document types and ERP profiles are YAML or Markdown in `config/`, editable in the **Agent Studio** UI |

Free-tier facts that shape the design (checked Oct 2026):
- Render free: 512 MB RAM, 0.1 CPU, sleeps after 15 min without inbound traffic, about a 1-minute cold start, 750 instance-hours a month. Render free Postgres expires after 30 days, so **don't use it**; use Supabase.
- Supabase free: 500 MB database, 1 GB storage, 50 MB max upload, 2 active projects, paused after 1 week of inactivity.
- Vercel Hobby: static hosting is free; functions are not needed.
- Keep-warm: a free cron-job.org (or UptimeRobot) ping to `GET /healthz` every 10 min on demo days.

## 2. Architecture

```
[Vite React SPA on Vercel]  --REST + SSE-->  [FastAPI on Render]
                                               |-- Orchestrator (deterministic state machine from config/workflow.yaml)
                                               |-- Job runner (in-process asyncio queue, concurrency 2, resumable)
                                               |-- Agents (11), each = class + YAML + optional prompt
                                               |-- LLM adapter -> Azure OpenAI GPT-5.2 (Responses API, structured outputs, image input)
                                               |-- Storage adapter -> local disk (dev) | Supabase Storage (prod)
                                               '-- DB (SQLAlchemy) -> SQLite (dev) | Supabase Postgres (prod)
```

Principles:
1. **The Orchestrator is code, not an LLM.** It reads `workflow.yaml`, runs a stage's agent and action, maps the returned `outcome` to the next stage, persists a `stage_event`, and emits SSE. This replaces Work Orders + Workflow Queue from the HCN Workflow Manager V2.
2. **An agent is like a Copilot Studio agent, and an action is like a topic.** One agent can expose several actions (`template.generate_draft`, `template.finalize`), selected by the stage config, the way `Context` picked a topic in HCN.
3. **LLMs only perceive and explain.** They classify, extract, infer PO candidates, parse remarks and write plain-English explanations. Money decisions come from `rules.yaml` + `decision` code.
4. **Every run snapshots the config** it used (the hash plus the full resolved YAML), so a decision is reproducible after the config changes.
5. **Resumable.** On startup, runs in `processing` restart from their last completed stage. Runs `waiting` on an event resume when the event arrives.

## 3. Repository layout

```
/
├─ README.md
├─ docs/  SPEC.md  DECISIONS.md  DEPLOY.md  DEMO_SCRIPT.md
├─ config/
│  ├─ app.yaml                # branding, feature flags, LLM pricing, limits
│  ├─ workflow.yaml           # stage graph (Work Orders equivalent)
│  ├─ rules.yaml              # policy rules + thresholds + severities
│  ├─ document_types.yaml     # classifier labels
│  ├─ agents/*.yaml           # one file per agent
│  ├─ prompts/*.md            # LLM prompts referenced by agents
│  └─ erp_profiles/*.yaml     # template mappings (Sage Intacct, QuickBooks Online, generic CSV)
├─ data/
│  ├─ scenarios.yaml          # exact spec of every synthetic test document
│  ├─ master/ vendors.csv purchase_orders.csv po_lines.csv grns.csv gl_accounts.csv   (generated)
│  └─ fixtures/*.pdf          # generated invoices and supporting docs
├─ backend/
│  ├─ pyproject.toml          # uv-managed
│  ├─ app/
│  │  ├─ main.py              # FastAPI app, CORS, routers, startup resume
│  │  ├─ settings.py          # env vars (pydantic-settings)
│  │  ├─ db.py models.py      # SQLAlchemy models (section 6)
│  │  ├─ config_loader.py     # YAML + DB overrides -> resolved config, with validation
│  │  ├─ orchestrator.py      # state machine + job runner + SSE event bus
│  │  ├─ llm.py               # Azure OpenAI adapter (section 8)
│  │  ├─ storage.py           # local | supabase
│  │  ├─ agents/base.py       # Agent ABC, AgentContext, AgentResult, registry
│  │  ├─ agents/<one file per agent>.py
│  │  ├─ rules/engine.py      # rule registry, evaluate(case, config) -> RuleResult[]
│  │  ├─ exports/             # template rendering, zip, batch files, audit-pack PDF
│  │  ├─ api/                 # routers: cases, review, exports, vendors, pos, config, dashboard, fixtures, portal
│  │  └─ seed.py              # loads data/master/*.csv into DB
│  ├─ scripts/ generate_fixtures.py  smoke_azure.py
│  └─ tests/                  # pytest; LLM mocked by default, `-m live` hits Azure
├─ frontend/                  # Vite + React + TS + Tailwind + shadcn/ui
└─ render.yaml                # Render blueprint for the API
```

## 4. Configuration (the Power Automate parity layer)

All files live in `config/`. `config_loader.py` validates them with Pydantic models at startup and on every save from Agent Studio. **An invalid config is rejected with a readable error and is never half-applied.**

DB overrides: Agent Studio saves edits into the `config_versions` table (path, content, version, author, created_at). The resolved config is the YAML files overlaid with the latest active DB version per path. "Reset to file" deletes the override.

### 4.1 `workflow.yaml` (see the file for the full graph)
- `stages[]`: `id`, `agent`, `action`, `next: {outcome: next_stage_id}` (not `on`: YAML 1.1 parses a bare `on` key as boolean true), optional `hitl: true`, `wait_for: <event>`, `resume_to: <stage>`, `retries`, `timeout_s`, `status_label` (shown in the UI journey).
- Reserved next targets: `end:<terminal_status>` (for example `end:approved`, `end:rejected`).
- The loader verifies that every outcome an agent declares in its YAML `outcomes` is mapped, every target exists, and there are no unreachable stages.

### 4.2 `agents/<id>.yaml`
`id, name, description, kind (llm|code|hybrid), enabled, handler (python path), actions{name: {outcomes[], prompt?, params?}}, model{deployment, reasoning_effort, max_output_tokens}, params{}, retries, timeout_s`.
A disabled agent's stage is skipped with outcome `skipped` (it must be mapped, or the loader rejects the config). This lets the reviewer turn off, for example, GRN matching.

### 4.3 `rules.yaml`
Each rule: `id, name, enabled, applies_to (po|non_po|all), severity (reject|hold|review|info), params, explain_template`. Plain-English results are rendered from `explain_template` (Jinja2) so reasons are deterministic, for example: "Invoice subtotal {{ invoice_amount }} exceeds PO remaining {{ remaining }} by {{ pct }}% (tolerance {{ tol_pct }}%)."

### 4.4 `document_types.yaml`
Labels with descriptions and `route: case_invoice | evidence | onboarding | ignore`.

### 4.5 `erp_profiles/<id>.yaml`
`id, name, file_format (csv|xlsx), grain (line|header), filename_template, columns[{header, value (Jinja2 sandboxed expression), required}]`, plus a `batch` section for multi-invoice import files. The Template agent renders the rows; the profile's `required` flags drive validation.

### 4.6 `app.yaml`
Branding, `features` flags (vendor_portal, vendor_memory, what_if, imap_intake, llm_cache), `llm.pricing_per_1m` (input and output USD, filled in by the user), `limits` (max_pages, max_upload_mb, concurrency).

## 5. Agents (11), contracts

All agents implement:
```python
class Agent(ABC):
    id: str
    async def run(self, action: str, ctx: AgentContext) -> AgentResult: ...

@dataclass
class AgentResult:
    outcome: str                 # must be in the agent YAML's outcomes for that action
    data: dict                   # merged into case state under the agent's key
    reasoning: list[str]         # 1-5 plain-English sentences shown in the live run view
    confidence: float | None
    metrics: dict                # tokens_in, tokens_out, cost_usd, latency_ms, cache_hit
```
`AgentContext` gives read access to the case, documents, master data, resolved config and LLM adapter, plus `emit(msg)` for sub-step progress lines.

| # | Agent | Kind | Actions → outcomes | Behaviour |
| --- | --- | --- | --- | --- |
| 1 | intake | code | `ingest` → ok, empty, too_large | Store files, sha256, convert images and DOCX to PDF (PyMuPDF/Pillow), render page PNGs (150 dpi, max 8 pages), extract the text layer, detect a scanned PDF (no text), split bundles **after** classification marks page ranges |
| 2 | classifier | llm | `classify` → has_invoice, no_invoice | Per document (first 2 pages as images + text) → label from document_types.yaml + confidence + page_ranges if the PDF holds several documents. Spawns child cases when a bundle holds more than one invoice |
| 3 | extraction | llm | `extract` → ok, low_confidence, failed | Vision + text → `InvoiceExtraction` schema (section 7). For text PDFs, locate each value with PyMuPDF `search_for` to get exact bounding boxes; for scans use the model's normalised boxes. Injects **vendor memory** hints when the vendor is known |
| 4 | normaliser | code | `normalise` → ok | Dates to ISO, amounts to Decimal, currency inference, invoice-number normalisation (upper-case, strip non-alphanumerics), embedded-tax split using `tax_rate`, unit-of-measure aliases |
| 5 | vendor | hybrid | `check` → approved_vendor, new_vendor, bank_mismatch, inactive_vendor; `request_onboarding` → requested | Match on tax ID (exact), then name (rapidfuzz token_set ≥ 90), then email domain. Compare the bank account last 4 digits and IBAN hash. `request_onboarding` creates a vendor_request with the documents needed (W-9 or tax certificate, bank letter) and a portal token |
| 6 | po_matching | hybrid | `match` → matched, implied, no_po; `suggest_gl` → coded | Explicit PO reference must exist, be open and belong to the vendor. Otherwise rank the vendor's open POs: amount closeness 40%, line-description similarity 40% (LLM judgement, effort low, given the candidate lines), date window 20%. A top score ≥ 0.85 with a gap ≥ 0.15 to the next → implied (pre-linked). Line matching: pair invoice lines to PO lines (LLM-assisted pairing + quantity/price math in code). `suggest_gl`: GL account + department from vendor history / category → `gl_accounts.csv` |
| 7 | policy | code | `evaluate` → evaluated | Runs every enabled rule in rules.yaml; stores RuleResult rows (`rule_id, status pass/warn/fail, severity, explanation, evidence{}`) |
| 8 | decision | hybrid | `decide` → auto_approve, needs_review, on_hold, reject | Code: decision = highest severity among failed rules (reject > hold > review). Auto-approve only if no failures **and** every gate in `rules.yaml: auto_approve_gates` passes. The LLM (effort low) writes a 2–4 sentence buyer-friendly explanation **from the rule results only**; it can't change the decision |
| 9 | template | code | `generate_draft` → ok; `finalize` → ok | Render the ERP profile rows, validate required columns, store a `template_version` (v1, v2…). Finalize freezes the version and sets the case to approved |
| 10 | reconciler | hybrid | `apply_remark` → changes_applied, needs_clarification | LLM converts a remark into a `ChangeSet` of typed ops (section 7). Code validates and applies them, records overrides with the reviewer and reason, then the workflow reruns policy → decision → template. Ambiguous remarks return a clarifying question shown in the review thread |
| 11 | comms | hybrid | `draft_request` → drafted; `draft_rejection` → drafted; `notify` → ok | No email API. Writes a draft (subject + body) for the vendor or submitter, builds a `mailto:` link, generates the vendor portal upload link, and creates in-app notifications. The UI shows "Copy", "Open in mail app" and "Copy upload link" |

## 6. Data model (SQLAlchemy)

- `vendors` (id, code, legal_name, aliases[], tax_id, country, email_domain, bank_last4, iban_hash, category, status: approved|pending_docs|rejected|inactive, default_gl, created_at)
- `vendor_requests` (id, vendor_id nullable, proposed_name, token, docs_needed[], docs_received[], status, created_at)
- `purchase_orders` (id, po_number, vendor_id, currency, amount, status: open|closed, created_at, approved_by)
- `po_lines` (id, po_id, line_no, description, qty, unit_price, amount, gl_code)
- `grns` (id, po_id, line_no, qty_received, received_at)
- `gl_accounts` (code, name, category)
- `cases` (id, ref like `INV-2026-0001`, parent_case_id, submitter_name, submitter_email, source: portal|upload|imap|fixture, status, current_stage, decision, decision_summary, vendor_id, po_id, currency, total, confidence, config_hash, config_snapshot JSON, created_at, updated_at, sla_due_at)
  - status enum: received, processing, needs_review, on_hold, approved, exported, rejected, failed
- `documents` (id, case_id, filename, mime, sha256, storage_key, pages, label, label_confidence, page_from, page_to, has_text_layer)
- `extractions` (id, case_id, version, data JSON (InvoiceExtraction), field_meta JSON {field: {confidence, page, bbox}})
- `po_consumption` (id, po_id, po_line_id nullable, case_id, amount, qty, state: reserved|consumed|released)
- `rule_results` (id, case_id, run_no, rule_id, status, severity, explanation, evidence JSON)
- `stage_events` (id, case_id, run_no, seq, stage_id, agent_id, action, status: started|completed|failed|skipped|waiting, outcome, input_summary JSON, output JSON, reasoning JSON, metrics JSON, started_at, ended_at) — **append-only**
- `template_versions` (id, case_id, version, erp_profile, rows JSON, file_key, status: draft|final, diff_from_prev JSON, created_by: agent|reviewer)
- `review_actions` (id, case_id, actor, action: approve|remark|reject|request_info|override, text, changeset JSON, created_at)
- `export_batches` (id, ref like `EXP-0001`, erp_profile, case_ids[], file_key, created_by, created_at)
- `notifications` (id, audience: submitter|reviewer, case_id, text, read, created_at)
- `vendor_memory` (id, vendor_id, hint, source_case_id, created_at) — learned from reviewer corrections
- `config_versions` (id, path, content, version, active, author, created_at)
- `llm_cache` (key sha256, response JSON, created_at)

PO balance: remaining = PO amount − sum(reserved + consumed). An approved case consumes its reservation; a rejected case releases it. Reservation happens at the decision stage for auto_approve or needs_review, so split invoices processed concurrently can't both pass.

## 7. Key schemas (Pydantic → strict JSON schema)

`InvoiceExtraction`: vendor_name, vendor_address, vendor_tax_id, vendor_email, remit_bank {bank_name, account_last4, iban}, invoice_number, invoice_date, due_date, payment_terms, currency, po_reference, other_references[] (quote numbers and similar), bill_to, lines[{description, qty, unit_price, amount, tax_rate, uom}], subtotal, tax_total, tax_inclusive (bool), shipping, total, notes, per-field confidence {field: 0..1}, per-field location {field: {page, bbox[x0,y0,x1,y1] normalised 0..1}}. Every property is required; unknowns are null (strict-mode rule).

`ChangeSet.ops[]`, a discriminated union on `op`:
- `set_field {path, value}` (for example `lines[2].amount`)
- `set_gl {line_index, gl_code, department?}`
- `link_po {po_number}`
- `unlink_po {}`
- `override_rule {rule_id, reason}`
- `set_po_amount_note {po_number, new_amount, reason}` (records the amendment and raises the effective PO amount for this case at once; on approval it becomes the PO master amount with a `po_amendments` audit row: amended_by, old/new amount, reason, case_id)
- `add_vendor_memory {hint}`
- `ask {question}` (results in needs_clarification)

## 8. LLM adapter (`backend/app/llm.py`)

- Client: `openai.OpenAI(base_url=f"{AZURE_OPENAI_ENDPOINT}/openai/v1/", api_key=AZURE_OPENAI_API_KEY)`; model = deployment name `AZURE_OPENAI_DEPLOYMENT` (for example `gpt-5.2`).
- Primary call: `client.responses.create(model=..., input=[{role, content:[input_text|input_image]}], reasoning={"effort": cfg}, max_output_tokens=cfg, text={"format": {"type": "json_schema", "name": ..., "schema": ..., "strict": True}})`.
- **Never send** `temperature`, `top_p` or `max_tokens` (reasoning models reject them).
- If `response.status == "incomplete"` because of max_output_tokens: retry once with double the budget.
- Fallback when the Responses API is unavailable on the deployment: `chat.completions.create(..., response_format={"type": "json_schema", ...}, max_completion_tokens=...)`. Pick the path once at startup with `scripts/smoke_azure.py` logic, and record it in the `/healthz` output.
- Images: `{"type": "input_image", "image_url": "data:image/png;base64,..."}`.
- Every call returns `(parsed, metrics)`, where metrics hold tokens, cost (from `app.yaml` pricing), latency and cache_hit. An optional cache keyed on sha256(model + effort + prompt + schema + input hashes) is controlled by `features.llm_cache` (on while building the UI, **off for the live demo**).
- Tests use `FakeLLM`, which returns fixtures from `backend/tests/llm_fixtures/*.json`.

## 9. API

```
GET  /healthz                                   -> {ok, db, storage, llm_path}
POST /api/cases            (multipart: files[], submitter_name, submitter_email, note)  -> case
GET  /api/cases?status=&q=&vendor=&page=        -> list
GET  /api/cases/{id}                             -> full case (documents, extraction, rule results, decision, templates, events)
GET  /api/cases/{id}/events/stream               -> SSE: stage_event objects + "case_updated"
GET  /api/cases/{id}/documents/{doc_id}/file     -> PDF bytes;  /pages/{n}.png
POST /api/cases/{id}/review   {action: approve|remark|reject|request_info, text?}  -> case
POST /api/cases/{id}/rerun    {from_stage}       -> case (admin)
GET  /api/cases/{id}/template/{version}/download -> csv|xlsx
GET  /api/cases/{id}/audit-pack                  -> PDF (decision report + events + rule results)
GET  /api/approved?exported=false                -> list
POST /api/exports  {case_ids[], erp_profile, mode: zip|batch}  -> export_batch (+ download URL)
GET  /api/exports, /api/exports/{id}/download
GET  /api/vendors, /api/vendors/{id}; POST /api/vendors/{id}/approve | reject
GET  /api/pos, /api/pos/{id}                      -> with consumption
GET  /api/dashboard/metrics?range=7d              -> KPIs + series
GET  /api/config, GET/PUT /api/config/{path}      -> Agent Studio (validates before save)
POST /api/config/test-agent {agent, action, case_id} -> dry run, no persistence
POST /api/whatif {rule_overrides}                 -> re-decides the last N cases in memory, returns flips
GET  /api/fixtures; POST /api/fixtures/{scenario}/run   -> one-click demo scenarios
GET  /portal/{token}  (public)                    -> vendor upload page data; POST /portal/{token}/upload
GET  /api/notifications?audience=
```

## 10. UI (Vite + React + TS + Tailwind + shadcn/ui)

Visual language: a calm, finance-grade look. Neutral surfaces, one accent (indigo), status colours that always mean the same thing (approved emerald, needs review amber, on hold sky, rejected rose, processing violet), Inter with tabular numerals for money, 8-pt spacing, light and dark mode. Use skeleton loaders, never spinners on whole pages. Every list is a dense TanStack Table with sticky headers, column filters and saved views.

A role switcher in the header (demo, no auth): **Submitter**, **AP Reviewer**, **Admin**. This is stated as an assumption.

Screens:
1. **Submit** (Submitter): drag-drop multi-file upload, name + email, note. After submit it shows a tracking card with the case ref and a link to the journey.
2. **My submissions / Journey tracker** (Submitter): a courier-style horizontal tracker per case — Received → Classified → Extracted → Matched → Checked → Decided → In review → Approved → Exported — with timestamps, the current status in plain English ("Waiting for your bank letter"), and the vendor upload link when on hold.
3. **Dashboard** (Reviewer, graded): KPI tiles (cases, straight-through %, in review, on hold, median time to decision, exceptions caught in $, LLM cost per invoice), a cases-over-time line, exceptions by rule (bars), decision mix, and a recent-cases table. Range filter.
4. **Live run view** (graded): a vertical stage timeline driven by SSE. Each card shows the agent and action, status chip, duration, 1–5 reasoning lines, key outputs as chips (for example "PO-1001 · remaining $4,000"), a collapsible JSON panel, and tokens and cost. The active stage animates; a failed stage shows the error and a Retry button; a waiting stage shows what it waits for. Includes **Replay** of a finished run (re-animates the stored events).
5. **Work queue** (Reviewer): cases needing review or on hold, sorted by SLA age, with the main reason in one line.
6. **Review workspace** (HITL): the decision bar (badge, explanation, confidence, vendor, PO, amounts, PO balance mini-bar). **Left**: PDF viewer (react-pdf) with document tabs and bounding-box highlights; hover a field to highlight it. **Right**: tabs for Template (editable grid in ERP profile columns; amber cells = low confidence or rule-flagged; inline edits become `set_field` ops), Rules (pass/warn/fail checklist), Match (invoice lines vs PO lines vs GRN qty) and History (versions with diffs, remarks thread). Action bar: Approve (A), Request changes (R, remark box), Reject (X, reason required), Ask vendor (V, comms draft modal). Duplicate cases open a side-by-side compare.
7. **Approved library**: approved cases with an exported/not-exported filter, multi-select, **Download selected (ZIP of per-invoice files)**, **Export batch (one ERP import file)**, per-row download, audit pack. Export history tab (EXP-0001…) with re-download; re-exporting an already-exported case needs confirmation (it prevents double posting).
8. **Vendors**: master list with status; vendor detail shows requests, received docs, verification checks (name on bank letter vs legal name, tax ID format), Approve/Reject, invoices waiting, and learned vendor memory hints.
9. **Purchase orders**: list with consumption bars (consumed / reserved / remaining) and linked invoices; split invoices are visible here.
10. **Agent Studio** (Admin): left list of agents (enabled toggle, kind, avg latency, cost); the right pane edits YAML and prompt (Monaco or CodeMirror) with validation errors inline, version history and reset, plus **Test agent** on a chosen case (dry run, shows output JSON). Tabs for Workflow (a read-only graph from workflow.yaml rendered with React Flow, edit as YAML), Rules (table editor: enabled, severity, params), ERP profiles, Document types.
11. **What-if simulator** (stretch): sliders for tolerance %, abs $, auto-approve ceiling, showing "N of last 50 decisions would change" with the list.
12. **Scenario launcher**: buttons for each fixture in `data/scenarios.yaml` ("Run: split PO part 2"), used in the demo.
13. **Vendor portal** `/portal/:token` (public, no login): branded page listing the requested docs and an upload dropzone. An upload attaches to the vendor_request and fires the `info_received` / `vendor_docs_received` event.

Global: command palette (Ctrl/Cmd+K: jump to a case by ref, run a scenario, open a screen), toasts, a notification bell, and a "Waking the engine…" banner when `/healthz` is cold.

## 11. Edge cases (must pass as automated tests with FakeLLM, and live)

| Scenario id (data/scenarios.yaml) | Expected decision | Key assertions |
| --- | --- | --- |
| S01_happy | auto_approve | All rules pass; template v1 final; PO-1002 consumed 4,415.00 |
| S02a_split_part1 | auto_approve | PO-1001 remaining goes from 10,000 to 4,000 |
| S02b_split_part2 | needs_review | po_balance fails (4,300 vs 4,000 = +7.5% > 2%); unmatched line "Premium support"; after the remark "PO was amended to $10,500, accept the premium support line and code it to 6410" → reconciler ops [set_po_amount_note, set_gl, override_rule(unmatched_lines)] → policy passes → `sticky_review` routes it back to review with template v2 + diff → reviewer approves → approved |
| S03_implied_po | needs_review | po_reference null; candidates PO-1004 (top, ≥ 0.85) and PO-1006; pre-linked PO-1004; rule implied_po_requires_review fails |
| S04_duplicate_scan | reject | Scanned PDF (no text layer); normalised number INVA2207 equals S01 → duplicate_exact; compare view links S01 |
| S05_unknown_vendor | on_hold | vendor new_vendor → request_onboarding → waiting; uploading the W-9 and bank letter via the portal → vendor verified → reviewer approves the vendor → case resumes → non-PO or needs_review |
| S06_bank_change | on_hold | bank_details_match fails (9910 vs 7788) → explanation mentions payment-fraud risk; request draft asks for a signed bank letter |
| S07_non_po | needs_review | no_po → suggest_gl 6100 Facilities Maintenance; non_po_requires_review |
| S08_price_variance | needs_review | line unit price 1,310 vs PO 1,250 = +4.8% (> 2%) and +$60 per unit (> $50) |
| S09_missing_fields | on_hold | invoice_number and invoice_date null → required_fields fail → request draft lists the missing fields |
| S10_bundle | split into 2 child cases | one PDF holding two invoices → 2 cases, each decided independently |

## 12. Non-functional

- p50 end-to-end under 90 s per single-page invoice on GPT-5.2 at low/medium effort.
- Upload limit 15 MB per file, 8 pages per document (config).
- All money as Decimal; display with currency.
- No secrets in the repo. `.env` is gitignored.
- Structured JSON logs; each stage_event carries latency and tokens.
- CORS allows the Vercel domain and localhost:5173.

## 13. Out of scope (state as assumptions in the demo)

Real ERP posting (the export file stands in for it), real auth/SSO (role switcher), outbound email (drafts + mailto), payments, multi-currency FX conversion (currency-mismatch rule only), and 3-way match beyond GRN quantity checks.
