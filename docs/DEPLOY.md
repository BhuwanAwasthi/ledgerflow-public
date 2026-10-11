# Deploy (free tier): Supabase + Render + Vercel + cron-job.org

Order matters: Supabase first (the API needs a DB and a bucket), then Render (the SPA needs the API URL),
then Vercel, then go back to Render to set the CORS origin. About 20 minutes. Commands are PowerShell 5.1.

## 1. Supabase (DB + file storage)
1. New project. Pick the region closest to Render's `singapore` (in `render.yaml`), e.g. *Southeast Asia (Singapore)*.
2. Storage → New bucket → `documents`, **private**.
3. Connect (top bar) → **Session pooler** → copy the URI (`postgresql://postgres.<ref>:<pw>@aws-0-<region>.pooler.supabase.com:5432/postgres`).
   Use the session pooler (port 5432, IPv4). Render free has no IPv6, so the direct `db.<ref>.supabase.co` host fails.
4. Project Settings → API → copy the **Project URL** and the **service_role** key (server-side only, never in the SPA).

## 2. Seed Supabase from your laptop
```powershell
$env:DATABASE_URL = "<session pooler URI>"
make seed
Remove-Item Env:DATABASE_URL
```
Expect `Seeded postgresql database: vendors=7 purchase_orders=7 ... seeded_history_cases=40`. Tables are created by
the seed (and by the API on startup), so there are no migrations to run. Re-running `make seed` resets the demo data.

## 3. Render (API)
1. Dashboard → New → **Blueprint** → pick this repo. It reads `render.yaml` (free web service, `uv sync`, uvicorn on `$PORT`, health check `/healthz`).
2. Fill the `sync: false` env vars:

| Key | Value |
| --- | --- |
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT` | same as your `.env` |
| `DATABASE_URL` | the session pooler URI from step 1.3 |
| `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | from step 1.4 |
| `CORS_ORIGINS` | the Vercel URL (comma-separated if several); set a placeholder now, fix in step 5 |
| `PUBLIC_APP_URL` | the Vercel URL (portal links and mailto drafts) |

3. Deploy, then open `https://<service>.onrender.com/healthz`. Expect
   `{"ok": true, "db": "ok", "storage": "ok", "llm_path": "responses"}` (or `"chat"`). Right after boot `llm_path` is
   `not_probed` for a few seconds while the background probe picks the API path.

## 4. Vercel (SPA)
1. Add New → Project → import this repo → **Root Directory: `frontend`**. `frontend/vercel.json` sets the Vite build,
   `dist/` output and the SPA rewrite (deep links such as `/cases/12` serve `index.html`).
2. Environment variable `VITE_API_URL` = `https://<service>.onrender.com` (no trailing slash). It is baked in at build
   time, so redeploy after changing it.

## 5. Close the loop
- Render → Environment → set `CORS_ORIGINS` and `PUBLIC_APP_URL` to the Vercel production URL → save (redeploys).
  `http://localhost:5173` is always allowed in addition. Preview deployments have other URLs; add them to the list if you use them.
- cron-job.org → new job → `GET https://<service>.onrender.com/healthz` every 10 minutes (demo days only: Render free
  allows 750 instance-hours a month, and an awake service uses about 720).

## 6. Verify (B5 done-check)
```powershell
make smoke-deploy URL=https://<service>.onrender.com
```
It waits out a cold start, requires `db=ok storage=ok llm_path=responses|chat`, runs `S01_happy` on the live LLM and
checks it ends `auto_approve`. It needs a freshly seeded DB. A second S01 run is a real duplicate: it rejects on
`duplicate_exact` and PO-1002 is already consumed.

Then **reset before the demo or P6**, so that S01 runs once in `run_order` and S04 can point at it:
```powershell
$env:DATABASE_URL = "<session pooler URI>"; make seed; Remove-Item Env:DATABASE_URL
```
Then restart the Render service (Manual Deploy → Restart). Open the Vercel URL, run S01 from the scenario launcher and
watch the live run.

## Troubleshooting
- `db: error: OperationalError`: wrong pooler URI or password, or the Supabase project is paused (free projects pause after a week idle; restore it in the dashboard).
- `storage: error`: the bucket name is not `documents`, or the anon key was pasted instead of service_role.
- Browser shows CORS errors: `CORS_ORIGINS` doesn't exactly match the SPA origin (scheme, no trailing slash).
- First request takes ~1 min: Render free cold start; the SPA shows "Waking the engine…". Paused cases resume on boot.
