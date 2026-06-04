# Genset Call Analyzer — Docs Index

**What this folder is:** the shared **KALA AI Platform** reference docs, plus this
page mapping them to *this* app. The eight platform docs describe conventions for
the whole platform (multiple apps, the federated shell, MongoDB, Windows/IIS
deployment). This app — **Genset Call Analyzer** — follows the conventions that
apply to a standalone service and ignores the federation-only parts.

Read this page first; it tells you which doc matters for what, and where this app
deviates from the generic platform defaults.

---

## This app at a glance

| Field | Value |
|---|---|
| App name | Genset Call Analyzer |
| Purpose | AI voice agent (Shivangi) that captures genset enquiries on inbound/outbound phone calls |
| Backend | FastAPI + uvicorn — `backend/main.py` |
| Frontend | Vite + React, **Module Federation remote** (`name: 'call-center'`) — `frontend/` |
| Agent pipeline | Pipecat 1.1 (`backend/agent/bot.py`) |
| STT | Sarvam Saarika v2.5 (streaming) |
| TTS | Sarvam Bulbul v2 (`manisha` / `anushka` voices) |
| LLM | Anthropic Claude Haiku 4.5 (direct API; OpenRouter fallback) |
| Telephony | Plivo (Media Streams over WebSocket) |
| Datastore | **SQL Server** (`ERPAI` read; `call_analyzer.dbo.AICallSummary` write) — *not* MongoDB |
| Backend port | 8006 |
| Frontend port | 5178 (Vite dev, standalone) — federation deploy lives behind IIS at `/cdn/call-center/latest/` |
| Public exposure | ngrok tunnel today → migrating to a server/domain |
| Health endpoints | `GET /api/health` (liveness) ✅ implemented · `/api/ready` (readiness) ⚠️ not yet — see gaps below |

---

## Doc applicability for this app

| Doc | Applies now? | Notes for Genset Call Analyzer |
|---|---|---|
| [`backend-standardizations.md`](./backend-standardizations.md) | ✅ **Yes** | HTTP conventions, health/ready, log format, env-var rules, port table (this app = FE 5178 / BE 8006). Swap "MongoDB" → "SQL Server" in the readiness check. |
| [`version-standardization.md`](./version-standardization.md) | ✅ **Yes** | Tier-B backend pinning applies (pin `requirements.txt` with `==`). Tier 1 React/RR now pinned exactly in `frontend/package.json`; Tier 2 Vite + federation plugin pinned to platform versions. |
| [`first_deploy.md`](./first_deploy.md) | ◐ Partial | Bootstrap shape applies; the **MongoDB Atlas** prereq is **N/A** (this app uses SQL Server). Federated-remote IIS bootstrap section DOES apply now — set CORS (only if shell origin differs from `ai.kalapms.com`) and configure IIS to serve `D:\inetpub\cdn\call-center\latest\` at URL `/cdn/call-center/latest/` per federation-cookbook §8. |
| [`deployment-runbook.md`](./deployment-runbook.md) | ◐ Partial | Ongoing-ops, service-restart, troubleshooting patterns apply. Federation/CDN sections now relevant too. |
| [`github-actions-integration.md`](./github-actions-integration.md) | ◐ Draft | CI workflows at [`.github/workflows/`](../.github/workflows/), **adapted** for this app: backend runs from source as a WinSW service (NOT PyInstaller — torch/pipecat won't bundle), frontend deploys as a federation remote (Tier-1 pin verified in CI, dist/assets/remoteEntry.js checked post-build). Server-specific env values + one-time WinSW/IIS setup still required before first run. |
| [`auth-contract.md`](./auth-contract.md) | ◐ Partial | URL-param handoff reader wired in `App.jsx` (`?email=&autoLogin=true` → `AuthContext`). **Blocker:** `call-center` is not yet in the canonical AppId enum (`§3`) — Platform Architecture must register it before SSO works end-to-end. Until then `user` stays null and nothing in the app gates on it. |
| [`federation-cookbook.md`](./federation-cookbook.md) | ✅ **Yes** | Frontend is a Module Federation remote (`name: 'call-center'`, exposes `./App`, base `/cdn/call-center/latest/`). Shell registration: pin `VITE_CALL_CENTER_URL=https://ai.kalapms.com/cdn/call-center/latest/assets/remoteEntry.js` in the shell's `.env`. Deploy layout puts past `v<sha>/` snapshots beside `latest/` for rollback; see `.github/workflows/deploy-frontend.yml` header. |
| [`app-dev-guide.md`](./app-dev-guide.md) | ◐ Reference | Good background; written for federated apps. Skip the federation/shell-auth steps for this app. |

Legend: ✅ applies · ◐ partially applies · ❌ not applicable today.

---

## Deviations from the platform defaults (intentional)

This app departs from the generic platform in a few documented ways. These are
deliberate, not gaps to "fix":

1. **SQL Server, not MongoDB.** Readiness (`/api/ready`) pings SQL Server, and
   leads persist to `call_analyzer.dbo.AICallSummary`. Anywhere the platform docs
   say "MongoDB ping," read it as "SQL Server ping."
