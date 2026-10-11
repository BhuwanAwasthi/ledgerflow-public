# LedgerFlow: AP invoice decision engine (Zamp case study, PS-1)

Source of truth: @docs/SPEC.md. Build order and done-checks: @docs/PHASES.md. Test data: `data/scenarios.yaml`.
When the spec and the code disagree, follow the spec. When the spec is silent, choose the simplest option and add a line to `docs/DECISIONS.md`.

## Stack
- backend/: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, sse-starlette, openai SDK, PyMuPDF, Pillow, rapidfuzz, Jinja2 (sandboxed), openpyxl, reportlab, pytest. Package manager: **uv**.
- frontend/: Vite + React 18 + TypeScript + Tailwind + shadcn/ui, TanStack Query + Table, react-router, react-pdf, recharts, cmdk, sonner, lucide-react. Package manager: **npm**.
- Dev: SQLite + local disk (`STORAGE_BACKEND=local`). Prod: Supabase Postgres (session pooler URL) + Supabase Storage. API on Render free, SPA on Vercel.

## Commands
- `make setup`: install backend (uv sync) and frontend (npm ci)
- `make dev`: API on :8000 + Vite on :5173
- `make test`: backend pytest with FakeLLM (no network). `make test-live`: `pytest -m live`, which hits Azure
- `make check`: ruff + pytest + `npm run typecheck` + `npm run build`. **Must pass before you say a phase is done.**
- `make fixtures`: regenerate data/master/*.csv and data/fixtures/*.pdf from data/scenarios.yaml
- `make seed`: reset the DB and load master data (+ seeded history if enabled)
- `uv run python backend/scripts/smoke_azure.py`: verify the Azure deployment (text, image, strict JSON)
- Shell is **Windows PowerShell 5.1**: chain commands with `;` or run them separately, never `&&` (a parser error in PS 5.1), and quote every path with spaces (the repo lives under `D:\DESKTOP EXTRACTION\...`).

Dev machine is **Windows 11 + PowerShell 5.1** (GNU make from ezwinports, Git for Windows, Node 24, uv). The Makefile must run here and on Linux (Render): keep recipes to plain `uv`/`npm` invocations, no bash-only syntax (`&&` chains inside one recipe line are fine for make, but avoid `$(shell ...)` pipelines, `rm -rf`, `cp`); use `uv run python -c` or small Python scripts for file operations. `make dev` must start both servers without relying on `&` backgrounding (use a Python launcher script or `npx concurrently`).

## Non-negotiable rules
- IMPORTANT: Money decisions are code. LLM output never sets a decision, rule status or amount without passing through rules/engine.py or decision code.
- Everything configurable lives in `config/` and is loaded through `config_loader.py`. No thresholds, prompts, column names or stage order hard-coded in Python or TSX.
- Adding an agent = class in `backend/app/agents/` + `config/agents/<id>.yaml` + a stage in `config/workflow.yaml`. Follow the `add-agent` skill.
- Every stage writes a `stage_event` (started → completed/failed/waiting) with reasoning lines and metrics; the UI relies on it.
- Never read, print or commit `.env`. Secrets come from env vars only.
- No email-sending libraries or APIs. Outbound messages are drafts + `mailto:` links.

## Azure OpenAI (GPT-5.2) gotchas
- Client: `OpenAI(base_url=f"{AZURE_OPENAI_ENDPOINT}/openai/v1/", api_key=...)`; `model` = the deployment name.
- Reasoning model: do NOT send `temperature`, `top_p`, `max_tokens`. Use `reasoning={"effort": ...}` and `max_output_tokens` (Responses) or `max_completion_tokens` (Chat).
- Strict JSON schema: every property listed in `required`, `additionalProperties: false`, optional values as `["type","null"]`. Generate schemas from Pydantic and post-process them in `llm.py`.
- `status == "incomplete"` → retry once with double the max_output_tokens.
- Tests must never call Azure unless marked `@pytest.mark.live`.

## Code style
- Backend: type hints everywhere, `Decimal` for money, async FastAPI routes, small pure functions in rules/. Ruff defaults.
- Frontend: function components, typed API client generated from `/openapi.json` (`npm run gen:api`), no `any`. Status colours come only from `src/lib/status.ts`.

## Working agreement for autonomous runs
- Work one phase of PHASES.md at a time. At the end of a phase: run `make check`, show the output, commit with message `phase N: <summary>`.
- If a check fails twice for the same reason, stop and write the blocker in `docs/DECISIONS.md` instead of hacking around it (no skipped tests, no `# type: ignore` to silence errors).
- When compacting, preserve: current phase number, files changed in this phase, failing tests and their errors.
