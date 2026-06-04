# KALA AI Platform — New App Developer Guide

**Audience:** Engineers about to start a new app on the KALA platform — this is your day-one reference. Read this **before** writing the first line of code.

**Status:** Active starter guide.

**Owner:** Platform Architecture team. Open a thread before deviating.

**Companion docs:** This guide stitches together the platform's reference docs in the order you'll need them. Read this first; dive into the others when each step references them.

| When you need it | Reference |
|---|---|
| Library versions to pin in `package.json` / `requirements.txt` | [`version-standardization.md`](./version-standardization.md) |
| How to expose your frontend as a federation remote | [`federation-cookbook.md`](./federation-cookbook.md) |
| HTTP contracts your backend must follow (health, auth, errors, logging) | [`backend-standardizations.md`](./backend-standardizations.md) |
| Auth handoff from shell to your app + JWT spec | [`auth-contract.md`](./auth-contract.md) |
| Deployment, IIS, WinSW, troubleshooting | [`deployment-runbook.md`](./deployment-runbook.md) |

---

## 1. Purpose

You're starting a new app. The KALA platform is a federation of independent apps stitched together by a single shell, with a single user identity, consistent backend conventions, and shared deployment patterns.

If you build *with* the platform, your app inherits all of that infrastructure for free — login, navigation, deployment, observability. If you build *against* it, you'll spend weeks re-implementing things that already exist.

This guide is the path of least resistance: follow it and you'll be productive in your first week and integrated by the second.

---

## 2. Should this be a new app at all?

Before scaffolding a fresh repo, sanity-check whether your work belongs in an existing app:

| Situation | Recommendation |
|---|---|
| You're adding a feature that's a clear sub-feature of an existing product (e.g., a new chart type for Corelytics) | Add to the existing app's repo. |
| You're building a self-contained product with its own identity, users, value proposition (e.g., a new ML service, a new vertical tool) | New app — proceed with this guide. |
| You're prototyping an idea that may or may not survive | New repo, but mark `Status: Experimental` everywhere. Don't add to the platform until validated. |
| You're replacing functionality in an existing app | Inside the existing repo, behind a feature flag. New app only after the old code is fully retired. |

If unsure, post in `#kala-platform` before starting. A new app is a permanent commitment — once it's in the shell and users find it, killing it is hard.

---

## 3. Pre-flight checklist

Decide these **before** writing code. They're hard to change later.

### 3.1 Choose your **App ID**

A short, kebab-case name used everywhere — URL path, federation key, Windows service name, env vars, log fields, JWT claim. Pattern: lowercase, hyphenated, 1–3 words.

