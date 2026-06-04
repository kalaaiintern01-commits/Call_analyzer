# KALA AI Platform — First Deploy

**Audience:** Whoever is bringing a new KALA app online for the first time on a production or staging server. Use this once per app; thereafter, CI handles every subsequent deploy automatically.

**Scope:** The bootstrap path for both **backend** (PyInstaller exe + WinSW service) and **frontend** (federation host or federated remote, deployed via IIS). Once everything in this doc is done, the GitHub Actions workflows in [`.github/workflows/`](../.github/workflows/) take over.

**Companion docs:**
- [`deployment-runbook.md`](./deployment-runbook.md) — ongoing operations, troubleshooting, the request lifecycle
- [`github-actions-integration.md`](./github-actions-integration.md) — CI workflow templates and self-hosted runner setup
- [`backend-standardizations.md`](./backend-standardizations.md) — port assignments, service naming, HTTP conventions
- [`app-dev-guide.md`](./app-dev-guide.md) — what each new app must implement before reaching this point

---

## 1. Prerequisites

Confirm the server is set up before starting any per-app work.

### 1.1 Server-wide prereqs (one-time, all apps share these)

- [ ] **Windows Server** (2019 or 2022)
- [ ] **IIS** with **URL Rewrite** + **Application Request Routing (ARR)** modules installed
- [ ] **GitHub Actions self-hosted runner** registered (see [`github-actions-integration.md`](./github-actions-integration.md) §4)
- [ ] **Python 3.11 + the Python launcher (`py`) on the runner** so backend workflows can build PyInstaller exes via `py -3.11 -m venv ...`. Skip `actions/setup-python` (which often fails on self-hosted Windows runners due to registry/Defender). Install with `InstallAllUsers=1 Include_launcher=1`. `PrependPath` is optional - the workflow uses `py -3.11` explicitly so PATH order doesn't matter. After install, `Restart-Service actions.runner.*`. Verify with `py -3.11 --version`.
- [ ] **WinSW binary** stashed at a known location:
  ```powershell
  New-Item -ItemType Directory -Force -Path C:\tools\winsw
  Invoke-WebRequest `
    -Uri 'https://github.com/winsw/winsw/releases/download/v3.0.0-alpha.11/WinSW-x64.exe' `
    -OutFile C:\tools\winsw\WinSW-x64.exe
  ```
- [ ] **`D:\deployments\` root folder** exists (per-backend folders go inside)
- [ ] **IIS site root** exists (typically `D:\inetpub\wwwroot\kala\` — check `Get-Website | Select PhysicalPath`)
- [ ] **CDN root** exists for federated remotes (`D:\inetpub\cdn\` or wherever `/cdn/...` is mapped)
- [ ] **MongoDB Atlas** has the server's public IP in its Network Access allowlist

If any of these is missing, fix it first — every backend deploy below assumes these are in place.

### 1.2 Per-app prereqs (in the repo)

Before bringing an app to first deploy, the repo must already have:

- [ ] `requirements.txt` exact-pinned (no `^`, no `~`)
- [ ] Production entrypoint that calls Waitress (Flask) or Uvicorn (FastAPI) — **not** Werkzeug's debug server. See [`deployment-runbook.md`](./deployment-runbook.md) §6.3.
- [ ] `.env.example` listing every env var with sample values
- [ ] `/api/health` and `/api/ready` endpoints implemented
- [ ] Frontend `vite.config.js` configured (federation expose for remotes; federation host config for the shell)
- [ ] App ID, ports, exe name, service name decided and added to [`backend-standardizations.md`](./backend-standardizations.md) §8

---

## 2. Backend — first deploy

End-to-end walkthrough for **one** backend. Repeat for each new backend (main, corelytics, nexora, saarthi, interviewer, etc.) with their own identifiers.

We'll use the main app as the running example:

| Field | Value |
|---|---|
| App ID | `main` |
| Exe name | `KalaMain` (so file is `KalaMain.exe`) |
| Service name | `KalaMainBackend` |
| Backend port | `5001` |
| Deploy path | `D:\deployments\main\backend\` |
| Health URL | `http://localhost:5001/api/health` |

