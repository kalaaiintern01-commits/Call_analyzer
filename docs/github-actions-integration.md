# KALA AI Platform - GitHub Actions Integration

**Audience:** Engineers wiring up CI/CD for KALA repos - installing the runner on the platform server, writing workflow files, and rolling out automated deploys per-repo.

**Status:** Active. Replace this doc when migrating to a different CI/CD provider.

**Owner:** Platform Architecture team (runner infrastructure) + each repo team (workflow files).

**Companion docs:**

- [`deployment-runbook.md`](./deployment-runbook.md) - what each deploy command actually does
- [`backend-standardizations.md`](./backend-standardizations.md) - the conventions deploys must preserve
- [`federation-cookbook.md`](./federation-cookbook.md) - frontend federation builds these workflows produce
- [`version-standardization.md`](./version-standardization.md) - version checks the workflow can enforce
- [`app-dev-guide.md`](./app-dev-guide.md) - onboarding flow that includes adding CI

---

## 1. Purpose

KALA's deploy story today is "robocopy from a developer's laptop". This works for one engineer; it doesn't scale to a multi-team platform. GitHub Actions automates it: every `git push` to `main` builds, tests, and deploys without human intervention.

This doc gives you:

1. The **architecture** - how GitHub Actions reaches the on-prem Windows server
2. The **one-time runner setup** for the server
3. **Workflow templates** for the three deploy types (federation remote, backend, shell)
4. The **rollout sequence** to bring repos onto CI one by one
5. **Common errors** with fixes

Total time from zero to "every repo deploys automatically": roughly a half-day for the whole platform.

---

## 2. Architecture

```
                    ┌─────────────────────────────────────────────┐
                    │  GitHub                                     │
                    │  ├── kala-corelytics    repo                │
                    │  ├── kala-nexora        repo                │
                    │  ├── kala-saarthi       repo                │
                    │  ├── kala-interviewer   repo                │
                    │  └── kala-shell         repo                │
                    │                                             │
                    │  on push to main:                           │
                    │     workflow runs on self-hosted runner     │
                    └────────────────────┬────────────────────────┘
                                         │ webhook (outbound from server)
                                         ▼
                    ┌─────────────────────────────────────────────┐
                    │  Windows Server (ai.kalapms.com)            │
                    │                                             │
                    │  ┌──────────────────────────────────────┐   │
                    │  │  GitHub Actions Runner (service)     │   │
                    │  │  Polls GitHub for jobs to run        │   │
                    │  └──────────┬───────────────────────────┘   │
                    │             │                               │
                    │             │ executes the workflow steps   │
                    │             ▼                               │
                    │  ┌──────────────────────────────────────┐   │
                    │  │ Build:                               │   │
                    │  │   • Frontend: npm run build          │   │
                    │  │   • Backend:  PyInstaller -> .exe     │   │
                    │  │ Deploy:                              │   │
                    │  │   • Frontend: robocopy -> IIS site    │   │
                    │  │   • Backend:  Stop-Service ->         │   │
                    │  │               drop exe -> Start       │   │
                    │  └──────────────────────────────────────┘   │
                    └─────────────────────────────────────────────┘
```

**Key properties:**

- The runner **polls** GitHub for work - only outbound HTTPS connections from the server. No inbound firewall changes.
- One runner serves **all KALA repos**. Workflows opt-in via `runs-on: [self-hosted, ...]`.
- Builds happen on the **same server** as the deployment target - no need to push artifacts over a network.
- `.env` files and secrets stay on the server; the runner deploys files alongside them but never overwrites.

---

## 3. Self-hosted vs GitHub-hosted runners

GitHub offers free Linux/macOS/Windows runners hosted on their infrastructure. They sound easier but don't fit our deploy target. Comparison:

| Aspect | Self-hosted (recommended) | GitHub-hosted |
|---|---|---|
| Where it runs | On `ai.kalapms.com` itself | GitHub's infrastructure |
| Network setup | None - outbound webhook only | Need WinRM/SSH inbound to your server (firewall changes, security review) |
| Speed | Fast - `npm ci` and `robocopy` are local | Slower - must upload artifacts to your server every run |
| Cost | Free (your hardware) | Free for public repos, billed for private |
| Secrets handling | `.env` files on server are referenced directly | Must inject every secret as a GitHub Secret on every run |
| Platform fit | Perfect - Windows + IIS + WinSW all local | Awkward - would need PowerShell remoting or smb |

**Use self-hosted on the deploy server.** The rest of this doc assumes that choice.

---

## 4. Install the runner (one-time, per server)

