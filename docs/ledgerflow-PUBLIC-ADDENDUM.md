# LedgerFlow Public — Addendum to SPEC.md

> This file **extends** `docs/SPEC.md` and `docs/PHASES.md`. Everything there still holds unless a section below overrides it.
> Goal: a public, multi-user replica for a portfolio/LinkedIn showcase. Anyone signs in with Google. If they bring their own GPT-5.2 key, the full pipeline runs live on their key. If not, they get **Demo mode**: the scenario launcher replays recorded GPT-5.2 runs through the real orchestrator, rules and UI.

## 0. What changes in one paragraph

Google sign-in (Supabase Auth) replaces the role switcher. Every row gets an `owner_id`, so each user has a private workspace seeded with the demo vendors and POs. Right after first sign-in, an onboarding step asks: *"Do you have a GPT-5.2 API key?"* **Yes** → the user enters it, the server tests it, encrypts it with AES-256-GCM and stores only ciphertext; the key is never sent back to the browser. **No** → Demo mode: the scenario launcher works, custom uploads are locked, and the LLM adapter swaps to a `ReplayLLM` that serves recorded outputs. Users can switch modes, replace or delete their key, and delete their whole account at any time.

## 1. Branding and IP (do this first)

- Rename everywhere: product name in `config/app.yaml`, UI header, README. No Zamp logo, name, or wording from the candidate guide in the public repo, the UI or the video.
- Don't commit the case-study PDF. Describe the problem in your own words ("AP invoice-to-decision engine").
- Wait until the hiring process concludes before posting, or ask your recruiter whether they mind.

## 2. Hard constraints (additions)

| Constraint | Consequence |
| --- | --- |
| Still free hosting | Supabase Auth (free, 50k MAU) for Google sign-in; no new paid service |
| You pay nothing for other people's runs | Live LLM calls only ever use the **signed-in user's own key**. Your Azure key is used only for recording demo fixtures, never at runtime |
| Keys are secrets | Encrypted at rest, never logged, never returned, never in `config_snapshot`, never in `stage_events` |
| Public internet | Per-user quotas, upload limits, endpoint allow-list (SSRF), account deletion |

## 3. Authentication — Google via Supabase Auth

**Setup (you):** Google Cloud Console → OAuth consent screen (External, scopes `openid email profile` only) → OAuth client (Web) with redirect URI `https://<project>.supabase.co/auth/v1/callback` → paste client ID/secret into Supabase → Authentication → Providers → Google. Add the Vercel URL and `http://localhost:5173` to Supabase → Authentication → URL configuration. Move the consent screen from **Testing** to **In production** (basic scopes need no Google review; Testing caps you at 100 named test users).

**Frontend:** `@supabase/supabase-js` → `supabase.auth.signInWithOAuth({ provider: 'google' })`. Store nothing yourself; the SDK handles the session. Every API call sends `Authorization: Bearer <access_token>`.