Substitute these for the app you're deploying.

### 2.1 Create the deploy folder structure

From an admin PowerShell on the server:

```powershell
$root = 'D:\deployments\main\backend'
New-Item -ItemType Directory -Force -Path $root, "$root\logs", "$root\versions"
```

Result:

```
D:\deployments\main\backend\
├── logs\        ← WinSW writes per-day stdout/stderr files here
└── versions\    ← CI snapshots prior exes for rollback
```

### 2.2 Drop the WinSW binary, renamed

```powershell
Copy-Item `
  -Path C:\tools\winsw\WinSW-x64.exe `
  -Destination "$root\KalaMain-svc.exe" `
  -Force
```

The naming convention `<ExeName>-svc.exe` keeps it obvious which service this WinSW instance manages.

### 2.3 Write the WinSW XML config

Save as `$root\KalaMain-svc.xml`:

```powershell
$xml = @'
<?xml version="1.0" encoding="UTF-8"?>
<service>
  <id>KalaMainBackend</id>
  <name>KALA Main Backend</name>
  <description>Dynamic Dashboard main backend (Flask, port 5001).</description>

  <executable>%BASE%\KalaMain.exe</executable>
  <workingdirectory>%BASE%</workingdirectory>

  <onfailure action="restart" delay="5 sec" />
  <onfailure action="restart" delay="10 sec" />
  <onfailure action="restart" delay="30 sec" />
  <resetfailure>1 hour</resetfailure>

  <log mode="roll-by-time">
    <pattern>yyyyMMdd</pattern>
    <autoRollAtTime>00:00:00</autoRollAtTime>
    <zipOlderThanNumDays>14</zipOlderThanNumDays>
  </log>
  <logpath>%BASE%\logs</logpath>

  <stoptimeout>30 sec</stoptimeout>
  <startmode>Automatic</startmode>
</service>
'@

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText("$root\KalaMain-svc.xml", $xml, $utf8NoBom)
```

> ⚠ **Encoding matters.** WinSW requires UTF-8 **without** a BOM.
>
> - `Out-File -Encoding utf8` on Windows PowerShell 5.x writes UTF-8 **with** BOM — some WinSW versions reject this with "Data at the root level is invalid. Line 1, position 1."
> - `Out-File` without an encoding flag defaults to UTF-16 LE on PS 5.x — guaranteed to fail.
> - `[System.IO.File]::WriteAllText(path, content, UTF8Encoding($false))` produces UTF-8 with no BOM on every PowerShell version. Use this for any file WinSW reads.
>
> Verify with `Format-Hex -Path <file> | Select-Object -First 1`. The first bytes of the XML should be `3C 3F 78 6D 6C` (`<?xml`), not `FF FE` (UTF-16 BOM) or `EF BB BF` (UTF-8 BOM).

`%BASE%` is auto-resolved by WinSW to the folder its exe lives in, so the XML is portable across environments.

### 2.4 Drop the `.env` with secrets

```powershell
$envContent = @'
PORT=5001
HOST=127.0.0.1
SERVICE_NAME=main

MONGODB_URI=mongodb+srv://USER:PASS@cluster.mongodb.net/main_db
USER_URI=mongodb+srv://USER:PASS@cluster.mongodb.net/auth
JWT_SECRET=<the-shared-secret>
OPENROUTER_API_KEY=sk-or-v1-...