You install the runner **once** per environment (production server, optionally a separate staging server). Every repo afterwards just opts in via labels.

### 4.1 Generate the runner registration token

1. In GitHub, navigate to your **organization** -> **Settings** -> **Actions** -> **Runners** -> **New self-hosted runner**.
   (You can scope a runner to a single repo, but org-level lets every KALA repo share one runner.)
2. Pick **Windows** + **x64**.
3. Copy the **registration token** GitHub displays - it's valid for one hour.

### 4.2 Install on the server

In an admin PowerShell on `ai.kalapms.com`:

```powershell
# Create runner directory
New-Item -ItemType Directory -Path D:\actions-runner -Force
Set-Location D:\actions-runner

# Download the runner package - version may have advanced; check
# https://github.com/actions/runner/releases for the latest
$ProgressPreference = 'SilentlyContinue'
Invoke-WebRequest `
  -Uri https://github.com/actions/runner/releases/download/v2.319.1/actions-runner-win-x64-2.319.1.zip `
  -OutFile runner.zip
Expand-Archive -Path runner.zip -DestinationPath . -Force
Remove-Item runner.zip

# Configure with the token from §4.1
.\config.cmd `
  --url https://github.com/<your-org> `
  --token <PASTE-FROM-GITHUB> `
  --name kala-prod-runner `
  --labels "self-hosted,Windows,X64,kala-prod" `
  --runasservice
```

The `--labels` are how workflows target this specific runner. Use:

- `kala-prod` for the production server
- `kala-staging` for staging (when you add it)
- `kala-dev` for any dev/test box

### 4.3 Run as a Windows service

```powershell
.\svc.cmd install
.\svc.cmd start
```

Verify in **Services** (`services.msc`): a service named `actions.runner.<org>.<runner-name>` is running. It auto-starts on reboot.

### 4.4 Verify in GitHub

GitHub -> org **Settings** -> **Actions** -> **Runners**. Your runner shows as **Idle (online)**. If it's offline, see §10.

### 4.5 Grant the runner permissions

The runner runs as `LOCAL SYSTEM` by default - that account already has the rights it needs. If you change to a less-privileged service account later, grant explicitly:

```powershell
icacls "D:\inetpub\wwwroot\kala" /grant "<runner-user>:(OI)(CI)F" /T
icacls "D:\inetpub\cdn"          /grant "<runner-user>:(OI)(CI)F" /T
icacls "D:\deployments"          /grant "<runner-user>:(OI)(CI)F" /T
```

The runner also needs permission to control the backend Windows services. Default `LOCAL SYSTEM` already has full SCM rights - no extra grants needed. If you change to a less-privileged service account, grant control via:

```powershell
sc.exe sdset KalaCorelyticsBackend "D:(A;;LCRPWPRC;;;<runner-user-sid>)..."
```