**Backend:** `app/auth.py` → FastAPI dependency `current_user()` that verifies the Supabase JWT (asymmetric signing keys via the project's JWKS endpoint; fall back to the legacy HS256 JWT secret if the project still uses it), checks `exp` and `aud = "authenticated"`, and upserts a `users` row on first sight. Every `/api/*` route depends on it except `/healthz` and `/portal/{token}`.

**SSE gotcha:** browser `EventSource` can't send an Authorization header. Use `POST /api/cases/{id}/stream-token` → a 60-second signed single-use token → `GET /api/cases/{id}/events/stream?t=<token>`. (Alternative: `@microsoft/fetch-event-source` with the header.) Never put the long-lived access token in a URL.

**Role switcher:** removed as auth. Each user is Submitter + Reviewer + Admin of their own workspace. Keep the switcher only as a *view* toggle (it changes navigation, not permissions).

## 4. Multi-tenancy

- New table `users` (id = Supabase user id, email, name, avatar_url, mode: `byok|demo|unset`, created_at, last_seen_at, deleted_at).
- Add `owner_id` (FK users, indexed, NOT NULL) to: vendors, vendor_requests, purchase_orders, po_lines, grns, gl_accounts, cases, documents, extractions, po_consumption, rule_results, stage_events, template_versions, review_actions, export_batches, notifications, vendor_memory, config_versions. `llm_cache` is **not** shared across users (key includes owner_id) or is disabled in prod.
- Repository layer: one helper `scoped(query, user)` used by every read and write. A test asserts that user B gets 404 (not 403) for user A's case, document, export and template.
- Invoice refs and export refs are per-owner sequences (`INV-2026-0001` restarts per user).
- Storage keys prefixed `u/<owner_id>/...`; download endpoints check ownership before streaming.
- **Workspace seeding:** on first sign-in, copy `data/master/*.csv` into the user's workspace (`seed_workspace(user_id)`, idempotent). A "Reset demo data" button wipes the workspace and reseeds, so split-PO balances start fresh for every visitor.
- Defense in depth (optional): enable Postgres RLS on the tables; the backend connects with a role that still filters by owner_id in code. Code filtering is the primary control.

## 5. Onboarding — the upfront question

Route `/onboarding`, shown when `users.mode = unset`, before any other screen.

Card 1: **"Do you have a GPT-5.2 API key?"**
- **Yes, use my key** → Card 2.
- **No, explore the demo** → set mode `demo`, land on the Scenario launcher with a short tour.

Card 2 (key form):
- Provider radio: **OpenAI** (fields: API key, model, default `gpt-5.2`) or **Azure OpenAI** (fields: endpoint, API key, deployment name).
- Key input is `type="password"`, `autocomplete="off"`, a reveal toggle, and is cleared from React state immediately after submit.
- Copy under the form, plainly: *"Your key is encrypted before it's stored and is only decrypted on the server for the moment a call is made. It's never shown again — we keep only the last 4 characters so you can recognise it. Calls are billed to your account. You can delete it any time in Settings."*
- **Test & save** → `POST /api/me/llm-key`. The server runs the smoke check (one tiny structured-output call plus one image call) with the submitted key **before** storing. On failure, return a readable reason (401 bad key, 404 deployment not found, model doesn't accept images, Responses API unavailable → chat fallback chosen) and store nothing.
- On success: mode `byok`, show `sk-…a1B9 · Azure · gpt-5.2 · tested just now`.

Settings → **API key**: replace, re-test, delete (delete = hard-delete the row and switch to demo). Settings → **Delete account**: deletes all rows with the owner_id, all storage objects under `u/<id>/`, the key, then the Supabase auth user (admin API).

## 6. Key storage — encryption design

**Scheme:** AES-256-GCM (Python `cryptography`, `AESGCM`) with envelope-style versioning.

- Master key `KEY_ENCRYPTION_KEY` (32 random bytes, base64) lives only in Render env vars. Generate with `python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"`. Never in the repo, never in Supabase.
- Table `llm_credentials` (id, owner_id UNIQUE, provider: `openai|azure`, endpoint, deployment_or_model, key_ciphertext BYTEA, nonce BYTEA (12 random bytes per encryption), key_last4, kek_version INT, llm_path: `responses|chat`, tested_at, created_at, updated_at).
- Associated data = `f"{owner_id}|{provider}|{kek_version}"`, so a ciphertext copied into another user's row fails to decrypt.
- `app/crypto.py`: `encrypt(plaintext, owner_id, provider) -> (ct, nonce, version)` and `decrypt(row) -> str`. Plaintext lives only inside the per-request LLM client and is dropped when the run ends; never stored on a module-level object.
- **Rotation:** support `KEY_ENCRYPTION_KEY_V<n>` env vars; `scripts/rotate_kek.py` re-encrypts every row to the newest version.
- **What this protects and what it doesn't** (say this honestly in the README): a leaked database or backup is useless without the env var. It is *not* zero-knowledge; the server must decrypt the key to call the model. That's the same trust model as any hosted bring-your-own-key tool. Supabase Vault is an acceptable alternative if you prefer the key material inside Postgres.

**Redaction rules (tests enforce them):**
- A logging filter masks anything matching `sk-[A-Za-z0-9_-]{10,}` and 32+ char hex/base64 tokens.
- Exceptions from the OpenAI SDK are re-raised as `LLMError(code, safe_message)`; the raw request is never logged.
- `GET /api/me` returns `{provider, deployment_or_model, key_last4, tested_at, mode}` only. No endpoint ever returns ciphertext or plaintext.
- A test greps stage_events, config_snapshot, logs and every API response for the plaintext test key and must find nothing.

**SSRF guard (Azure endpoints are user-supplied):** accept only `https://` hosts ending in `.openai.azure.com`, `.cognitiveservices.azure.com` or `.services.ai.azure.com`; reject IP literals, ports and redirects. OpenAI provider always uses the fixed `https://api.openai.com/v1`.

## 7. LLM adapter changes (`backend/app/llm.py`)

The adapter becomes **per-run**, not global:

```python
def llm_for(user, case) -> LLM:
    if user.mode == "byok":
        cred = load_and_decrypt(user.id)        # in memory only
        return OpenAILLM(provider=cred.provider, endpoint=cred.endpoint,
                         model=cred.deployment_or_model, api_key=cred.key,
                         path=cred.llm_path)
    if case.source == "fixture":
        return ReplayLLM(scenario=case.scenario_id)
    raise DemoModeLocked()                       # custom upload without a key
```

- `OpenAILLM` is the existing Azure client generalised: OpenAI uses `base_url="https://api.openai.com/v1"`; Azure keeps `f"{endpoint}/openai/v1/"`. Same Responses-first, chat-fallback logic, same "never send temperature/top_p/max_tokens".
- Cost metrics still use `llm.pricing_per_1m` from `app.yaml`, labelled "billed to your key".
- The orchestrator receives the LLM via `AgentContext`; agents never construct a client.

## 8. Demo mode — `ReplayLLM`

- **Record real runs, don't hand-write them.** `scripts/record_fixtures.py` runs every scenario against *your* GPT-5.2 key once and saves each call's parsed output **plus real metrics** (tokens, cost, latency) to `data/recordings/<scenario>/<agent>__<action>__<n>.json`. Commit these. They also replace hand-written FakeLLM fixtures for tests.
- `ReplayLLM` looks up the recording by (scenario, agent, action, call index), sleeps for the recorded latency (capped at 4 s, so a demo run feels live but stays snappy), and returns the recorded metrics with `replayed: true`.
- Everything downstream is **real**: normaliser, vendor matching, PO scoring, consumption ledger, all rules, decision precedence, template rendering, exports, audit pack. Only perception is replayed. Say exactly this in the UI.
- **Labelling:** a persistent violet pill in the header, *"Demo mode · replaying recorded GPT-5.2 outputs"*, and a "replayed" chip on each LLM stage card in the live run view. Honest labelling matters more on LinkedIn than polish.
- **Reconciler in demo mode:** remarks are free text, so only recorded remarks can replay. In the review workspace show suggested-remark chips (for S02b: *"PO was amended to $10,500, accept the premium support line and code it to 6410"*). Typed text that doesn't match a recording (normalised whitespace/case) shows: *"Custom remarks need your own key — add one in Settings."* Inline template edits (`set_field` ops) work without an LLM, so keep them enabled.
- **Locked in demo mode:** Submit page dropzone (shows a lock card with "Add your key to process your own invoices"), Agent Studio *Test agent* and prompt edits that would need a live call. Rules table edits and the what-if simulator still work, since they're pure code.
- **Vendor portal (S05):** works in demo; uploads of the fixture W-9 and bank letter replay their classification.

## 9. Abuse limits (config in `app.yaml: limits`)

| Limit | Demo | BYOK |
| --- | --- | --- |
| Scenario runs per day | 40 | 200 |
| Custom uploads per day | 0 | 50 |
| Max file size / pages | — | 10 MB / 8 pages |
| Concurrent runs per user | 1 | 2 |
| Global concurrency (Render 512 MB) | 2 | 2 |

Implement as a `usage_counters` table (owner_id, day, kind, count). Over-limit returns 429 with a friendly reset time. Add a global kill switch `features.public_signups` in `app.yaml`.

## 10. API additions

```
GET    /api/me                          -> profile + mode + key metadata (never the key)
POST   /api/me/mode        {mode}        -> demo | byok (byok requires a stored key)
POST   /api/me/llm-key     {provider, endpoint?, model, api_key}  -> tests, encrypts, stores
POST   /api/me/llm-key/test              -> re-runs the smoke check with the stored key
DELETE /api/me/llm-key
POST   /api/me/reset-workspace           -> wipe + reseed demo data
DELETE /api/me                           -> delete account and all data
POST   /api/cases/{id}/stream-token      -> short-lived SSE token
```
All existing routes now require auth and are owner-scoped.

## 11. UI additions

1. **Landing page** (public, `/`): one-line pitch, a 20-second looping GIF of the live run view, "Sign in with Google", and a link to the GitHub repo. This is what LinkedIn visitors see first.
2. **Onboarding** (§5).
3. **Settings**: profile, mode switch, API key card, reset demo data, delete account, privacy note.
4. Header: avatar menu (Settings, Sign out), mode pill.
5. Scenario launcher becomes the demo-mode home, with each card showing the expected outcome and one sentence on what it proves.

## 12. Tests to add

- Auth: missing/expired/forged JWT → 401; `/portal/{token}` stays public.
- Tenancy: two users, cross-access to every resource type → 404.
- Crypto: round-trip; tampered ciphertext fails; ciphertext moved to another owner fails; KEK rotation.
- Secrecy: plaintext test key appears nowhere in logs, events, snapshots or responses.
- SSRF: rejected endpoints (IP literal, `http://`, foreign host, port).
- Demo: every scenario passes `expect` through `ReplayLLM`; custom upload in demo → 403 with lock message; unrecorded remark → clear message.
- Limits: 41st demo run in a day → 429.

## 13. Out of scope

Teams/shared workspaces, paid plans, storing keys client-side only, providers other than OpenAI and Azure OpenAI (the adapter boundary makes Gemini a later add-on), and email delivery.

---

# Build phases (replica)

Fork or copy the working repo into a new one (`ledgerflow-public`), fresh Supabase project, fresh Render and Vercel services. Same `/clear` → `/run-phase <id>` rhythm, each phase ending with a runnable check.

| Phase | Goal | Done when |
| --- | --- | --- |
| **R0 (you)** | Google OAuth client, Supabase Auth provider, `KEY_ENCRYPTION_KEY` generated, rebrand | Sign-in works from a bare Supabase test page |
| **R1** Auth + tenancy | `auth.py`, `users` table, owner_id on every table, `scoped()` helper, per-user storage prefix, workspace seeding, SSE stream tokens | Auth and tenancy tests pass; two Google accounts each see only their own S01 run |
| **R2** Key vault + onboarding | `crypto.py`, `llm_credentials`, `/api/me/*`, smoke-test-before-save, SSRF guard, redaction filter, onboarding + Settings screens | Crypto, secrecy and SSRF tests pass; entering a real key runs S01 live; deleting it falls back to demo |
| **R3** Demo mode | `record_fixtures.py` run once on your key, `ReplayLLM`, `llm_for()`, locks, labels, suggested-remark chips | Every scenario passes `expect` in demo mode; S02b remark loop completes via the chip; upload is locked with a clear message |
| **R4** Limits + landing + deploy | usage counters, kill switch, landing page, deploy, keep-warm ping on `/healthz` (which also touches the DB so Supabase doesn't pause) | A fresh Google account goes landing → onboarding → demo → S01 in under a minute on the public URL |
| **R5** Showcase | README with architecture diagram, security section (§6 honest trust model), 60–90 s LinkedIn video: sign in → demo S02b remark loop → S04 duplicate scan → Settings showing the key as `…a1B9` | Posted |

Cut line: if short on time, ship R1 + R3 + R4 first (demo-only public site) and add R2 (BYOK) a day later. A working demo-only link beats a half-built key vault.
