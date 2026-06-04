# KALA AI Platform — Deployment Runbook

**Audience:** Whoever deploys, troubleshoots, or operates KALA backends on production / staging Windows servers (IIS + WinSW + PyInstaller-built exe).

**Status:** Living document — amend after every production incident with the lesson learned.

**Owner:** Platform Architecture team + on-call rotation.

**Companion docs:**
- [`version-standardization.md`](./version-standardization.md) — pinning versions
- [`backend-standardizations.md`](./backend-standardizations.md) — HTTP-level conventions
- [`federation-cookbook.md`](./federation-cookbook.md) — frontend deploy
- [`auth-contract.md`](./auth-contract.md) — auth flow

---

## 1. Purpose

This runbook is the operational reference for the KALA platform's Windows deployment. Most of the lessons in here were learned the hard way — CPU spikes, 30-second timeouts, ECONNREFUSED at midnight, "why is it returning the old format?" — and now live in one place so the next deployer doesn't re-discover them.

**Who should read this:**
- New engineers joining KALA, before their first deploy
- On-call engineers when something is on fire
- Anyone setting up a new service that follows the platform pattern

---

## 2. Stack overview

```
                ┌────────────────────────────────────────────┐
                │        Windows Server (production)         │
                │                                            │
                │   ┌──────────────────────────────────────┐ │
                │   │  IIS  (port 80 / 443, public)        │ │
                │   │  ├─ web.config: URL Rewrite + ARR    │ │
                │   │  ├─ TLS termination                  │ │
                │   │  └─ Static-file serving (frontend)   │ │
                │   └─────┬────────────────────────────────┘ │
                │         │ reverse-proxies to localhost:N   │
                │         ▼                                  │
                │   ┌──────────────────────────────────────┐ │
                │   │  WinSW  (lightweight service wrapper)│ │
                │   │  ├─ KalaCorelyticsBackend  → :5101   │ │
                │   │  ├─ KalaNexoraBackend       → :3500  │ │
                │   │  ├─ KalaSaarthiBackend      → :5000  │ │
                │   │  └─ KalaInterviewerBackend  → :8001  │ │
                │   └─────┬────────────────────────────────┘ │
                │         │ each WinSW launches:             │
                │         ▼                                  │
                │   ┌──────────────────────────────────────┐ │
                │   │  Self-contained .exe (PyInstaller):  │ │
                │   │  • bundled Python + deps             │ │
                │   │  • bundled Waitress / Uvicorn        │ │
                │   │  • Flask or FastAPI app              │ │
                │   │  • binds 127.0.0.1:N (not public)    │ │
                │   │  • reads .env from cwd               │ │
                │   │  • writes JSON-Lines logs to stdout  │ │
                │   └──────────────────────────────────────┘ │
                └────────────────────────────────────────────┘
                              │
                              │ HTTPS
                              ▼
                  ┌──────────────────────┐
                  │  MongoDB Atlas       │
                  │  OpenRouter (LLM)    │
                  └──────────────────────┘
```

**Roles:**
- **IIS** — public-facing HTTP server. Serves static frontend files from `dist/`, reverse-proxies API calls to the backend services.
- **ARR (Application Request Routing)** — IIS module that does the actual proxying. Has its own timeouts (the famously low 30s default).
- **WinSW (Windows Service Wrapper)** — single ~700KB exe + XML config that registers any console binary as a Windows service. Auto-start on reboot, auto-restart on crash, log redirection. Replaced NSSM in 2026-05.
- **Self-contained app exe** — PyInstaller-built bundle containing the Python runtime, all pip dependencies, the WSGI/ASGI server (Waitress for Flask, Uvicorn for FastAPI), and the app code. One exe per service, bound to `localhost` only. **Server has no Python install; everything runs from the exe.**

---

## 3. Anatomy of a request

When a user clicks "Generate Dashboard" on the deployed app, here's the chain:

1. **Browser** → `POST https://app.kala.com/api/generate-dashboard-summary`
2. **DNS / firewall / TLS** termination
3. **IIS** receives the request
4. **`web.config` URL Rewrite rule** `^api/(.*)` matches → action: rewrite to `http://localhost:5101/api/$1`
5. **ARR module** opens a TCP connection to `127.0.0.1:5101` (Corelytics backend)
6. **The exe (WinSW-supervised)** has Waitress already listening — it hands the connection to the embedded Flask app
7. **Flask app** runs the route handler — e.g., `generate_dashboard_dynamic` makes 5 sequential calls to OpenRouter
8. **Response** flows back: Flask → Waitress (in exe) → ARR → IIS → Browser