(Most teams skip this and stay on `LOCAL SYSTEM` - it's simplest.)

### 4.6 Smoke test

Drop this minimal workflow into any repo at `.github/workflows/test-runner.yml`:

```yaml
name: Test runner
on: workflow_dispatch
jobs:
  test:
    runs-on: [self-hosted, Windows, kala-prod]
    steps:
      - run: Write-Host "Hello from $env:COMPUTERNAME"
        shell: powershell
```

Trigger it manually from GitHub Actions tab. If it prints your server's hostname, the runner is wired correctly. Delete the file afterwards.

---

## 5. Workflow templates

Three templates cover everything you'll ship. Drop them into each repo's `.github/workflows/` folder, customize the env vars at the top, commit.

> ### Important: keep `run: |` blocks pure ASCII
>
> Every `run: |` block becomes a temp PowerShell script on the runner. Windows PowerShell 5.x reads those temp scripts using the system's ANSI codepage (typically Windows-1252), not UTF-8. Multi-byte UTF-8 characters get misinterpreted and can break parsing.
>
> Symptom: `The string is missing the terminator: "`, `Missing closing '}'`, or `Unexpected token` errors at lines that look syntactically correct.
>
> Concrete: an em-dash (`-`, UTF-8 bytes `E2 80 94`) becomes `Â§"` under Windows-1252 - the trailing byte is interpreted as a closing quote and breaks string parsing.
>
> **Stick to plain ASCII inside any `run: |` block.** Use `->` not arrows, `--` not em-dashes, plain hyphens not bullets, "section X" not section signs. YAML comments above `jobs:` are fine because they never reach the runner.

### 5.1 Template A - Federation remote (frontend)

For Corelytics, Nexora, Saarthi, Interviewer Bot, and any future federated remote.

Place at `.github/workflows/deploy.yml`:

```yaml
name: Build and Deploy Federation Remote

on:
  push:
    branches: [main]
    paths:
      - 'frontend/**'
      - '.github/workflows/deploy.yml'
  workflow_dispatch:

env:
  APP_ID: corelytics                # <- change per repo: corelytics / nexora / saarthi / interviewer
  CDN_ROOT: 'D:\inetpub\cdn'

concurrency:
  # GitHub Actions does NOT expose the workflow `env:` block to
  # concurrency.group expressions - env is not in scope at parse time.
  # Hardcode the app id; keep it in sync with APP_ID below.
  group: deploy-corelytics       # change per repo
  cancel-in-progress: false

jobs:
  deploy:
    runs-on: [self-hosted, Windows, kala-prod]
    defaults:
      run:
        working-directory: frontend
        shell: powershell

    steps:
      - name: Check out code
        uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '20'

      - name: Install dependencies
        run: npm ci

      - name: Verify Tier-1 versions match the platform standard
        run: |
          $required = @{
            'react'             = '18.2.0'
            'react-dom'         = '18.2.0'
            'react-router-dom'  = '6.22.0'
          }
          foreach ($pkg in $required.Keys) {
            $got = (Get-Content "node_modules/$pkg/package.json" -Raw | ConvertFrom-Json).version
            if ($got -ne $required[$pkg]) {
              Write-Error "Tier-1 mismatch: $pkg - required $($required[$pkg]), got $got"
              exit 1
            }
            Write-Host "OK $pkg $got"
          }

      - name: Build with versioned base path
        run: |
          $sha = "${{ github.sha }}".Substring(0, 7)
          $env:VITE_BASE_PATH = "/cdn/${{ env.APP_ID }}/v$sha/"
          npm run build
          Write-Host "Built with VITE_BASE_PATH=$env:VITE_BASE_PATH"

      - name: Deploy to versioned + latest folders
        run: |
          $sha = "${{ github.sha }}".Substring(0, 7)
          $appRoot    = "${{ env.CDN_ROOT }}\${{ env.APP_ID }}"
          $versionDir = "$appRoot\v$sha"
          $latestDir  = "$appRoot\latest"

          # /R:3 /W:5 = retry 3 times with 5s wait. Default robocopy retry is
          # /R:1000000 which can hang the workflow for hundreds of days on a
          # locked or permission-denied destination - fail fast instead.
          robocopy dist $versionDir /MIR /XD .git /R:3 /W:5
          if ($LASTEXITCODE -gt 7) { Write-Error "robocopy versioned failed"; exit 1 }
          robocopy dist $latestDir /MIR /XD .git /R:3 /W:5
          if ($LASTEXITCODE -gt 7) { Write-Error "robocopy latest failed"; exit 1 }

          # robocopy 0-7 are success codes - reset so this step succeeds
          $LASTEXITCODE = 0
          Write-Host "Deployed: $versionDir + $latestDir"

      - name: Verify the manifest is reachable
        run: |
          $url = "https://ai.kalapms.com/cdn/${{ env.APP_ID }}/latest/assets/remoteEntry.js"
          $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 15
          if ($r.StatusCode -ne 200) {
            Write-Error "Manifest at $url returned $($r.StatusCode)"
            exit 1
          }
          if ($r.Headers['Content-Type'] -notlike 'application/javascript*') {
            Write-Error "Wrong content-type: $($r.Headers['Content-Type'])"
            exit 1
          }
          Write-Host "OK Manifest reachable, content-type OK"

      - name: Prune old versions (keep last 5)
        run: |
          $appRoot = "${{ env.CDN_ROOT }}\${{ env.APP_ID }}"
          Get-ChildItem $appRoot -Directory -Filter "v*" |
            Sort-Object Name -Descending |
            Select-Object -Skip 5 |
            ForEach-Object {
              Write-Host "Removing old version: $($_.Name)"
              Remove-Item $_.FullName -Recurse -Force
            }
```

### 5.2 Template B - Backend service (Flask / FastAPI)

For each Python backend on the platform. The runner builds a self-contained exe with PyInstaller and ships only that exe to the server.

Place at `.github/workflows/deploy-backend.yml`:

```yaml
name: Deploy Backend Service

on:
  push:
    branches: [main]
    paths:
      - 'backend/**'
      - '.github/workflows/deploy-backend.yml'
  workflow_dispatch:

env:
  APP_ID:        corelytics                              # <- change per repo
  EXE_NAME:      KalaCorelytics                          # output filename (no .exe)
  SERVICE_NAME:  KalaCorelyticsBackend                   # must match WinSW <id> on the server
  DEPLOY_PATH:   'D:\deployments\corelytics\backend'     # holds .env, WinSW.exe, WinSW.xml
  HEALTH_URL:    'http://localhost:5101/api/health'      # adjust port per service
  KEEP_VERSIONS: '5'                                     # rollback snapshots to retain

concurrency:
  # GitHub Actions does NOT expose the workflow `env:` block to
  # concurrency.group expressions - env is not in scope at parse time.
  # Hardcode the app id; keep it in sync with APP_ID below.
  group: backend-corelytics      # change per repo
  cancel-in-progress: false

jobs:
  deploy:
    runs-on: [self-hosted, Windows, kala-prod]

    steps:
      - name: Check out code
        uses: actions/checkout@v4

      # Build uses Python via the `py` launcher, selecting 3.11 explicitly so
      # the build is deterministic regardless of what `python` resolves to in
      # PATH. `py -3.11` requires Python 3.11 + the launcher to be installed
      # on the runner. If 3.11 isn't present, this step fails with a clear
      # "no python found" error.
      #
      # actions/setup-python often fails on self-hosted Windows runners due
      # to registry/Defender interference, so we use a pre-installed Python
      # rather than installing per-run. The produced exe is fully
      # self-contained, so the deploy server has no Python install.
      - name: Build exe with PyInstaller
        shell: powershell
        working-directory: backend
        run: |
          py -3.11 -m venv .venv
          .\.venv\Scripts\pip install -q --upgrade pip
          .\.venv\Scripts\pip install -q -r requirements.txt
          .\.venv\Scripts\pip install -q pyinstaller

          # --onefile: single self-contained exe
          # --name:    output filename (must match EXE_NAME env var)
          # The PyInstaller spec MUST point at a production entrypoint that
          # calls Waitress/Uvicorn directly - never one ending in
          # app.run(debug=True). See deployment-runbook.md section 6.3.
          .\.venv\Scripts\pyinstaller --onefile --clean `
            --name "${{ env.EXE_NAME }}" `
            run.py

          if (-not (Test-Path "dist\${{ env.EXE_NAME }}.exe")) {
            Write-Error "PyInstaller did not produce dist\${{ env.EXE_NAME }}.exe"
            exit 1
          }
          Write-Host "OK Built dist\${{ env.EXE_NAME }}.exe"

      - name: Snapshot current exe for rollback
        shell: powershell
        run: |
          $sha       = "${{ github.sha }}".Substring(0, 7)
          $current   = "${{ env.DEPLOY_PATH }}\${{ env.EXE_NAME }}.exe"
          $snapshots = "${{ env.DEPLOY_PATH }}\versions"
          New-Item -ItemType Directory -Force -Path $snapshots | Out-Null
          if (Test-Path $current) {
            $dest = "$snapshots\v$sha"
            New-Item -ItemType Directory -Force -Path $dest | Out-Null
            Copy-Item $current "$dest\${{ env.EXE_NAME }}.exe" -Force
            Write-Host "OK Snapshotted current exe -> versions\v$sha"
          } else {
            Write-Host "(first deploy - no current exe to snapshot)"
          }

      - name: Stop service, swap exe, start service
        shell: powershell
        run: |
          # Stopping releases the file lock so we can overwrite the exe.
          if ((Get-Service "${{ env.SERVICE_NAME }}" -ErrorAction SilentlyContinue).Status -eq "Running") {
            Stop-Service "${{ env.SERVICE_NAME }}"
            # Wait for the process to actually release the file
            $deadline = (Get-Date).AddSeconds(30)
            while ((Get-Service "${{ env.SERVICE_NAME }}").Status -ne "Stopped" -and (Get-Date) -lt $deadline) {
              Start-Sleep -Milliseconds 500
            }
            Write-Host "OK Service stopped"
          }

          Copy-Item "backend\dist\${{ env.EXE_NAME }}.exe" `
                    "${{ env.DEPLOY_PATH }}\${{ env.EXE_NAME }}.exe" -Force
          Write-Host "OK Exe replaced"

          Start-Service "${{ env.SERVICE_NAME }}"
          # Allow a moment for Waitress/Uvicorn to bind the port
          Start-Sleep -Seconds 5

          $status = (Get-Service "${{ env.SERVICE_NAME }}").Status
          if ($status -ne "Running") {
            Write-Error "Service did not return to Running - current status: $status"
            Write-Host "--- Last 30 lines of stderr ---"
            $errLog = "${{ env.DEPLOY_PATH }}\logs\${{ env.EXE_NAME }}-svc.err.log"
            if (Test-Path $errLog) { Get-Content $errLog -Tail 30 }
            exit 1
          }
          Write-Host "OK ${{ env.SERVICE_NAME }} is $status"

      - name: Health check
        shell: powershell
        run: |
          # Already slept 5s above; one more retry loop in case readiness lags.
          $deadline = (Get-Date).AddSeconds(30)
          do {
            try {
              $r = Invoke-WebRequest -Uri "${{ env.HEALTH_URL }}" `
                                     -UseBasicParsing -TimeoutSec 5
              if ($r.StatusCode -eq 200) {
                Write-Host "OK Health endpoint returned 200"
                exit 0
              }
            } catch {
              Start-Sleep -Seconds 2
            }
          } while ((Get-Date) -lt $deadline)
          Write-Error "Health check did not return 200 within 30s"
          exit 1

      - name: Prune old version snapshots
        shell: powershell
        if: always()
        run: |
          $snapshots = "${{ env.DEPLOY_PATH }}\versions"
          if (Test-Path $snapshots) {
            Get-ChildItem $snapshots -Directory |
              Sort-Object LastWriteTime -Descending |
              Select-Object -Skip ${{ env.KEEP_VERSIONS }} |
              ForEach-Object {
                Write-Host "Pruning old snapshot: $($_.Name)"
                Remove-Item $_.FullName -Recurse -Force
              }
          }
```

⚠ **Important - what stays untouched on the server**

This workflow only writes the new exe to `${DEPLOY_PATH}\${EXE_NAME}.exe`. It NEVER overwrites:

| File | Why it's protected |
|---|---|
| `.env` | Contains secrets; created once during initial setup |
| `${EXE_NAME}-svc.exe` (WinSW binary) | Stable across deploys; pin a WinSW version in your initial-setup script |
| `${EXE_NAME}-svc.xml` (WinSW config) | Service definition; only update during a deliberate config rev |
| `logs\` | Historical log files - WinSW manages rotation |
| `versions\` | Rollback snapshots - pruned to `KEEP_VERSIONS`; never wholesale deleted |

The `.env` survives every deploy because the workflow doesn't `robocopy /MIR` the deploy folder - it only drops a single file.

### 5.3 Template C - Shell frontend

For the user-facing app at the platform root (currently `dynamic_dashboard/`, eventually `micro-apps/shell/`).

Place at `.github/workflows/deploy-shell.yml`:

```yaml
name: Deploy Shell Frontend

on:
  push:
    branches: [main]
    paths:
      - 'frontend/**'
      - '.github/workflows/deploy-shell.yml'
  workflow_dispatch:

env:
  IIS_SITE_ROOT: 'D:\inetpub\wwwroot\kala'
  APP_POOL:      'DefaultAppPool'

concurrency:
  group: deploy-shell
  cancel-in-progress: false

jobs:
  deploy:
    runs-on: [self-hosted, Windows, kala-prod]
    defaults:
      run:
        working-directory: frontend
        shell: powershell

    steps:
      - name: Check out code
        uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '20'

      - name: Install dependencies
        run: npm ci

      - name: Build (production)
        run: npm run build

      - name: Deploy dist/ to IIS site root
        run: |
          # /MIR mirrors source -> destination (deletes orphan files in dest).
          # /XD .git excludes git folders.
          # We deliberately do NOT exclude web.config - Vite produces a fresh
          # one in dist/, and that's what we want IIS to use after deploy.
          # /R:3 /W:5 - fail fast instead of robocopy's default /R:1000000.
          robocopy dist "${{ env.IIS_SITE_ROOT }}" /MIR /XD .git /R:3 /W:5
          if ($LASTEXITCODE -gt 7) { Write-Error "robocopy failed"; exit 1 }
          $LASTEXITCODE = 0

      - name: Recycle IIS app pool (best-effort)
        # IIS auto-detects file changes in the site root and reloads web.config
        # on the next request, so a static SPA deploy doesn't strictly need this.
        # Restart-WebAppPool requires admin-level IIS permissions (read access
        # to applicationHost.config) which the runner may not have. Treated as
        # best-effort: log and continue on failure.
        continue-on-error: true
        run: |
          Import-Module WebAdministration
          try {
            Restart-WebAppPool -Name "${{ env.APP_POOL }}"
            Write-Host "OK App pool ${{ env.APP_POOL }} recycled"
          } catch {
            Write-Warning "App pool recycle skipped: $($_.Exception.Message)"
            Write-Warning "Static files are already deployed; IIS will reload them on next request."
          }

      - name: Verify shell loads
        run: |
          $r = Invoke-WebRequest -Uri https://ai.kalapms.com/ -UseBasicParsing -TimeoutSec 30
          if ($r.StatusCode -ne 200) {
            Write-Error "Shell did not return 200: $($r.StatusCode)"
            exit 1
          }
          Write-Host "OK Shell at https://ai.kalapms.com/ returned 200"
```

---

## 6. Secrets management

For self-hosted-runner deploys, you typically need **zero GitHub Secrets**:

- The runner runs ON the deploy target -> all paths are local
- `.env` files live on the server -> never injected at deploy time
- No SSH/WinRM credentials needed

This is the second-biggest reason to choose self-hosted.

If you ever do need a secret in a workflow (e.g., a Slack webhook URL, a CDN purge API key), add it via the repo's **Settings -> Secrets and variables -> Actions** UI:

```yaml
- name: Notify Slack
  run: Invoke-WebRequest "${{ secrets.SLACK_WEBHOOK }}" -Method POST -Body $body
  shell: powershell
```

**Secrets convention:**

| Use | Convention |
|---|---|
| One repo only | Repo secrets (Settings -> Secrets) |
| All KALA repos | Org-level secrets - set once at the org, accessible from every repo |
| Production-only | Environment secrets (Settings -> Environments -> production) - requires manual approval to deploy |

Never put secrets in plain text in workflow files or in commit messages. The runner has access to repo files, so they leak everywhere if committed.

---

## 7. First-deploy procedure (per repo)

Once the runner is installed (§4), bringing a repo onto CI is roughly 30 minutes:

### 7.1 Pick the smallest, lowest-risk repo first

Recommendation: **Corelytics** (the standalone we just extracted). Smallest blast radius. Validates the whole pipeline before the higher-stakes apps.

### 7.2 Create the workflow file

In the repo, create `.github/workflows/deploy.yml`. Copy Template A (or B for backend, C for shell). Customize the `env:` block:

- `APP_ID`
- `EXE_NAME` (backends - output filename and what's dropped on the server)
- `SERVICE_NAME` (backends - must match the WinSW `<id>` on the server)
- `DEPLOY_PATH` (backends)
- `HEALTH_URL` (backends, with correct port)

### 7.3 Commit and push

```bash
git add .github/workflows/deploy.yml
git commit -m "ci: add GitHub Actions deploy workflow"
git push
```

### 7.4 Watch the first run

GitHub -> Actions tab -> click the running job -> live log streaming.

Common first-run failures (full table in §10):

- "Waiting for runner" -> label mismatch
- `npm ci` fails on lock-file mismatch -> run `npm install` locally and commit lock file
- `robocopy` step exits 1 -> already handled by the `$LASTEXITCODE = 0` line; if you removed it, add it back
- `Stop-Service`/`Start-Service` says "Cannot find any service with service name X" -> `SERVICE_NAME` env var doesn't match the WinSW `<id>` registered on the server

### 7.5 Verify the deploy actually shipped

After "✅ workflow succeeded":

```powershell
# Backend
curl.exe -I http://localhost:<port>/api/health

# Frontend remote
curl.exe -I https://ai.kalapms.com/cdn/<app>/latest/assets/remoteEntry.js

# Shell
curl.exe -I https://ai.kalapms.com/
```

All should return 200.

### 7.6 Add a status badge to README

```markdown
[![Deploy](https://github.com/<org>/<repo>/actions/workflows/deploy.yml/badge.svg)](https://github.com/<org>/<repo>/actions/workflows/deploy.yml)
```

Drop it at the top of the repo README so anyone can see at-a-glance whether main is healthy.

---

## 8. Rollout sequence

The right order to bring repos onto CI:

1. **Corelytics frontend (Template A)** - proves the federation deploy pipeline
2. **Corelytics backend (Template B)** - proves the PyInstaller build + Stop-Service/Start-Service flow
3. **Shell frontend (Template C)** - proves the IIS site root + app-pool-recycle path
4. **Saarthi frontend + backend** - same templates, different env vars
5. **Nexora frontend + backend** - same
6. **Interviewer frontend + backend** - same

By the end you have ~9 workflows but they're all near-copies of the three templates. Each subsequent repo takes ~10 minutes once you're familiar.

---

## 9. Refinements (add when needed)

These are nice-to-haves you can layer on once the basics work.

### 9.1 Slack / Teams notifications

```yaml
- name: Notify on failure
  if: failure()
  shell: powershell
  run: |
    $body = @{
      text = "Deploy of ${{ env.APP_ID }} FAILED - commit ${{ github.sha }}, branch ${{ github.ref_name }}, see ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
    } | ConvertTo-Json
    Invoke-WebRequest -Uri "${{ secrets.SLACK_WEBHOOK }}" -Method POST `
      -Body $body -ContentType "application/json"
```

### 9.2 Manual approval for production

Use **GitHub Environments**:

1. GitHub repo -> Settings -> Environments -> New environment "production"
2. Add **Required reviewers** (one or more team members)
3. In the workflow:

```yaml
jobs:
  deploy:
    runs-on: [self-hosted, Windows, kala-prod]
    environment: production    # <- waits for human approval before running
```

The workflow pauses until an approver clicks "Approve and deploy" in the GitHub UI.

### 9.3 Staging vs production

Install a second runner with `--labels "self-hosted,Windows,X64,kala-staging"` on a staging server. Then in workflows:

```yaml
strategy:
  matrix:
    target:
      - { runner: kala-staging, branch: develop }
      - { runner: kala-prod,    branch: main }
runs-on: [self-hosted, Windows, '${{ matrix.target.runner }}']
```

Different branches deploy to different servers automatically.

### 9.4 Auto-rollback on health-check failure

After the deploy step, if the health check fails, restore the previous version from the `v<sha>/` snapshot:

```yaml
- name: Auto-rollback on health failure
  if: failure()
  shell: powershell
  run: |
    $appRoot = "${{ env.CDN_ROOT }}\${{ env.APP_ID }}"
    $previousVersion = (Get-ChildItem $appRoot -Directory -Filter "v*" |
      Sort-Object Name -Descending | Select-Object -Skip 1 -First 1).Name
    if ($previousVersion) {
      Write-Host "Rolling back latest/ -> $previousVersion"
      robocopy "$appRoot\$previousVersion" "$appRoot\latest" /MIR /XD .git
    }
```

### 9.5 Caching `node_modules`

`actions/setup-node` already caches the npm cache directory. To cache `node_modules` itself:

```yaml
- uses: actions/cache@v4
  with:
    path: frontend/node_modules
    key: node-modules-${{ runner.os }}-${{ hashFiles('frontend/package-lock.json') }}
```

Speeds up builds by 30-60s when dependencies haven't changed.

### 9.6 Release tagging

After a successful deploy to production, tag the commit so you can find it later:

```yaml
- name: Tag release
  if: success() && github.ref == 'refs/heads/main'
  shell: powershell
  run: |
    $sha = "${{ github.sha }}".Substring(0, 7)
    $tag = "deploy-$(Get-Date -Format 'yyyyMMdd-HHmm')-$sha"
    git tag $tag
    git push origin $tag
```

---

## 10. Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| Workflow stuck on "Waiting for runner" | Runner is offline -> check `services.msc` for `actions.runner.*`; restart. Or label mismatch -> check `runs-on` labels match `--labels` from `config.cmd` |
| `npm ci` fails: "lockfile differs from package.json" | Lock file out of sync. Run `npm install` locally and commit the new `package-lock.json` |
| `robocopy` exits 1 even though files copied successfully | robocopy uses 0-7 as success codes (1=files copied, 2=extra files, etc.); 8+ is a real failure. The templates include `$LASTEXITCODE = 0` to reset. Don't remove that line. |
| `Stop-Service`/`Start-Service` says "Cannot find any service" | The `SERVICE_NAME` env var doesn't match a registered service. Run `Get-Service Kala*` on the server to list KALA services |
| `Access denied` when copying the new exe | Service didn't fully release its file lock before the copy. Bump the wait loop after `Stop-Service`, or check that no orphaned exe processes are running (`Get-Process KalaCorelytics`) |
| Service starts then stops within seconds (after deploy) | New exe crashes at startup. Tail `logs\<exe>-svc.err.log`. Common causes: missing `.env`, MSVC redistributable missing on server, port already in use |
| `Restart-WebAppPool` fails: "value cannot be null" | The `APP_POOL` name doesn't exist. Check IIS Manager for the actual app pool name |
| Workflow uses old code after merge | The runner cached the workspace. Add `Remove-Item ${env:RUNNER_WORKSPACE} -Recurse -Force` as a cleanup step, or just trust the next `actions/checkout@v4` to clear it |
| `pip install` fails during the build step | Runner user doesn't have write access to its workspace. Default `LOCAL SYSTEM` works; if you switched users, `icacls` the runner workspace |
| `pyinstaller` not found | Build step skipped the `pip install pyinstaller` line. Verify Template B's `Build exe with PyInstaller` step |
| Self-hosted runner shows "online" but jobs queue forever | Repo's runner is at the org level; workflow can't reach it. Match scope: org-level runner ↔ workflows in repos under that org; repo-level runner ↔ that one repo only |
| Health check times out (after deploy) | Backend hasn't finished startup. The template already retries for 30s; if your service routinely takes longer, hit `/api/ready` (which waits for Mongo + downstream) instead of `/api/health` |
| `Resource not accessible by integration` | Workflow lacks `permissions:` block. Add at top: `permissions: { contents: read }` (or write if it pushes tags) |
| Workflow runs OK manually but fails on push | Branch protection or paths filter is gating it. Check the `on:` trigger and any `if:` conditions |
| First deploy of a backend fails at `python -m venv` | Runner can't find Python. The `actions/setup-python@v5` step in Template B installs the right version per workflow run; if you removed it, restore it |

If the runner itself is acting up, restart it cleanly:

```powershell
Set-Location D:\actions-runner
.\svc.cmd stop
.\svc.cmd start
```

Or fully unregister and reinstall:

```powershell
.\svc.cmd uninstall
.\config.cmd remove --token <token>     # generate a removal token in GitHub
# then re-run §4.2 from scratch
```

---

## 11. FAQ

**Q. Why one runner for all repos? Should each repo have its own?**
One runner is simpler. Multiple workflows queue on it cleanly and concurrency rules prevent collisions. Add a second runner only when concurrency becomes a real bottleneck (e.g., builds take long enough that queueing impacts deploy times).

**Q. Can the runner be on a different machine than the deploy target?**
Yes, but you lose the "no-network-deploy" benefit. The runner would need to push artifacts to the deploy target via SMB / WinRM / SSH - adds firewall rules, credentials, complexity. Keep the runner on the deploy server unless you have a strong reason.

**Q. What if the runner machine reboots mid-deploy?**
The workflow fails. GitHub doesn't auto-resume; the next push triggers a fresh run. For zero-downtime guarantees, look at horizontal scaling (multiple runners + a real load balancer) - overkill for KALA's current scale.

**Q. Can I run the workflow locally before pushing?**
Use [`act`](https://github.com/nektos/act) - runs GitHub Actions locally in Docker. Useful for iterating on workflow YAML without push spam. Not perfect (some `runs-on: self-hosted` configs don't translate cleanly), but good for syntax checks.

**Q. What permissions does the workflow need?**
Default permissions for self-hosted runners are read-only `contents`. If you want the workflow to push tags / create releases, add at the top:
```yaml
permissions:
  contents: write
```

**Q. How do I trigger a deploy manually (re-deploy without a code change)?**
The `workflow_dispatch:` trigger in the templates lets you click "Run workflow" in the GitHub Actions UI and pick a branch. Add input parameters if you want to pass a specific commit SHA or environment.

**Q. Can I deploy to multiple environments in one workflow?**
Yes - use a job matrix:
```yaml
jobs:
  deploy:
    strategy:
      matrix:
        env: [staging, production]
    runs-on: [self-hosted, Windows, "kala-${{ matrix.env }}"]
```
Each matrix value spawns a parallel job on a different runner.

**Q. What's the difference between `runs-on: self-hosted` and `runs-on: [self-hosted, X64]`?**
The first matches any self-hosted runner. The second requires both `self-hosted` AND `X64` labels. With multiple runners (Windows + Linux + ARM), more specific labels prevent the wrong runner from picking up a job.

**Q. We have a private package on a private npm registry. How does the runner authenticate?**
Add an `.npmrc` step:
```yaml
- run: |
    "//npm.pkg.github.com/:_authToken=${{ secrets.NPM_TOKEN }}" |
      Out-File -Encoding ASCII -Append .npmrc
```
Then `npm ci` reads `NPM_TOKEN` from GitHub Secrets.

**Q. What about Dependabot / Renovate?**
Both work fine with self-hosted runners. They open PRs that update dependencies, and your existing CI workflow runs on the PR (not just main). Just make sure your `on:` trigger includes `pull_request:` if you want CI on PRs.

**Q. Can I skip the deploy on certain commits?**
Add `[skip ci]` to the commit message - GitHub Actions honors this convention and won't trigger workflows.

---

## 12. Document history

| Date | Change | By |
|---|---|---|
| 2026-05-04 | Initial version. Self-hosted runner setup + 3 workflow templates (federation remote, backend, shell) + rollout sequence + troubleshooting. | Platform Architecture |
| 2026-05-08 | Replaced backend Template B (NSSM + venv + pip-install) with PyInstaller-build + WinSW-managed exe-swap deploy. Server requires no Python. | Platform Architecture |