2. **Federation remote, shell registration pending.** Frontend is wired as a
   Module Federation remote (`name: 'call-center'`, exposes `./App`, Tier-1
   pinned) and the deploy emits `dist/assets/remoteEntry.js`. The shell hasn't
   added `VITE_CALL_CENTER_URL` or a route for it yet, so end users still
   reach the app standalone via its IIS site root. Removes the previous
   "standalone, not federated" deviation.
3. **Shell SSO scaffolded, not yet active.** `App.jsx` reads
   `?email=&autoLogin=true` URL params into an `AuthContext` per
   [`auth-contract.md`](./auth-contract.md) and
   [`federation-cookbook.md`](./federation-cookbook.md) §4.5.1, but the
   `call-center` AppId isn't in the canonical enum yet, no app code gates
   on `user`, and there's no login screen. JWT validation on the backend is
   still not implemented.
4. **Public exposure via ngrok (interim).** Plivo webhooks reach the local
   backend through an ngrok tunnel (`SERVER_BASE_URL` in `.env`). Production will
   replace this with a server + domain; until then, ngrok must be running for
   live calls. See the project [`README.md`](../README.md).
5. **Extra third-party dependencies** the generic backend docs don't mention:
   **Sarvam** (STT+TTS), **Anthropic** (LLM), **Plivo** (telephony), optional
   **Deepgram** (STT fallback). Each needs its own API key in `.env`.

---

## Environment variables (this app)

Defined in `backend/.env` (gitignored — never commit). Required unless noted:

| Var | Purpose |
|---|---|
| `SARVAM_API_KEY` | Sarvam STT (Saarika) + TTS (Bulbul) |
| `ANTHROPIC_API_KEY` | Claude Haiku 4.5 (LLM) — preferred |
| `OPENROUTER_API_KEY` | LLM fallback when Anthropic key absent; also the upload-analyzer path |
| `DEEPGRAM_API_KEY` | Optional — alternate streaming STT (kept for failover trials) |
| `PLIVO_AUTH_ID` / `PLIVO_AUTH_TOKEN` | Plivo telephony (REST + recording) |
| `SERVER_BASE_URL` | Public base URL Plivo webhooks hit (ngrok domain today) |
| `AGENT_MAPPING` | DID → salesperson routing for IVR option 1 |
| `ERP_SQL_SERVER` / `ERP_SQL_DATABASE` / `ERP_SQL_USERNAME` / `ERP_SQL_PASSWORD` / `ERP_SQL_DRIVER` | SQL Server connection (ERP enquiries read) |
| `ERP_SUMMARY_DATABASE` | Separate DB for AI call summaries (writes) |
| `PLIVO_CALL_LANGUAGE` | Default transcription language (`mr-IN`) |
| `DEV_SKIP_IVR` | Dev-only: bypass IVR menu (leave empty in production) |

A sanitised `.env.example` should mirror this list with placeholder values.

---

## Known gaps to close before production

Tracked here so they're not forgotten — these are where the app doesn't yet meet
the platform standards:

1. **JWT / session auth (§4)** — backend currently accepts every request without
   identifying the user. Wiring needs the platform-level `JWT_SECRET` + `USER_URI`
   Mongo collection + registration of `call-center` in the AppId enum
   ([`auth-contract.md`](./auth-contract.md) §3). Until those land there are no
   protected endpoints to gate, so this is scaffolding-pending, not a regression.
2. **Standard error envelope (§5.2) is production-only.** `main.py` installs the
   `{error:{type,message,…}}` handlers only when `APP_ENV=production` because the
   frontend currently parses `data.detail`. Migrate the frontend to read either
   shape, then drop the gate so dev matches prod.
3. **Most success responses don't use the `{data:…}` wrapper (§5.1).** Doc allows
   existing services to defer this until a major version bump — fine for now.
4. **Pin `requirements.txt` exactly** (`==`) per Tier-B — `pipecat-ai`, `plivo`,
   `anthropic`, `imageio-ffmpeg`, `pyodbc`, `loguru` are still `>=`. Pin once
   `pip freeze` reflects a known-good resolved set.
4. **Replace ngrok with a server + domain** — set a stable `SERVER_BASE_URL`; update
   the Plivo Application Answer URL once.
5. **Run backend as a managed service** (systemd / WinSW) instead of `python main.py`,
   so it survives reboots and restarts on crash. The CI workflow in
   [`.github/workflows/deploy-backend.yml`](../.github/workflows/deploy-backend.yml)
   assumes a WinSW service named `KalaGensetBackend` — that service must be
   created once on the server (see `first_deploy.md` + `deployment-runbook.md`).
6. **Fill in the CI workflow env values** — `DEPLOY_PATH`, `SITE_ROOT`, the
   self-hosted runner labels, and IIS `/api` reverse-proxy — then confirm the
   server matches the KALA Windows model the workflows assume.

---

## Quick links

- Project overview & local run: [`../README.md`](../README.md)
- Backend entrypoint: [`../backend/main.py`](../backend/main.py)
- Agent pipeline: [`../backend/agent/bot.py`](../backend/agent/bot.py)
- System prompt: [`../backend/agent/prompt.py`](../backend/agent/prompt.py)

*Last updated: 2026-05-27.*