Existing IDs (don't reuse): `corelytics`, `kala-coach`, `nexora`, `kala-interviewer`.

Good new IDs: `kala-finance`, `kala-recruit`, `audit-bot`.
Bad new IDs: `KalaFinanceTool`, `tool_2`, `app-V2-final-final`.

Add yours to the canonical list in [`auth-contract.md`](./auth-contract.md#3-the-user-object--canonical-shape) §3 in the same PR that bootstraps the repo.

### 3.2 Choose your **ports**

Pick the next free slots from the table in [`backend-standardizations.md`](./backend-standardizations.md#8-port-assignments) §8. Add yours to that table.

Convention: a 4-digit pair, frontend usually `517X`, backend `5XXX` or `8XXX`.

### 3.3 Choose your **Python framework**

Default: **Flask**. Most platform tooling, scripts, and team knowledge target Flask.

Use **FastAPI** only when you have a real reason: heavy async, SSE streaming, OpenAPI auto-generation as a deliverable. Document the reason in your repo's `README.md`.

### 3.4 Decide on **storage**

- Auth: shared MongoDB collection via `USER_URI` env var (mandatory — you don't make your own user store)
- App data: own MongoDB database via `MONGODB_URI` (can be the same cluster, different DB name)
- Caching / sessions: in-process is fine for single-instance services; use Redis if multi-instance

### 3.5 Allocate a **GitHub / Azure repo**

One repo per app, named `kala-<app-id>` (e.g., `kala-recruit`). Frontend and backend live as siblings inside:

```
kala-<app-id>/
├── frontend/
└── backend/
```

Don't put two apps in one repo. Federation requires per-app independence; one repo per app keeps deploy pipelines, permissions, and team ownership clean.

### 3.6 Confirm with the platform team

Drop a short note in `#kala-platform`:

> Bootstrapping new app: `kala-foo`. Frontend port 5178, backend port 8002. Backend: Flask. Purpose: <one-line>. Repo: <link>. Will follow `app-dev-guide.md`.

This catches port collisions and naming conflicts before you waste time.

---

## 4. Tech stack — what to install

Pin **exactly** these versions. See [`version-standardization.md`](./version-standardization.md) for the full tier system; this is the minimum for a new app.

### 4.1 Frontend

```json
{
  "dependencies": {
    "react":            "18.2.0",
    "react-dom":        "18.2.0",
    "react-router-dom": "6.22.0"
  },
  "devDependencies": {
    "vite":                              "5.1.4",
    "@vitejs/plugin-react":              "4.2.1",
    "@originjs/vite-plugin-federation":  "1.3.6"
  }
}
```

Plus an `.npmrc`:

```
save-exact=true
```

Add app-specific deps as you need them, but stay aligned on the libraries listed in version-standardization.md §3.3 ("Tier 3 — recommended").

### 4.2 Backend

Copy this `requirements.txt` as the floor:

```
flask==3.0.2
flask-cors==4.0.0
requests==2.31.0
python-dotenv==1.0.1
gunicorn==21.2.0; sys_platform != "win32"
gevent==24.11.1; sys_platform != "win32"
waitress==3.0.2; sys_platform == "win32"
pymongo[srv]==4.7.3
dnspython==2.6.1
certifi==2024.2.2
bcrypt==4.2.1
PyJWT==2.8.0
```

For FastAPI services, swap Flask for:

```
fastapi==0.110.0
uvicorn[standard]==0.27.1
```

Add app-specific libs (pandas, ML, etc.) below this baseline.

---

## 5. Bootstrap the frontend

### 5.1 Scaffold

```bash
cd kala-<app-id>
npm create vite@latest frontend -- --template react
cd frontend
echo "save-exact=true" > .npmrc
npm install react@18.2.0 react-dom@18.2.0 react-router-dom@6.22.0
npm install --save-dev vite@5.1.4 @vitejs/plugin-react@4.2.1 @originjs/vite-plugin-federation@1.3.6
```

### 5.2 Configure `vite.config.js`

```js
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

export default defineConfig(({ mode, command }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendUrl = env.VITE_BACKEND_URL || 'http://localhost:<your-backend-port>'

  return {
    // Match the URL prefix this app will be served under in production.
    // CDN at https://cdn.kala.com/<app-id>/  →  base: '/<app-id>/'
    // Subdomain (https://<app>.kala.com/)    →  base: '/'
    base: command === 'build' ? '/<app-id>/' : '/',

    plugins: [
      react(),
      federation({
        name: '<app-id>',                            // matches the shell's `remotes` key
        filename: 'remoteEntry.js',
        exposes: { './App': './src/App.jsx' },
        shared: ['react', 'react-dom', 'react-router-dom'],
      }),
    ],

    server: {
      host: '0.0.0.0',
      port: <your-frontend-port>,
      strictPort: true,
      proxy: { '/api': { target: backendUrl, changeOrigin: true } },
    },
    preview: {
      port: <your-frontend-port>,
      strictPort: true,
    },
    build: {
      target: 'esnext',
      modulePreload: false,
      cssCodeSplit: false,
    },
  }
})
```

Replace the three placeholders with your real app ID and ports.

### 5.3 Structure the app — router-less `App.jsx`

```jsx
// src/App.jsx — exposed via federation; NEVER include <BrowserRouter> here
import { Routes, Route } from 'react-router-dom'
import { AuthProvider } from './context/AuthContext'
import { ThemeProvider } from './context/ThemeContext'
import HomePage from './pages/HomePage'

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <Routes>
          <Route index element={<HomePage />} />
          {/* add more routes; the shell prefixes them with /<app-id> */}
        </Routes>
      </AuthProvider>
    </ThemeProvider>
  )
}
```

```jsx
// src/main.jsx — used only when running standalone (`npm run dev`)
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App.jsx'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
)
```

The duality is deliberate: same `App.jsx`, two ways to mount it. Standalone for dev; federated for production.

### 5.4 No login screen

Your app **must not** display a login form. The shell handles login. Your app receives the user from the shell via URL params, postMessage, or sessionStorage — see [`auth-contract.md`](./auth-contract.md#5-identity-handoff--shell-to-remote) §5 for the resolution priority and §9.2 for the React hook.

For local dev when no shell is involved, use the `VITE_DEV_USER` env-var pattern from [`auth-contract.md`](./auth-contract.md#10-standalone-dev-workflow) §10.

---

## 6. Bootstrap the backend

### 6.1 Layout

```
backend/
├── app/
│   ├── __init__.py        Flask app factory
│   ├── routes/
│   │   ├── health.py      /api/health, /api/ready
│   │   └── <feature>.py
│   └── services/
├── run.py                 entry point (loads .env, creates app)
├── requirements.txt       (pinned; bundled into the exe at build time)
├── .env.example           (committed; real .env never committed)
├── .gitignore
├── pyinstaller.spec       (optional — generated on first build)
└── winsw\
    ├── KalaCorelytics-svc.xml.example   committed template; copied to .xml on server during initial setup
    └── README.md                        notes on initial service registration
```

The `winsw\` folder holds the per-service WinSW config in source-controlled form. The actual `*-svc.xml` and `WinSW.exe` live in the deploy folder on the server (see [`deployment-runbook.md`](./deployment-runbook.md) §6) and are written **once** during initial setup — CI deploys never overwrite them.

There's no `manage.bat` and no `venv` in the repo for production purposes — the server has neither Python nor pip. CI builds the exe with PyInstaller; that artifact is what ships.

### 6.2 `run.py`

```python
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env BEFORE importing the app so config is available at import time
load_dotenv(Path(__file__).resolve().parent / '.env')

from app import create_app
app = create_app()

if __name__ == '__main__':
    # ⚠ For local laptop iteration ONLY. The production exe (built by
    # PyInstaller in CI) imports a separate `wsgi_main.py` that calls
    # waitress.serve(app, ...) directly — never this debug path.
    # See deployment-runbook.md §6.3.
    debug = os.getenv('FLASK_DEBUG', '0') == '1'
    port = int(os.getenv('PORT', '<your-backend-port>'))
    app.run(debug=debug, host='0.0.0.0', port=port, threaded=True)
```

### 6.3 `app/__init__.py`

```python
import os
from flask import Flask, jsonify
from flask_cors import CORS

def create_app():
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB

    # CORS — see backend-standardizations.md §9
    CORS(app, supports_credentials=True, origins=[
        'http://localhost:5173',
        'http://localhost:<your-frontend-port>',
        'https://app.kala.com',
        'https://staging.kala.com',
    ])

    # Health + readiness — see backend-standardizations.md §3
    @app.route('/api/health')
    def health():
        return {'status': 'ok', 'service': '<app-id>',
                'version': os.getenv('GIT_SHA', 'dev')}, 200

    @app.route('/api/ready')
    def ready():
        # … check Mongo / OpenRouter / etc. as relevant
        return {'status': 'ready', 'checks': {}}, 200

    # Register your feature blueprints here
    # from .routes.foo import foo_bp
    # app.register_blueprint(foo_bp, url_prefix='/api')

    return app
```

### 6.4 `.env.example`

```
# Standalone backend port (override only if needed)
# PORT=<your-backend-port>

# OpenRouter (omit if your app doesn't call LLMs)
OPENROUTER_API_KEY=sk-or-v1-...

# MongoDB — app data
MONGODB_URI=mongodb+srv://USER:PASS@cluster.mongodb.net/<your-db>

# MongoDB — auth (SHARED with main app)
USER_URI=mongodb+srv://USER:PASS@cluster.mongodb.net/auth

# JWT secret (SHARED across all KALA backends — coordinate with platform team)
JWT_SECRET=<from-secrets-manager>

# Logging
LOG_LEVEL=INFO

# Build identifier (set by CI)
GIT_SHA=
SERVICE_NAME=<app-id>
```

### 6.5 First-class conventions checklist

Before pushing your first PR, your backend must already conform to these from day one — they're cheaper to add upfront than retrofit:

- [ ] `/api/health` returns `{status, service, version}` (200)
- [ ] `/api/ready` returns 503 if any downstream is broken
- [ ] Errors return `{ error: { type, message, detail? } }` ([backend-standardizations.md §5.2](./backend-standardizations.md#52-error-responses))
- [ ] Successes return `{ data: ... }` (new apps only — old apps grandfathered)
- [ ] Logs are JSON Lines to stdout with the standard fields ([§6](./backend-standardizations.md#6-logging-format))
- [ ] Auth: `Authorization: Bearer` validated first, session cookie fallback ([auth-contract.md §7](./auth-contract.md#7-jwt-specification))
- [ ] 401 for missing/invalid auth, 403 for insufficient permission
- [ ] CORS allows the four canonical origins
- [ ] Every outbound HTTP call has an explicit `timeout=`
- [ ] No secrets hardcoded; everything via env vars

---

## 7. Wire auth from day one

### 7.1 Frontend

Implement the `useAuthHandshake` hook from [`auth-contract.md` §9.2](./auth-contract.md#92-remote-frontend--receiving-the-user). Drop it into `src/context/AuthContext.jsx`. From there, every component reads `useAuth()` and gets the User without a login screen.

### 7.2 Backend

Implement the `auth_required` / `app_admin_required` decorators from [`auth-contract.md` §9.3](./auth-contract.md#93-backend-flask--validating-an-incoming-request) (Flask) or §9.4 (FastAPI). Use them on every route that should not be anonymous.

### 7.3 The `appAdmin` rule

When checking if the user is admin **for your app**:

```python
# Backend: re-derive from adminScope (don't trust client-sent flags)
def is_app_admin(user, app_id='<app-id>'):
    return 'all' in (user.get('adminScope') or []) or app_id in (user.get('adminScope') or [])

# Frontend: the shell already resolved appAdmin; trust it
const canEditConfig = !!user?.appAdmin
```

This is the single most-confused part of the auth model. Re-read [`auth-contract.md` §6](./auth-contract.md#6-authorization--adminscope) once before shipping.

---

## 8. Federation wiring

When ready to integrate, follow [`federation-cookbook.md`](./federation-cookbook.md) §4 in full. Quick reference:

1. ✅ Plugin installed (you already did §5.1)
2. ✅ `vite.config.js` configured (you already did §5.2)
3. ✅ `App.jsx` router-less; `main.jsx` standalone (§5.3)
4. ✅ `base` path matches deploy URL (§5.2)
5. → Coordinate with shell team to register your remote URL

When the shell team adds your remote, send them:

> App ID: `<app-id>`
> Production URL: `https://cdn.kala.com/<app-id>/v<sha>/assets/remoteEntry.js`
> Staging URL: `https://cdn-staging.kala.com/<app-id>/latest/assets/remoteEntry.js`
> Frontend dev port: `<your-frontend-port>`

They'll add a slot to the shell's `.env.production` / `.env.staging`.

---

## 9. Deployment plumbing — set up early

You'll thank yourself for doing this on day 1 instead of week 6.

### 9.1 Windows service (WinSW)

When your backend lands on a server, it'll run as a Windows service named `Kala<App>Backend`. The service is registered **once** at initial setup using WinSW (a single ~700KB binary that wraps any console exe as a service). See [`deployment-runbook.md` §6.4](./deployment-runbook.md#64-installing-a-new-winsw-service) for the install commands.

Each repo ships a `winsw/<App>-svc.xml.example` template for reference. The platform team registers the actual service from that template during onboarding; subsequent CI deploys only swap the exe.

### 9.2 web.config rules (frontend)

The shell's IIS `web.config` reverse-proxies traffic to backends. When your app goes live, add a rule above the generic `^api/(.*)`:

```xml
<rule name="Kala <App> API" stopProcessing="true">
  <match url="^api/<app-id>($|/.*)" />
  <action type="Rewrite" url="http://localhost:<your-backend-port>/api/<app-id>{R:1}" />
</rule>
```

(Or, if your backend mounts everything under `/api/...` without an app-id prefix, use a more specific match like `^api/<your-resource>(...)`. See examples in `dynamic_dashboard/frontend/public/web.config`.)

### 9.3 ARR proxy timeout

If your endpoints take more than 30 seconds (LLM calls, big uploads), the deployment will fail in production with `502 Bad Gateway`. Coordinate with ops to verify ARR's `proxyTimeout` is at least your worst-case backend timeout. See [`deployment-runbook.md` §7](./deployment-runbook.md#7-arr-proxy-timeout--the-famous-30-second-killer).

### 9.4 CI/CD

Copy the federation deploy template from [`federation-cookbook.md` §7.1](./federation-cookbook.md#71-github-actions-example) for the frontend. It builds your app, verifies Tier-1 versions, uploads to the CDN folder, and notifies the team.

For the backend, copy Template B from [`github-actions-integration.md` §5.2](./github-actions-integration.md#52-template-b--backend-service-flask--fastapi). The runner uses PyInstaller to build a self-contained exe in CI, then ships only that exe to the deploy server (which has no Python install). Deploy steps: `Stop-Service` → drop new exe → `Start-Service` → `/api/health` check.

---

## 10. Coordinate with the platform team

You're not building in isolation. Loop in the platform team at three checkpoints:

### Checkpoint 1 — Bootstrap (week 0)

Before pushing your first commit:

> "Starting `<app-id>`. Frontend `5178`, backend `8002`. Flask. Purpose: `<one-line>`."

Catches port collisions, naming clashes, scope-creep concerns.

### Checkpoint 2 — Auth integration (week 2-3)

Before your first PR that touches auth:

> "Wiring auth. Plan: shell delivers user via URL params + postMessage; backend validates JWT via `JWT_SECRET`. App ID for adminScope checks: `<app-id>`. Anything I should know?"

Catches "we're rotating the JWT secret next week" or "we're moving to OAuth".

### Checkpoint 3 — Production rollout (week 6+)

Before adding to the shell's production `.env`:

> "Ready for prod. Remote URL: `<url>`. Backend exe `Kala<App>.exe`, registered as WinSW service `Kala<App>Backend` on the server. CORS configured. Health endpoint live. Want to schedule a 30-min review?"

Triggers a quick architecture review and CDN/IIS setup. Don't skip this — it's how mistakes are caught before users see them.

---

## 11. Suggested milestones

Realistic timeline for a small-to-medium app, assuming familiarity with React/Flask:

| Week | Milestone | What "done" looks like |
|---|---|---|
| **0** | Bootstrap | Repo created, ports allocated, scaffolding committed, `npm run dev` and `python run.py` both work |
| **1** | Standalone MVP | One end-to-end feature works in standalone mode, no shell integration yet |
| **2** | Auth wired | Open `localhost:<port>?email=...&autoLogin=true` and the user populates AuthContext; backend rejects unauth'd calls |
| **3** | Backend conventions | `/api/health`, `/api/ready`, error shape, JSON-Lines logging — all conformant |
| **4** | Federation expose | `npm run build && npm run preview` produces a `remoteEntry.js`; shell can `import('app/App')` |
| **5** | Local integration | Run shell + your app together, verify navigation works in both directions |
| **6** | Deployment plumbing | WinSW service installed in staging from your repo's XML template, web.config rules added, ARR timeout verified |
| **7** | Production rollout | Shell team flips `.env.production`, app is live |

Slips happen. The key milestones are 2 (auth working), 3 (conventions), 6 (deployable). Everything else can compress.

---

## 12. What NOT to do

Common pitfalls that cost teams weeks. Read this list once.

| Anti-pattern | Why not | Do instead |
|---|---|---|
| Add a login form to your app | Breaks SSO; users hate logging in twice | Receive user via auth handshake (§7.1) |
| Make your own user store | Splits identity; data drift | Share `USER_URI` with platform |
| Use a different React major (17 / 19) | Federation crash, "invalid hook call" | Pin React 18.2.0 (Tier 1) |
| Hardcode `localhost:8001` in fetch calls | Won't work in prod; can't be deployed | Use relative `/api/...` and let the proxy resolve |
| Wrap App.jsx in `<BrowserRouter>` (the exposed file) | Conflicts with shell's router | Router only in `main.jsx` (standalone) |
| Compile `app.run(debug=True)` into the production exe | Triggers Werkzeug auto-reloader, pins CPU, exposes RCE console | The PyInstaller spec must point at a Waitress/Uvicorn entrypoint, never `run.py`'s `__main__` block (deployment-runbook §6.3) |
| Skip `/api/health` ("it's just a hobby project") | Ops can't monitor; deploys go silent on failure | 5 lines of code; do it day 1 |
| Return `[1, 2, 3]` directly as JSON | Frontend can't add `meta` later non-breakingly | Wrap in `{ data: [...] }` (new apps) |
| Catch every exception and return `{}` | Hides real bugs; on-call rage | Let the global error handler emit the standard error shape |
| Pin `react: ^18.2.0` (caret) | Federation drift across remotes | Pin `18.2.0` exactly + `save-exact=true` |
| Build your own auth decorator from scratch | Drifts from the contract; subtly different behavior | Copy auth-contract.md §9.3 verbatim |
| Skip `/api/ready` because "Mongo never goes down" | It does. Restart loop ensues | 10 minutes upfront, lifetime saved |

If you find yourself doing any of these, stop and re-read the relevant doc.

---

## 13. Validation checklist before "production ready"

Use this as the final gate before asking the platform team to flip your app on:

### App-level

- [ ] App ID added to canonical list in `auth-contract.md` §3
- [ ] Ports added to `backend-standardizations.md` §8
- [ ] README.md describes purpose, owner, runbook in <1 page

### Frontend

- [ ] `package.json` Tier-1 versions exact-pinned; `.npmrc` has `save-exact=true`
- [ ] `package-lock.json` committed
- [ ] `vite.config.js` has federation `expose` for `./App` + correct `base`
- [ ] `App.jsx` is router-less; `main.jsx` wraps standalone in `BrowserRouter`
- [ ] No login form anywhere
- [ ] `useAuthHandshake` populates user from URL params, postMessage, sessionStorage in priority order
- [ ] `npm run build && npm run preview` produces a working `remoteEntry.js`

### Backend

- [ ] `/api/health` and `/api/ready` working
- [ ] Errors conform to `{ error: { type, message } }` shape
- [ ] JSON-Lines logging on stdout with required fields
- [ ] Auth: JWT first, session cookie fallback, 401/403 used correctly
- [ ] CORS allows the four canonical origins + credentials
- [ ] Every outbound call has explicit `timeout=`
- [ ] `.env.example` lists every env var with sample values
- [ ] No secrets hardcoded
- [ ] Windows service name follows `Kala<App>Backend` pattern (set in `winsw/<App>-svc.xml.example`)
- [ ] Pinned `requirements.txt` exact versions
- [ ] PyInstaller build produces a working self-contained exe (`pyinstaller --onefile run.py` succeeds and the exe boots independently of any venv)

### Deployment

- [ ] CI/CD pipeline builds + uploads `remoteEntry.js` on every push to main
- [ ] CORS configured on the upload target (CDN / S3 / IIS)
- [ ] Coordinate with platform team — Checkpoint 3 (§10)
- [ ] Staging deployment tested end-to-end
- [ ] Backend `winsw/<App>-svc.xml.example` committed
- [ ] Health endpoint reachable via IIS reverse proxy in staging

### Ops

- [ ] On-call escalation contact in `README.md`
- [ ] Rollback procedure documented (or just "redeploy previous git tag")
- [ ] Logs visible to whoever's on-call

When every box is checked: you're ready.

---

## 14. FAQ

**Q. I want to use TypeScript instead of JavaScript. Allowed?**
Yes. Add `typescript`, `@types/react`, `@types/react-dom` to your devDependencies (versions per Tier 4). The platform doesn't care; build output is JS either way.

**Q. Can I use a different state management library (Zustand, Redux, etc.)?**
Yes — fully your call. Tier 4 / app-private. The platform only cares about `react` itself.

**Q. Can I use Tailwind / styled-components / emotion?**
Yes. Document your choice in your README so other engineers know what to expect.

**Q. How do I share types with the shell or other remotes?**
Publish a small npm package (e.g., `kala-shared-contracts`) to your private registry. Each repo `npm install`s it. See [`version-standardization.md` §3](./version-standardization.md#3-canonical-versions) for the strict-typing precedent.

**Q. Can I add new admin scopes?**
Adding a new app ID to `adminScope` is fine — that's how new apps are integrated. Adding new role concepts (beyond `adminScope`) requires a platform-architecture review and an `auth-contract.md` amendment.

**Q. My app needs a long-running background job. What's the pattern?**
Out of scope for this doc. Today: a separate worker process; tomorrow: a queue. Open a thread in `#kala-platform`.

**Q. Can my app have its own subdomain (`finance.kala.com`)?**
Yes, but coordinate with ops. Cross-domain requires JWT auth (no shared session cookie). The shell can still embed it via URL params, but the auth flow becomes more elaborate. Same-domain is simpler if you can swing it.

**Q. The platform docs reference dates and version history that hasn't been updated in months. Are they stale?**
Re-read this doc set quarterly. Each doc has a `Document history` section that gets amended on changes — if it's been quiet, the platform was stable; if there are recent entries, scan for relevance.

**Q. I disagree with one of the conventions in `backend-standardizations.md`. How do I propose a change?**
Open an issue in the platform repo with a concrete proposal + migration plan. Platform Architecture reviews. Reasonable changes ship; "I just don't like it" doesn't.

**Q. I'm done with this guide. What now?**
Run through §13 validation checklist. If everything's checked, ping `#kala-platform` for Checkpoint 3 (§10). You're ready to ship.

---

## 15. Document history

| Date | Change | By |
|---|---|---|
| 2026-05-04 | Initial version. Bootstraps a new KALA app from zero through production readiness. | Platform Architecture |
| 2026-05-08 | Updated §6 backend layout, §9.1 service installation, §9.4 CI/CD, §11 milestones, §12 anti-patterns, §13 validation for the WinSW + PyInstaller exe deployment model. | Platform Architecture |