LOG_LEVEL=INFO
'@

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText("$root\.env", $envContent, $utf8NoBom)
```

> ⚠ **Same encoding pitfall as the XML.** python-dotenv opens `.env` as UTF-8 and rejects any BOM. Use the `[System.IO.File]::WriteAllText` approach with UTF-8 no-BOM. Verify with `Format-Hex -Path "$root\.env" | Select-Object -First 1` — first bytes should be the first ASCII characters of your content (e.g., `50 4F 52 54` for `PORT`), not `FF FE` (UTF-16) or `EF BB BF` (UTF-8 BOM).

Replace placeholders with actual values. Then lock it down — only Administrators and SYSTEM can read it:

```powershell
icacls "$root\.env" /inheritance:r `
  /grant "Administrators:F" `
  /grant "SYSTEM:F"
```

### 2.5 Build the first exe (one-time bootstrap)

The CI workflow expects the service to already be registered before it runs. So for the **first deploy**, an exe must be in place before the service can be registered. Two options:

#### Option A — Build locally (recommended for first install)

On any machine with Python 3.11:

```powershell
cd <repo>\dynamic_dashboard\backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt pyinstaller
.\.venv\Scripts\pyinstaller --onefile --clean --name KalaMain run.py
```

Copy `dist\KalaMain.exe` to the server:

```powershell
Copy-Item .\dist\KalaMain.exe `
          \\<server-name>\D$\deployments\main\backend\KalaMain.exe -Force
```

Or via remote PowerShell / robocopy / SCP — whichever fits your network.

#### Option B — Trigger the GitHub Actions workflow once and copy from runner workspace

The first CI run will fail at the `Stop-Service` step (no service exists yet), but the **build step** runs first and produces the exe in the runner's workspace. After the workflow fails, copy the exe out of the workspace into the deploy folder:

```powershell
Copy-Item `
  -Path 'D:\actions-runner\_work\<repo-name>\<repo-name>\dynamic_dashboard\backend\dist\KalaMain.exe' `
  -Destination 'D:\deployments\main\backend\KalaMain.exe' `
  -Force