Total wall-clock time: 5–300 seconds depending on whether it's an LLM-heavy endpoint. **Every layer in this chain has a timeout.** Anything that fires earlier than the slowest legitimate response kills the whole request.

---

## 4. Deployment flow

### 4.1 Frontend

1. **Build:** `cd <app>/frontend && npm run build`
2. **Output:** `dist/` containing `index.html`, `assets/<hashed>.js`, `assets/<hashed>.css`, plus `web.config`
3. **Deploy:** copy/sync `dist/` to the IIS site root (e.g., `D:\inetpub\wwwroot\kala\`)
4. **No restart needed** — IIS picks up new files immediately
5. **CDN cache:** if you're behind a CDN, invalidate the path or wait for TTL expiry

### 4.2 Backend

CI builds the exe (PyInstaller) and ships only the exe to the server. The `.env`, `WinSW.exe`, `WinSW.xml`, and `logs/` folder are never touched by deploys.

1. **Stop the service:** `Stop-Service Kala<App>Backend` (releases the exe's file handle so it can be overwritten)
2. **Drop the new exe** into `D:\deployments\<app-id>\backend\` (overwrite the old one)
3. **Start the service:** `Start-Service Kala<App>Backend`
4. **Verify:** `curl http://localhost:<port>/api/health` → expect `{ "status": "ok" }`

That's the entire backend deploy. No Python on the server, no pip, no venv, no NSSM.

**Why stop-then-replace:** Windows holds an exclusive lock on running executables. Trying to overwrite an exe while the service runs throws `Access denied`. The CI workflow handles this ordering automatically (see [`github-actions-integration.md`](./github-actions-integration.md) §5.2).

### 4.3 Combined deploy (frontend + backend together)

CI does this; manual equivalent on the server:

```powershell
# 1. Backend — drop new exe + restart
Stop-Service KalaCorelyticsBackend
Copy-Item -Path "$BUILD_OUT\KalaCorelytics.exe" `
          -Destination "D:\deployments\corelytics\backend\KalaCorelytics.exe" -Force
Start-Service KalaCorelyticsBackend
curl.exe http://localhost:5101/api/health   # verify before continuing

# 2. Frontend — robocopy dist; IIS picks up changes immediately
robocopy "$BUILD_OUT\frontend\dist" "D:\inetpub\wwwroot\kala" /MIR /XD .git
# IIS auto-detects file changes; no iisreset needed
```

---

## 5. IIS configuration

### 5.1 `web.config` — the canonical pattern

Every app's `frontend/public/web.config` (which gets copied to `dist/web.config` by Vite) contains the URL Rewrite rules. The pattern:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<configuration>
  <system.webServer>
    <httpErrors existingResponse="PassThrough" />
    <security>
      <requestFiltering>
        <requestLimits maxAllowedContentLength="52428800" />  <!-- 50 MB -->
      </requestFiltering>
    </security>
    <rewrite>
      <rules>
        <!-- 1. SPECIFIC API proxies first (most specific wins) -->
        <rule name="Nexora Chat" stopProcessing="true">
          <match url="^api/chat($|/.*)" />
          <action type="Rewrite" url="http://localhost:3500/api/chat{R:1}" />
        </rule>

        <!-- 2. Generic API proxy -->
        <rule name="API Proxy" stopProcessing="true">
          <match url="^api/(.*)" />
          <action type="Rewrite" url="http://localhost:5001/api/{R:1}" />
        </rule>

        <!-- 3. Per-app sub-bundles served as static SPAs -->
        <rule name="Nexora Pulse SPA" stopProcessing="true">
          <match url="^nexora/pulse/(.*)" />
          <conditions>
            <add input="{REQUEST_FILENAME}" matchType="IsFile" negate="true" />
            <add input="{REQUEST_FILENAME}" matchType="IsDirectory" negate="true" />
          </conditions>
          <action type="Rewrite" url="/nexora/pulse/index.html" />
        </rule>

        <!-- 4. Main React SPA fallback (catches everything else) -->
        <rule name="React SPA" stopProcessing="true">
          <match url=".*" />
          <conditions>
            <add input="{REQUEST_FILENAME}" matchType="IsFile" negate="true" />
            <add input="{REQUEST_FILENAME}" matchType="IsDirectory" negate="true" />
          </conditions>
          <action type="Rewrite" url="/index.html" />
        </rule>
      </rules>
    </rewrite>
  </system.webServer>
</configuration>
```

### 5.2 Order matters

URL Rewrite evaluates rules **top to bottom**. The first match with `stopProcessing="true"` wins. So:

- **Specific before generic:** `^api/chat($|/.*)` must come before `^api/(.*)`. Otherwise the generic rule catches it first.
- **API proxies before SPA fallback:** otherwise `/api/foo` gets rewritten to `/index.html` and the user gets HTML when they expected JSON.

### 5.3 Static-vs-rewrite resolution

The `IsFile` / `IsDirectory negate` conditions on the SPA fallback mean: only rewrite if the file doesn't exist on disk. So `/assets/index-abc123.js` — a real file — is served directly; `/dashboard` — not a real file — gets rewritten to `/index.html` so the React Router takes over.

**This is the SPA pattern.** Every KALA frontend uses it. Don't deviate.

### 5.4 Increasing upload size

Default IIS request limit is 30 MB. Generators that accept Excel uploads need more. Set:

```xml
<security>
  <requestFiltering>
    <requestLimits maxAllowedContentLength="52428800" />  <!-- 50 MB -->
  </requestFiltering>
</security>
```

If you bump above 30 MB, you may also need to bump `<httpRuntime maxRequestLength="51200" />` in a sibling `<system.web>` block — depends on your IIS config.

---

## 6. WinSW configuration

Each backend service consists of three files in its deploy folder:

```
D:\deployments\corelytics\backend\
├── KalaCorelytics.exe              ← the app (PyInstaller-built, self-contained)
├── KalaCorelytics-svc.exe          ← renamed copy of WinSW.exe
├── KalaCorelytics-svc.xml          ← service config (what to launch, where to log)
├── .env                            ← per-environment config, NOT in CI artifacts
└── logs\                           ← created by WinSW on first run
```

WinSW is one ~700KB binary that registers any console exe as a Windows service. It supersedes NSSM for this platform as of 2026-05.

### 6.1 The app exe

PyInstaller produces a single self-contained binary that bundles:

- The Python interpreter
- All `requirements.txt` dependencies (Waitress, Flask, pymongo, etc.)
- The app code
- Any data files declared in the spec

The exe reads `.env` from its current working directory at startup (via `python-dotenv`) and starts the WSGI/ASGI server. **No Python install on the server. No virtualenv. No `pip install` at deploy time.**

CI builds the exe (see [`github-actions-integration.md`](./github-actions-integration.md) §5.2). Devs reproducing locally:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt pyinstaller
.\.venv\Scripts\pyinstaller --onefile --name KalaCorelytics run.py
# Output: dist\KalaCorelytics.exe — self-contained
```

### 6.2 The WinSW XML config

Every backend has a per-service XML in this exact shape (saved as `<ServiceName>-svc.xml` next to the WinSW exe):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<service>
  <id>KalaCorelyticsBackend</id>
  <name>KALA Corelytics Backend</name>
  <description>Corelytics standalone backend, port 5101.</description>

  <!-- The exe to launch. Working directory defaults to the folder this XML
       lives in, so the app's relative .env path resolves correctly. -->
  <executable>%BASE%\KalaCorelytics.exe</executable>

  <!-- Optional: override the working directory if the layout is unusual. -->
  <!-- <workingdirectory>%BASE%</workingdirectory> -->

  <!-- Restart on crash: 5s, 10s, 30s back-off, then give up after the third. -->
  <onfailure action="restart" delay="5 sec" />
  <onfailure action="restart" delay="10 sec" />
  <onfailure action="restart" delay="30 sec" />
  <resetfailure>1 hour</resetfailure>

  <!-- Logs: roll daily, keep 14 days. -->
  <log mode="roll-by-time">
    <pattern>yyyyMMdd</pattern>
    <autoRollAtTime>00:00:00</autoRollAtTime>
    <zipOlderThanNumDays>14</zipOlderThanNumDays>
  </log>
  <logpath>%BASE%\logs</logpath>

  <!-- Run as LocalSystem (default). Override only if you need a domain account. -->
  <!-- <serviceaccount>
    <username>NT AUTHORITY\NetworkService</username>
    <allowservicelogon>true</allowservicelogon>
  </serviceaccount> -->

  <!-- Stop signal: WinSW sends Ctrl+Break by default; the app should catch it
       and shut down Waitress / Uvicorn cleanly. 30 seconds before SIGKILL. -->
  <stoptimeout>30 sec</stoptimeout>
</service>
```

`%BASE%` resolves to the folder the WinSW exe lives in — making the XML portable across environments without path edits.

### 6.3 ⚠ Don't use `python run.py` or include a dev-server fallback in the exe

The PyInstaller spec must point at a production entrypoint that calls Waitress/Uvicorn directly — never one that ends in `app.run(debug=True)`. The latter triggers Werkzeug's auto-reloader, which under a service wrapper:

1. Spawns a reloader subprocess that `stat()`-polls every Python file every second
2. Pins a CPU core
3. Crashes loops when files are touched
4. Exposes a remote-code-execution debug console

This bug actually shipped to production once on the previous NSSM stack and caused a CPU spike incident. Now codified in [`backend-standardizations.md`](./backend-standardizations.md): the **only** entrypoint compiled into the exe is the Waitress / Uvicorn boot path. Any `if __name__ == '__main__': app.run(...)` block is for local laptop iteration and must be excluded from the PyInstaller build.

### 6.4 Installing a new WinSW service

```powershell
# One-time: drop WinSW.exe (renamed) + .xml + your built app exe into the deploy folder
$svc = "KalaCorelytics"
$root = "D:\deployments\corelytics\backend"

# Download WinSW once (any modern release; pin a version in your CI)
Invoke-WebRequest `
  -Uri "https://github.com/winsw/winsw/releases/download/v3.0.0-alpha.11/WinSW-x64.exe" `
  -OutFile "$root\$svc-svc.exe"

# Drop the XML config (templated above) at $root\$svc-svc.xml

# Install + start
& "$root\$svc-svc.exe" install
& "$root\$svc-svc.exe" start
```

After install, the service shows up in `services.msc` and PowerShell:

```powershell
Get-Service KalaCorelyticsBackend
```

### 6.5 Common WinSW / service commands

WinSW exposes its own subcommands AND the service is a normal Windows service, so you can use either tool. Pick one and be consistent.

```powershell
# Via WinSW exe (works from anywhere; no admin shell needed for status)
.\KalaCorelytics-svc.exe status         # Started / Stopped
.\KalaCorelytics-svc.exe stop
.\KalaCorelytics-svc.exe start
.\KalaCorelytics-svc.exe restart
.\KalaCorelytics-svc.exe uninstall      # remove the service entirely

# Via standard PowerShell (recommended for scripting)
Get-Service KalaCorelyticsBackend
Stop-Service KalaCorelyticsBackend
Start-Service KalaCorelyticsBackend
Restart-Service KalaCorelyticsBackend
```

`Restart-Service` does stop-then-start atomically — preferred over manual stop/start because it handles the in-progress lock release correctly.

### 6.6 Logs

WinSW redirects the exe's stdout/stderr to per-day files under `<backend>\logs\`. With `roll-by-time` daily rotation, 14-day retention, older logs zip automatically.

Layout:

```
logs\
├── KalaCorelytics-svc.out.log              ← today's stdout
├── KalaCorelytics-svc.err.log              ← today's stderr
├── KalaCorelytics-svc.out.20260506.log     ← previous days
├── KalaCorelytics-svc.err.20260506.log
└── KalaCorelytics-svc.20260420.log.zip     ← compressed beyond retention window
```

Tail during a deploy:

```powershell
Get-Content D:\deployments\corelytics\backend\logs\KalaCorelytics-svc.out.log -Tail 50 -Wait
```

If your service follows [`backend-standardizations.md`](./backend-standardizations.md) and emits JSON-Lines logs, pipe through `jq`:

```bash
tail -f KalaCorelytics-svc.out.log | jq -r '"[\(.timestamp)] \(.level) \(.path) \(.status) \(.duration_ms)ms \(.message)"'
```

---

## 7. ARR proxy timeout — the famous 30-second killer

### 7.1 The problem

ARR's default `proxyTimeout` is **30 seconds**. If your backend takes longer than 30 seconds to respond, ARR closes the connection and returns 502 to the browser — even though the backend is still doing useful work.

This is the #1 cause of "the deployed app gives a network error but local works fine" tickets.

### 7.2 The fix

**Option A — IIS Manager (GUI):**

1. Open IIS Manager
2. Click the **server node** (top of tree, NOT the site)
3. Double-click **Application Request Routing Cache**
4. Right pane → **Server Proxy Settings...**
5. **Time-out (seconds)** = `600`
6. **Apply**

**Option B — appcmd:**

```cmd
%windir%\system32\inetsrv\appcmd.exe set config -section:system.webServer/proxy /timeout:00:10:00 /commit:apphost
```

**Option C — direct edit of `applicationHost.config`** (`C:\Windows\System32\inetsrv\config\`):

```xml
<system.webServer>
  <proxy enabled="true" timeout="00:10:00" />
</system.webServer>
```

Then `iisreset`.

### 7.3 Recommended timeout values

| Endpoint type | ARR timeout | Backend timeout |
|---|---|---|
| Generic API calls (fast CRUD) | 60s | 30s |
| LLM-driven endpoints (single call) | 240s | 240s |
| Multi-pass LLM (e.g., dynamic dashboard) | **600s** | **600s** |
| File uploads (large Excel) | 300s | 300s |

**Set ARR ≥ backend timeout.** Otherwise ARR closes mid-request even when the backend has more time available.

### 7.4 How to confirm ARR is the problem

```powershell
# Direct hit on backend, bypassing IIS
Invoke-WebRequest -Uri http://localhost:5101/api/<slow-endpoint> -UseBasicParsing

# Through IIS
Invoke-WebRequest -Uri https://app.kala.com/api/<slow-endpoint> -UseBasicParsing
```

| Direct | Through IIS | Diagnosis |
|---|---|---|
| 200 in 90s | 502 at 30s | ARR timeout (this section) |
| 200 in 90s | 200 in 90s | All good |
| Slow / hangs | 502 at 30s | Backend slow + ARR timeout (fix both) |
| Connection refused | 502 | Backend not running (service stopped or crashed) |

---

## 8. Frontend deployment patterns

### 8.1 The `dist/` layout

After `npm run build`:

```
dist/
├── index.html                          (entry; small)
├── assets/
│   ├── index-<hash>.js                 (main bundle)
│   ├── index-<hash>.css                (styles)
│   └── <other-hashed>.js               (code-split chunks)
├── web.config                          (from public/, copied verbatim)
├── kala-logo.png, *.png                (static assets from public/)
├── nexora/                             (static sub-app bundles, if applicable)
├── sales-training-app/
└── interviewer/
```

`web.config` lives at the dist root because IIS reads it from the site root. Vite copies anything in `public/` to `dist/` verbatim, so editing `frontend/public/web.config` is the way to change deployed rules.

### 8.2 Deploying with `robocopy /MIR`

`/MIR` mirrors source to destination, deleting files in destination not in source. This catches "I forgot to delete the old `index-abcdef.js`" issues. Be careful with it though — exclude any IIS-managed files:

```powershell
robocopy "D:\build\dist" "D:\inetpub\wwwroot\kala" /MIR /XD .git
```

### 8.3 Cache busting

Hashed filenames in `assets/` mean each build produces unique URLs. Browsers can cache them forever. But:

- `index.html` should have **short cache** (e.g., `max-age=60`) so deployments propagate quickly.
- `assets/*` can have **long cache** (e.g., `max-age=31536000, immutable`) — hash changes invalidate.

Add to `web.config`:

```xml
<location path="assets">
  <system.webServer>
    <staticContent>
      <clientCache cacheControlMode="UseMaxAge" cacheControlMaxAge="365.00:00:00" />
    </staticContent>
  </system.webServer>
</location>
```

(Optional; current setup doesn't have this, which means deploys are always immediate but bandwidth isn't optimal.)

---

## 9. MongoDB connection settings

The backend connects to MongoDB Atlas. Connection-time bugs are a frequent source of slowness.

### 9.1 Required pymongo client config

```python
from pymongo import MongoClient
import certifi

_client = MongoClient(
    uri,
    tlsCAFile=certifi.where(),
    serverSelectionTimeoutMS=10000,   # 10s; default 30s = too long for fail-fast
    connectTimeoutMS=10000,
    socketTimeoutMS=30000,
)
```

Without these timeouts, a transient Atlas hiccup blocks requests for 30 seconds — exceeds ARR's default and trips the 502 cascade.

### 9.2 IP allowlist

MongoDB Atlas requires the source IP to be allowlisted. The deployed Windows server's public IP must be in the cluster's "Network Access" list. If you see:

```
ServerSelectionTimeoutError: SSL handshake failed
```

…it's almost always because the server's IP isn't allowed. Add it in Atlas → Network Access.

### 9.3 Two URIs

Convention: `MONGODB_URI` for app data, `USER_URI` for shared auth. They can point at the same cluster or different ones. See [`backend-standardizations.md`](./backend-standardizations.md#7-configuration-via-environment-variables) for the env var contract.

---

## 10. Health checks and monitoring

### 10.1 What to ping

Per [`backend-standardizations.md`](./backend-standardizations.md#3-health-and-readiness-endpoints):

- `GET /api/health` — liveness; expects 200
- `GET /api/ready` — readiness; expects 200, downstream OK

### 10.2 IIS Application Initialization (warmup)

So the backend doesn't go cold between deploys, configure IIS Application Initialization:

```xml
<applicationInitialization
  doAppInitAfterRestart="true"
  remapManagedRequestsTo="warmup.html">
  <add initializationPage="/api/health" />
</applicationInitialization>
```

After every IIS restart / app pool recycle, IIS pre-fires `/api/health` so the first user request doesn't pay cold-start cost.

### 10.3 Monitoring (recommended)

We don't have a hosted monitoring stack today. Until we do, the on-call rotation runs:

```powershell
# scripts/health-check.ps1
$services = @(
  @{ Name='Main';        Url='http://localhost:5001/api/health' },
  @{ Name='Corelytics';  Url='http://localhost:5101/api/health' },
  @{ Name='Nexora';      Url='http://localhost:3500/api/health' },
  @{ Name='Saarthi';     Url='http://localhost:5000/api/health' },
  @{ Name='Interviewer'; Url='http://localhost:8001/api/health' }
)
foreach ($s in $services) {
  try {
    $r = Invoke-WebRequest -Uri $s.Url -UseBasicParsing -TimeoutSec 5
    Write-Host "✓ $($s.Name): $($r.StatusCode)"
  } catch {
    Write-Host "✗ $($s.Name): FAILED — $($_.Exception.Message)" -ForegroundColor Red
  }
}
```

Schedule via Task Scheduler every 5 min; pipe failures to email/Slack.

---

## 11. Common deployment errors with fixes

| Symptom | Cause | Fix |
|---|---|---|
| 502 Bad Gateway after 30s | ARR proxy timeout | §7 — bump to 600s |
| 502 immediately, "ECONNREFUSED" in `*.err.log` | Backend service stopped or crashed | `Get-Service Kala<App>Backend`; if Stopped, `Start-Service`; check `*.err.log` for traceback |
| `Access denied` when CI tries to overwrite the exe | Service still running — Windows holds an exclusive lock | CI must `Stop-Service` before overwriting; `Start-Service` after. Templates do this; see [`github-actions-integration.md`](./github-actions-integration.md) §5.2 |
| Service starts then stops within seconds | App crashed at startup (bad `.env`, missing MSVC redist, bad imports) | Tail `*.err.log`. Common: missing `.env`, MongoDB URI invalid, port already in use |
| 500 with `OPENROUTER_API_KEY is not set` | `.env` not loaded or missing | Check `.env` exists in backend dir alongside the exe; check `[STARTUP]` lines in `*.out.log` |
| 503 with `db_timeout` | MongoDB Atlas IP allowlist / cluster down | Atlas dashboard → Network Access; add server IP |
| Browser shows old code after deploy | CDN / browser cache | Hard-refresh (Ctrl+Shift+R); invalidate CDN; bump asset hashes (Vite does this automatically — usually a service-worker issue) |
| Frontend serves but `/api/*` 404s | web.config rewrite rules wrong or missing | Verify `web.config` at `dist/` root; check rule order (specific before generic) |
| 100% CPU on backend, no traffic | Werkzeug auto-reloader compiled into the exe by mistake | The PyInstaller spec must NOT include `app.run(debug=True)` paths. See §6.3 |
| Edits to `auth.py` don't take effect | The exe is the previous build; new exe wasn't deployed | CI builds a fresh exe per push; if you uploaded manually, check the file's timestamp vs. your local build. `Restart-Service` only matters once the new exe is in place |
| "session not found" on every edit | In-memory session store wiped (process restart between create + edit) | Already mitigated — frontend now sends summary along with session_id; see [`backend-standardizations.md`](./backend-standardizations.md) for state-management guidance |
| Cross-origin errors in browser console | CORS not allowing the origin | Update CORS allowlist in backend (Flask: `flask-cors`; FastAPI: `CORSMiddleware`) — see backend-standardizations §9 |
| 401 on every request | Cookie not being sent (cross-domain) or JWT expired | Check `Set-Cookie` flags (`SameSite`, `Secure`, `Domain`); check JWT `exp` claim |
| Static asset 404 — `text/html` for JS file | Frontend `base` path doesn't match deploy URL | Set `base: '/<app>/'` in `vite.config.js`; rebuild |
| Exe runs locally but service won't start on the server with `vcruntime140.dll not found` | MSVC runtime DLLs missing on server (PyInstaller didn't auto-bundle, e.g. heavy C-extension dep) | Ship the DLLs alongside the exe (`vcruntime140.dll`, `vcruntime140_1.dll`, `msvcp140.dll` etc., copied from `C:\Windows\System32\` on a machine with VC++ Redist installed). Windows finds them in the exe's folder first. Or rebuild the exe with `pyinstaller --add-binary` to embed them directly. Avoids needing admin to install the system-wide VC++ Redistributable |
| Service stuck in `StopPending` | App ignored Ctrl+Break and didn't drain in time | `Stop-Service -Force`; bump `<stoptimeout>` in the WinSW XML if your shutdown is legitimately slow |

---

## 12. Rollback procedure

If a deploy goes bad, you have two paths:

### 12.1 Backend rollback

CI keeps the previous N versions of each exe in `D:\deployments\<app>\backend\versions\`. Rollback is just a swap:

```powershell
$svc  = "KalaCorelytics"
$root = "D:\deployments\corelytics\backend"
Get-ChildItem "$root\versions" | Sort-Object Name -Descending | Select-Object -First 5 Name
# pick the version you want, e.g. v3a4f8c2

Stop-Service "${svc}Backend"
Copy-Item -Path "$root\versions\v3a4f8c2\$svc.exe" -Destination "$root\$svc.exe" -Force
Start-Service "${svc}Backend"
curl.exe http://localhost:5101/api/health        # verify
```

If you don't keep version snapshots, re-run the GitHub Actions workflow against the prior commit (Run workflow → branch/commit selector). CI rebuilds the exe and redeploys.

### 12.2 Frontend rollback

If you keep prior `dist/` builds:

```cmd
robocopy D:\backups\dist-<previous-date> D:\inetpub\wwwroot\kala /MIR /XD .git
```

Otherwise, rebuild from the previous git tag:

```cmd
cd D:\deployments\<app>\frontend
git checkout <previous-tag>
npm ci
npm run build
robocopy dist D:\inetpub\wwwroot\kala /MIR /XD .git
```

### 12.3 Bake snapshots before high-risk deploys

```powershell
# Pre-deploy backup
Copy-Item -Path D:\inetpub\wwwroot\kala -Destination "D:\backups\kala-$(Get-Date -Format 'yyyy-MM-dd-HHmm')" -Recurse
```

Cheap insurance.

---

## 13. Troubleshooting playbook

When something is broken in production, work through this in order:

### Step 1 — Is the backend alive?

```powershell
Get-Service KalaCorelyticsBackend
curl.exe http://localhost:5101/api/health
```

If `Status` is `Stopped` → `Start-Service KalaCorelyticsBackend`. If start fails or it bounces back to Stopped, tail `logs\KalaCorelytics-svc.err.log` for the crash reason.

### Step 2 — Is the backend responding correctly?

```cmd
curl -i http://localhost:5101/api/<some-known-endpoint>
```

If 5xx → check the app logs (`stdout.log` JSON lines).
If 200 here but not through IIS → §7 (ARR), §11 web.config rules.

### Step 3 — Can the backend reach MongoDB?

```cmd
curl http://localhost:5101/api/debug/db
```

If it returns errors → IP allowlist, network outage, expired credentials.

### Step 4 — Can the backend reach OpenRouter?

```cmd
curl http://localhost:5101/api/debug/openrouter
```

OpenRouter intermittent slowness is normal; persistent failures indicate API key issues or our usage limits.

### Step 5 — Is IIS proxying correctly?

```cmd
curl -i https://app.kala.com/api/health
```

Should return same response as Step 1. If different → `web.config` rules (§5.2 order).

### Step 6 — Is the frontend serving the latest build?

```cmd
curl -i https://app.kala.com/index.html | findstr "<script"
```

The script src should reference `assets/index-<hash>.js` — compare hash to your local build's. If they don't match, deploy didn't propagate.

### Step 7 — Restart everything

When all else fails:

```powershell
Restart-Service KalaCorelyticsBackend, KalaNexoraBackend, KalaSaarthiBackend, KalaInterviewerBackend
iisreset /restart
```

Brutal but effective. Note the brief downtime in the on-call log.

---

## 14. Validation checklist for a new deployment

Before declaring a deploy "done", confirm:

- [ ] Backend service `Get-Service` returns `Running`
- [ ] `curl http://localhost:<port>/api/health` returns 200
- [ ] `curl http://localhost:<port>/api/ready` returns 200 with all checks ok
- [ ] `curl https://app.kala.com/api/health` (through IIS) returns 200
- [ ] `*.out.log` shows no errors / warnings on startup; `*.err.log` is empty
- [ ] No stale pre-deploy `dist/<old-hash>.js` files referenced anywhere
- [ ] Browser hard-refresh of the site loads cleanly without console errors
- [ ] Login flow works end-to-end
- [ ] One key feature exercised (e.g., generate a dashboard, run a chat session)
- [ ] If timeouts changed, ARR proxy timeout matches backend timeout

---

## 15. FAQ

**Q. Why don't we use Docker?**
The current platform runs on Windows Server with IIS, WinSW, and self-contained PyInstaller exes. Containerization is a future direction (and would simplify some of this runbook), but it's not where we are today. The exe-based deploy already gives most of the dependency-isolation benefits of containers without the operational overhead of running a Windows container host.

**Q. Why WinSW instead of NSSM (or no wrapper at all)?**
NSSM is unmaintained and historically buggy under high-restart scenarios. WinSW is actively maintained, used by the official Jenkins agent installer, and supports rolling logs natively (so we don't need NSSM's quirky `AppRotateBytes`). A no-wrapper "just call sc.exe" approach is tempting but doesn't work reliably for console apps — they don't respond to SCM control messages within the 30-second window, so SCM kills them immediately. WinSW handles that handshake on the app's behalf.

**Q. Why Waitress instead of Gunicorn?**
Gunicorn doesn't run on Windows. Waitress is the canonical choice for Windows Python apps and is bundled inside the PyInstaller exe. On Linux deploys (future), we'd use Gunicorn + gevent.

**Q. Can I use IIS's built-in URL Rewrite without ARR?**
URL Rewrite alone is enough for static-file rules. To proxy to a different host/port, you need ARR (Application Request Routing) — it's the module that actually does the network proxying. Both are usually installed together in IIS.

**Q. Why localhost-only for backend services?**
Backends bind to `127.0.0.1` (or `0.0.0.0` only if you want explicit network access). IIS reverse-proxies them, so the public surface is IIS only. This keeps the firewall rules trivial: open 80/443 to the world, all other ports stay localhost.

**Q. How do I tail multiple service logs at once?**
PowerShell can do it with multiple jobs:
```powershell
Get-Job | Remove-Job
Start-Job -Name corelytics  -ScriptBlock { Get-Content D:\deployments\corelytics\backend\logs\KalaCorelytics-svc.out.log -Tail 0 -Wait }
Start-Job -Name interviewer -ScriptBlock { Get-Content D:\deployments\interviewer\backend\logs\KalaInterviewer-svc.out.log -Tail 0 -Wait }
while ($true) { Get-Job | Receive-Job; Start-Sleep -Seconds 1 }
```

**Q. What's the longest acceptable time to keep all services down for maintenance?**
Without explicit comms, no more than 60 seconds. Over that, post in `#ops` first. Over 5 minutes, send an email.

**Q. The on-call playbook says "restart the service" a lot. When is that NOT the right answer?**
When the failure is downstream (Mongo, OpenRouter, network), restarting just delays the issue. Steps 1-6 of §13 should run *before* "nuke and pave" in §13 step 7.

**Q. We're seeing weird behaviour after a deploy that went smoothly — could it be DNS / TLS / load-balancer caching upstream?**
Sometimes yes. KALA's deploy is single-server today so this is rare. If you're behind Cloudflare / a reverse-proxy in front of IIS, also clear that cache.

---

## 16. Document history

| Date | Change | By |
|---|---|---|
| 2026-05-04 | Initial version. Captures NSSM-vs-dev-server, ARR 30s timeout, web.config rule ordering, MongoDB timeout config, troubleshooting playbook. | Platform Architecture |
| 2026-05-08 | Switched backend deployment from NSSM + venv + pip to WinSW + self-contained PyInstaller exe. Server no longer requires Python; deploys are exe-swap + service restart. | Platform Architecture |
