# LedgerFlow build kit: start here

This kit is everything Claude Code needs to build the whole system today, without you writing code:
- `CLAUDE.md`: the rules Claude follows in every session
- `docs/SPEC.md`: the full product spec (architecture, agents, data model, API, UI, edge cases)
- `docs/PHASES.md`: 10 phases with time targets and "Done when" checks
- `config/`: the configurable layer (workflow, 11 agents, prompts, rules, ERP profiles), already written
- `data/scenarios.yaml`: exact test data for 11 scenarios, so tests can assert real numbers
- `.claude/`: permissions, 2 reviewer subagents, and the `/run-phase`, `/add-agent` and `/live-check` skills
- `backend/scripts/smoke_azure.py`: checks that your GPT-5.2 deployment supports what the build needs

Total spend: your Azure OpenAI usage. Hosting is Vercel Hobby + Render free + Supabase free.

---

## 1. One-time setup (about 30 min)

1. Install Git, Node 20+, uv (`pip install uv` or the official installer), and VS Code with the **Claude Code** extension.
2. Create an empty folder, unzip this kit into it, then:
   ```bash
   git init && git add -A && git commit -m "kit"
   cp .env.example .env      # fill in the 3 AZURE_OPENAI_* values
   uv run --with openai --with python-dotenv --with pillow python backend/scripts/smoke_azure.py
   ```
   All three lines should print OK (at least one path must). If the image check fails, stop and fix the deployment first: extraction depends on it.
3. Give Claude a browser so it can check its own UI: in the VS Code terminal run
   `claude mcp add playwright -- npx @playwright/mcp@latest`
4. Create the free accounts now so deploys don't block you later: GitHub, Supabase (create a project + private bucket `documents`), Render, Vercel, cron-job.org.

## 2. Make Claude Code run on its own

- **Permission mode: Auto.** In the VS Code prompt box, pick *Auto* in the mode selector (in the terminal CLI, `Shift+Tab` cycles modes). A classifier then reviews actions instead of you approving each one. `.claude/settings.json` already pre-approves `make`, `uv`, `npm`, `pytest` and `git add/commit`, and **denies reading `.env`**. If Auto isn't offered on your plan, use *Accept edits*; the allowlist still removes most prompts.
- **Never paste your key into the chat.** It lives in `.env`, which Claude is blocked from reading.
- **One phase per session context.** Before each phase run `/clear`, then `/run-phase <id>`. The skill makes Claude plan, build, test, run the `spec-reviewer` subagent on its own diff, run `make check`, and commit. You only watch and redirect.
- **Make it keep going until the checks pass:** after `/run-phase B3`, you can also set `/goal make check passes and every "Done when" item of phase B3 in docs/PHASES.md has evidence`. Claude keeps working until the goal holds.
- **Redirect early:** `Esc` stops Claude without losing context; `Esc Esc` (or `/rewind`) restores code and conversation to an earlier checkpoint. After two failed corrections on the same issue, `/clear` and restate the problem with what you learned.
- **Two sessions in parallel** (this is how you finish today):
  ```bash
  git worktree add ../ledgerflow-fe -b fe     # after B1 is committed
  code ../ledgerflow-fe                        # second VS Code window = Session B
  ```
  Session A (main folder) runs B1 → B2 → B3 → B4 → B5. Session B runs D1, then F1 → F2 → F3. After each Session B phase, in Session A: `merge branch fe and resolve conflicts, then run make check`.
  D1 can start in Session B at the same time as B1: tell it "work only in backend/scripts, data/ and backend/tests/llm_fixtures".

### Kick-off prompts

**Session A, first message:**
```
Read CLAUDE.md, docs/SPEC.md and docs/PHASES.md. Summarise the architecture in 8 lines and list anything ambiguous you would decide differently. Don't write code yet.
```
then `/clear` and `/run-phase B1`.

**Session B, first message:** `/run-phase D1`, then for each frontend phase `/clear` and `/run-phase F1` (F2, F3).

**When a phase finishes**, read its report. Then, if the UI matters for that phase:
```
Use the ui-critic subagent on the new screenshots in frontend/screenshots and apply the top 5 fixes. Re-screenshot.
```

**To add or change an agent later** (this shows off configurability in the interview):
```
/add-agent  -- add a "fx_check" agent after normalise that flags invoices whose currency differs from the vendor's usual currency
```

## 3. Keep the GPT-5.2 bill small

- Tests use FakeLLM (`make test`), which costs nothing. Only `make test-live` and real runs call Azure.
- While building the UI, set `features.llm_cache: true` in `config/app.yaml`: repeated runs of the same fixture replay instantly from the cache. **Set it to false before the demo.**
- Reasoning effort is `low` everywhere except extraction and the reconciler (`medium`). Change it in `config/agents/*.yaml`.
- Fill in `llm.pricing_per_1m_usd` in app.yaml so the dashboard shows the real cost per invoice (a good talking point).

## 4. Deploy (phase B5, about 45 min)

1. Push to GitHub.
2. **Render:** New → Blueprint → select the repo (it reads `render.yaml`). Fill in the secret env vars: the 3 Azure values, `DATABASE_URL` (the Supabase *Session pooler* string, with `postgresql+psycopg://` in front), `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `CORS_ORIGINS=https://<your-app>.vercel.app`, `PUBLIC_APP_URL=https://<your-app>.vercel.app`.
3. Seed Supabase from your laptop: `DATABASE_URL="<pooler url>" STORAGE_BACKEND=supabase ... make seed`.
4. **Vercel:** import the repo, root directory `frontend`, framework Vite, env `VITE_API_URL=https://ledgerflow-api.onrender.com`.
5. **cron-job.org:** GET `https://ledgerflow-api.onrender.com/healthz` every 10 minutes (Render free sleeps after 15 idle minutes and takes about a minute to wake).
6. In Claude Code: `/live-check https://ledgerflow-api.onrender.com`. Every scenario should match.

Free-tier caveats to know (and mention if asked): Render free has 512 MB RAM and sleeps when idle; Supabase free pauses after a week of inactivity (unpause it the day before the interview); Render's own free Postgres expires after 30 days, which is why the database is on Supabase.

## 5. Before the interview

`docs/DEMO_SCRIPT.md` is the 5-minute run order. Rehearse it twice on the deployed URL. `docs/DECISIONS.md` holds the assumptions to state out loud.