```

Option A is cleaner. After this step the folder looks like:

```
D:\deployments\main\backend\
├── KalaMain.exe            ← the app (~30-50 MB self-contained)
├── KalaMain-svc.exe        ← WinSW
├── KalaMain-svc.xml        ← service config
├── .env                    ← secrets (ACL'd)
├── logs\                   ← empty until first run
└── versions\               ← empty until second deploy
```

### 2.6 Register and start the service

From an **administrator** PowerShell:

```powershell
& "$root\KalaMain-svc.exe" install
& "$root\KalaMain-svc.exe" start
```

Verify:

```powershell
Get-Service KalaMainBackend
```

Expected output:

```
Status   Name              DisplayName
------   ----              -----------
Running  KalaMainBackend   KALA Main Backend
```

If the status is anything other than `Running`, tail the error log for the cause:

```powershell
Get-Content "$root\logs\KalaMain-svc.err.log" -Tail 50
```

### 2.7 Smoke test

```powershell
# 2.7a — Direct hit (bypasses IIS)
curl.exe -i http://localhost:5001/api/health
# Expect: 200 with JSON { "status": "ok", "service": "main", ... }

# 2.7b — Through the IIS reverse proxy (the path users actually take)
curl.exe -i https://ai.kalapms.com/api/health
# Expect: same 200 response

# 2.7c — Tail the logs to see request lines flowing
Get-Content "$root\logs\KalaMain-svc.out.log" -Tail 10 -Wait
```

### 2.8 Confirm the IIS rewrite rule routes to your backend

The parent `web.config` at the IIS site root needs a rewrite rule pointing `/api/<resource>/*` at your backend's port. For the main app, the existing rule:

```xml
<rule name="API Proxy" stopProcessing="true">
  <match url="^api/(.*)" />
  <action type="Rewrite" url="http://localhost:5001/api/{R:1}" />
</rule>
```

For new backends with an app-specific path prefix (e.g., `/api/corelytics/*`), add a more specific rule **before** the generic `^api/(.*)` rule. See [`deployment-runbook.md`](./deployment-runbook.md) §5.2 for ordering rules.

### 2.9 Reboot test (recommended once)

```powershell
Restart-Computer -Force
```

After the server comes back:

```powershell
Get-Service KalaMainBackend          # should be Running automatically
curl.exe http://localhost:5001/api/health
```

If both succeed, the service survives reboots. The `<startmode>Automatic</startmode>` in the XML handles this.

### 2.10 Backend done — what to expect from now on

From this point forward, every push to `main` that touches `dynamic_dashboard/backend/**` triggers the GitHub Actions workflow at [`.github/workflows/deploy-backend.yml`](../.github/workflows/deploy-backend.yml). The workflow:

1. Builds a new exe with PyInstaller on the runner
2. Snapshots the current exe to `versions\v<sha>\`
3. Stops the service, copies the new exe over, starts the service
4. Health-checks `http://localhost:5001/api/health`
5. Prunes snapshots beyond the most recent 5

You don't touch the server for ongoing deploys. The `.env`, WinSW exe, WinSW XML, and `logs\` directory are never modified by CI.

---

## 3. Frontend — first deploy

There are **two distinct frontend deploy patterns** on this platform. Pick the one that matches your app:

| Pattern | When to use | Where it lands |
|---|---|---|
| **Shell host** | The main app at the site root (currently `dynamic_dashboard/frontend`) — federation host that lazy-loads remotes | `D:\inetpub\wwwroot\kala\` (IIS site root) |
| **Federated remote** | Apps consumed by the shell via federation (corelytics, future apps) | `D:\inetpub\cdn\<app-id>\latest\` and `D:\inetpub\cdn\<app-id>\v<sha>\` |

### 3.1 Pattern A — Shell host (main app)

This is the one already deployed at `https://ai.kalapms.com/`. First-deploy steps for replacing or initially setting up the shell:

#### 3.1.1 Confirm `vite.config.js` is configured for the live deploy

```js
const base = command === 'build' ? (env.VITE_BASE_PATH || '/') : '/'
```

And `.env.production` has `VITE_BASE_PATH=/` for the live build. (Use `/test/` for the side-by-side test deploy — separate concern.)

#### 3.1.2 Verify all federation remote URLs are correct

`.env.production` lists every remote:

```
VITE_CORELYTICS_URL=https://ai.kalapms.com/cdn/corelytics/latest/assets/remoteEntry.js
# VITE_NEXORA_URL=...     (uncomment when ready)
# VITE_SAARTHI_URL=...    (uncomment when ready)
```

Each of these URLs must respond **before** the shell deploy lands — otherwise users hit "Loading…" forever. Test with:

```powershell
curl.exe -I https://ai.kalapms.com/cdn/corelytics/latest/assets/remoteEntry.js
# Expect 200 with content-type: application/javascript
```

Federate one app at a time. Don't enable a remote URL in `.env.production` until that remote is actually live on the CDN.

#### 3.1.3 First build + deploy

On the runner (or any build machine):

```powershell
cd <repo>\dynamic_dashboard\frontend
npm ci
npm run build
```

Console should print:

```
[main-app] mode=production — federation remotes:
  corelytics   → https://ai.kalapms.com/cdn/corelytics/latest/assets/remoteEntry.js
```

Output goes to `dist\`. Copy contents (not the folder itself) to the IIS site root:

```powershell
robocopy dist 'D:\inetpub\wwwroot\kala' /MIR /XD .git test
# (XD test preserves any side-by-side test deploy at \kala\test\)
```

Recycle the IIS app pool to make sure no cached bytes serve the previous version:

```powershell
Import-Module WebAdministration
Restart-WebAppPool -Name DefaultAppPool
```

#### 3.1.4 Smoke test

```powershell
# Public URL returns 200 with the latest index.html
curl.exe -I https://ai.kalapms.com/

# A federated route loads (shell + remote both have to work)
# (open in a real browser: https://ai.kalapms.com/corelytics)
```

In a fresh incognito window, hit `https://ai.kalapms.com/`. The shell should render and the nav links to federated remotes should load their content lazily.

### 3.2 Pattern B — Federated remote (e.g., corelytics)

Apps that the shell consumes as federation remotes. They live under `/cdn/<app-id>/` and are referenced by URL from `.env.production` of the shell.

We'll use corelytics as the running example.

#### 3.2.1 Prepare the CDN folder structure

```powershell
New-Item -ItemType Directory -Force -Path `
  D:\inetpub\cdn, `
  D:\inetpub\cdn\corelytics, `
  D:\inetpub\cdn\corelytics\latest
```

Two paths matter:

| Path | What lives here |
|---|---|
| `D:\inetpub\cdn\<app>\latest\` | The current production version |
| `D:\inetpub\cdn\<app>\v<sha>\` | Per-build snapshots (for rollback / pinning) |

#### 3.2.2 Confirm IIS exposes `/cdn/...`

Either:

- The IIS site already serves `D:\inetpub\cdn\` at the URL `/cdn/...` via a Virtual Directory, **or**
- The web.config has a CDN passthrough rule (we already added one in [`dynamic_dashboard/frontend/public/web.config`](../dynamic_dashboard/frontend/public/web.config))

Verify:

```powershell
curl.exe -I https://ai.kalapms.com/cdn/corelytics/latest/
# Should NOT return 404. Returns either a directory listing (if enabled) or 403/200 — anything but 404.
```

If 404: you need to add the Virtual Directory mapping in IIS Manager:

```powershell
Import-Module WebAdministration
New-WebVirtualDirectory `
  -Site "<your-site-name>" `
  -Name "cdn" `
  -PhysicalPath "D:\inetpub\cdn"
```

#### 3.2.3 Build the federated remote

In the corelytics repo:

```powershell
cd <corelytics-repo>\frontend
npm ci
npm run build
```

The plugin emits `dist\assets\remoteEntry.js` plus hashed chunks. Verify:

```powershell
ls dist\assets\ | Select-String remoteEntry
```

Should list `remoteEntry.js`.

#### 3.2.4 First-time CDN deploy

Copy `dist\` contents to **both** the versioned path and the `latest` alias:

```powershell
$sha = git rev-parse --short HEAD
$appRoot    = 'D:\inetpub\cdn\corelytics'
$versionDir = "$appRoot\v$sha"
$latestDir  = "$appRoot\latest"

robocopy dist $versionDir /MIR /XD .git
robocopy dist $latestDir  /MIR /XD .git
```

After this, the manifest is reachable at:

```
https://ai.kalapms.com/cdn/corelytics/latest/assets/remoteEntry.js
https://ai.kalapms.com/cdn/corelytics/v<sha>/assets/remoteEntry.js
```

#### 3.2.5 Smoke test the manifest from the public URL

```powershell
curl.exe -i https://ai.kalapms.com/cdn/corelytics/latest/assets/remoteEntry.js
```

Should return:

- Status `200`
- `Content-Type: application/javascript`
- `Cache-Control: public, max-age=60, must-revalidate` (per the outbound rule in web.config)
- Body containing the federation manifest (look for the exposed module name like `./App`)

#### 3.2.6 Wire the shell to consume this remote

In the shell's `dynamic_dashboard/frontend/.env.production`, add or uncomment:

```
VITE_CORELYTICS_URL=https://ai.kalapms.com/cdn/corelytics/latest/assets/remoteEntry.js
```

Then redeploy the shell (Pattern A). The shell's federation plugin will fetch the manifest at runtime and lazy-load chunks on demand.

#### 3.2.7 First-time done — what to expect from now on

Subsequent corelytics deploys are handled by the GitHub Actions workflow in the corelytics repo (Template A from [`github-actions-integration.md`](./github-actions-integration.md) §5.1):

- Push to `main` → CI builds → drops to `D:\inetpub\cdn\corelytics\v<sha>\` and updates `latest\`
- The shell's existing `remoteEntry.js` URL (`/cdn/corelytics/latest/...`) automatically resolves to the new version because of the short-cache (60s) outbound rule on `remoteEntry.js`. No shell rebuild needed for backend updates that don't change the federation contract.

---

## 4. Per-app onboarding checklist

Use this when bringing an app online. Tick every box before declaring "deployed."

### Backend

- [ ] App ID, exe name, service name, port chosen and recorded in [`backend-standardizations.md`](./backend-standardizations.md) §8
- [ ] `D:\deployments\<app-id>\backend\` created with `logs\` and `versions\` subfolders
- [ ] `<exe>-svc.exe` (renamed WinSW binary) copied into the folder
- [ ] `<exe>-svc.xml` written with UTF-8 encoding, `<id>` matches service name, `%BASE%` paths used
- [ ] `.env` populated with secrets, ACL'd to Administrators+SYSTEM only
- [ ] First exe built (locally with PyInstaller) and dropped into the folder
- [ ] `<exe>-svc.exe install` succeeded
- [ ] `Get-Service Kala<App>Backend` shows `Running`
- [ ] Loopback `curl http://localhost:<port>/api/health` returns 200
- [ ] Through-IIS `curl https://ai.kalapms.com/api/<resource>/health` returns 200 (after web.config rule added)
- [ ] IIS rewrite rule for the backend's URL prefix exists in parent web.config and is ordered correctly
- [ ] Reboot test: server restart → service comes back automatically → health endpoint responds

### Frontend (shell host pattern)

- [ ] `vite.config.js` has `base` env-driven, federation host config with all consumed remotes
- [ ] `.env.production` has correct remote URLs (only ones that are actually live)
- [ ] `npm run build` produces `dist/` with `index.html` referencing the right base path
- [ ] `dist/` contents copied (not the folder itself) to IIS site root
- [ ] App pool recycled
- [ ] `https://ai.kalapms.com/` returns 200 with the latest `index.html`
- [ ] At least one federated route loads end-to-end in a fresh browser

### Frontend (federated remote pattern)

- [ ] `vite.config.js` has federation plugin in expose mode, exposing `./App`
- [ ] `App.jsx` is router-less (host provides the router); `main.jsx` wraps in `BrowserRouter` for standalone dev
- [ ] `npm run build` produces `dist/assets/remoteEntry.js`
- [ ] `D:\inetpub\cdn\<app-id>\latest\` and `\v<sha>\` exist with the build output
- [ ] `https://ai.kalapms.com/cdn/<app-id>/latest/assets/remoteEntry.js` returns 200 with `Content-Type: application/javascript`
- [ ] Shell's `.env.production` updated with the remote URL
- [ ] Shell rebuilt and redeployed (Pattern A)
- [ ] In a real browser, the shell's route for this remote loads the federated chunk

---

## 5. Common first-deploy gotchas

### Backend

| Symptom | Cause | Fix |
|---|---|---|
| `<svc>-svc.exe install` fails with "Data at the root level is invalid. Line 1, position 1." | XML has UTF-16 LE BOM (PS 5.x default) or UTF-8 with BOM | Re-write the file as UTF-8 **without** BOM: `[System.IO.File]::WriteAllText($path, $xml, [System.Text.UTF8Encoding]::new($false))`. Verify with `Format-Hex -Path $path | Select-Object -First 1` — first bytes should be `3C 3F 78 6D 6C` (`<?xml`) |
| Service crashes on startup with `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0` | `.env` file has UTF-16 LE BOM (PS 5.x default) | Same fix as the XML — rewrite `.env` as UTF-8 no-BOM via `[System.IO.File]::WriteAllText`. python-dotenv opens `.env` as UTF-8; any BOM blows up the parser. |
| Service starts then stops within 5 seconds | App crashed reading `.env` (missing var, bad URI) | Tail `logs\<exe>-svc.err.log` for the exception |
| `vcruntime140.dll not found` | MSVC runtime DLLs missing on server | Either install VC++ Redistributable, or copy DLLs into deploy folder, or rebuild with `pyinstaller --add-binary` (see [`deployment-runbook.md`](./deployment-runbook.md) §11) |
| `OSError: [WinError 10048]` (port in use) | Old NSSM service or a dev process still bound to the port | `Get-NetTCPConnection -LocalPort <port>` to find the holder; stop it |
| Service shows `Running` but `/api/health` 404s | Exe bound to a different port than `.env` declares | Verify the exe loads `.env` from `%BASE%`; check `<workingdirectory>` in XML |
| `Access denied` overwriting the exe during deploy | Service still running and Windows holds a file lock | The CI workflow handles this with `Stop-Service` first; manual deploys must too |
| First deploy shows the OLD app on `/api/health` | Forgot to put the new exe in the deploy folder | The `Stop-Service` succeeded but no new file landed; recheck CI logs or manual copy |

### Frontend — shell host

| Symptom | Cause | Fix |
|---|---|---|
| `index.html` references `/test/assets/...` on the live site | `VITE_BASE_PATH=/test/` in `.env.production` | Change to `VITE_BASE_PATH=/` and rebuild |
| Federated route gets stuck on "Loading…" | The remote's `remoteEntry.js` URL returns 404 | Verify the URL in `.env.production` is correct and the CDN deploy actually shipped |
| `Failed to fetch dynamically imported module` in console | CORS blocking the remoteEntry fetch (only when shell + CDN are different origins) | Use the same-origin CDN at `/cdn/...` or add CORS headers on the CDN response |
| Shell shows "Mixed content" warnings | Some asset loads over HTTP from inside an HTTPS page | All federation URLs in `.env.production` must be `https://` |

### Frontend — federated remote

| Symptom | Cause | Fix |
|---|---|---|
| `https://ai.kalapms.com/cdn/<app>/latest/assets/remoteEntry.js` returns 404 | CDN folder missing or IIS isn't routing `/cdn/...` to it | Step 3.2.1 + 3.2.2 — verify physical folder and IIS Virtual Directory |
| `Content-Type: text/html` on `remoteEntry.js` | The React SPA catch-all rule rewrote the request to `/index.html` | Add the CDN passthrough rule (already in our web.config) before the SPA catch-all |
| Browser console: "Module not found: `./App`" | Remote exposes a different name than the host imports | Match: shell's `import('corelytics/App')` ↔ remote's `exposes: { './App': './src/App.jsx' }` |
| "Invalid hook call" / two copies of React | React not properly shared between host and remote | Both `vite.config.js` files must have `shared: ['react', 'react-dom', 'react-router-dom']`. Versions must match. |

---

## 6. After first deploy

Once everything in §4 is checked:

1. **Tag the commit** that produced the first successful deploy:
   ```powershell
   git tag deploy-<app-id>-<date>-<sha>
   git push origin --tags
   ```

2. **Subscribe the on-call channel** to the GitHub Actions notifications for the repo (via Slack / Teams integration — see [`github-actions-integration.md`](./github-actions-integration.md) §9.1).

3. **Update the canonical lists**:
   - Add the new service to `backend-standardizations.md` §8 port table
   - Add the new app ID to `auth-contract.md` §3 (if it's a federated app)

4. **Document the first-deploy date** in this app's repo `README.md` so the next person to touch it knows when it went live.

5. **Hand off to CI.** From now on, `git push` is the only deploy command anyone runs.

---

## 7. Document history

| Date | Change | By |
|---|---|---|
| 2026-05-08 | Initial version. Bootstrap walkthrough for first-time backend (WinSW + PyInstaller exe) and frontend (shell host + federated remote) deploys. | Platform Architecture |
