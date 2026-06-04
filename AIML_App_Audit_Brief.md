# AIML App Audit Brief

*Phase 1 Constitution audit — run against every AIML application. Aggregated from 27–28 May tasks.*

---

## 1. Why this exists

The AIML Constitution is being drafted to standardise how the department's applications are built, deployed, and operated. Before standards can be written, we need a clear read on **what each app already does, what's missing, and where apps diverge from each other.**

This brief is run **once per application** by the dev who knows that app. The reports are then aggregated. Where apps already converge, that pattern becomes the standard. Where apps diverge, the Constitution authors decide which way the dept will go.

### Tasks consolidated into this brief

| Date | Person | Original task | Constitution sections it feeds |
|---|---|---|---|
| 27 May | Sairaj | Audit existing infra docs | S4 / S5 / S9 |
| 27 May | Rutuj | Audit existing lint/coding configs | S6 |
| 28 May | Sairaj | Draft Environments + deployment method | S5.1 / S5.2 |
| 28 May | Rutuj | Draft Code + DB naming | S10.1 / S10.3 |

This is a **documentation audit**, not a code quality review. Tag every finding with the section it feeds.

---

## 2. Scope and assumptions

- One audit per application listed in the Constitution's S2.1 Application Inventory (Kala Horizon, Saarathi, AI Infra, and any other app the dept owns).
- Typical stack across the dept is Python (backend) + Node.js (frontend). The audit is written against that. If your app diverges, follow the spirit of each check and note the divergence in the report.
- Audit timebox: roughly half a day per app. One pass is enough — flag deep questions as follow-up actions, don't block on them.

---

## 3. App context block — fill this in first

Before you start, fill this in for your app. The auditor and the aggregator both need it.

| Field | Value |
|---|---|
| Application name | Genset Call Analyzer |
| Primary owner | Varun Bhilare |
| Secondary owner | *(unassigned)* |
| Tier (P0 / P1 / P2 / P3) | P2 — *proposed; revenue-adjacent voice agent, business hours, no PII-critical writes beyond capture* |
| Stage (Prod / Beta / Internal / Prototype) | Beta — deployed and taking real outbound calls, still iterating on prompt + reliability |
| Backend language + framework | Python 3.13 + FastAPI 0.115 + uvicorn 0.30 (Pipecat 1.1 voice pipeline) |
| Frontend language + framework | JavaScript (not TS) + React 18.3 + Vite 5.4 — standalone (not a federation remote) |
| Repo path or URL | `https://github.com/varunbhilare-balloon/genset-call-analyzer` |
| Audited on (DD-MMM-YYYY) | 28-May-2026 |
| Auditor | Varun Bhilare (self-audit) |
| Repo commit at audit time (short SHA) | `3f9078d` |

---

## 4. What to audit

### Area 1 — Infra docs (feeds S4 / S5 / S9)

**Look at:** `README.md`, `ARCHITECTURE.md`, `RUNBOOK.md`, `Dockerfile`, `docker-compose*.yml`, `.github/workflows/`, any `k8s/`, `helm/`, or `terraform/` directories, deploy scripts, `requirements.txt` / `pyproject.toml`, `package.json`.

**Check:**
- Tech stack documented and versions pinned (language versions, framework versions, DB, cache, LLM provider, container runtime, orchestrator)?
- Does the documented stack match what the code actually imports and the Dockerfile actually installs?
- Environments listed somewhere (local / dev / staging / prod)?
- CI/CD pipeline configured? Manual-deploy paths still possible?
- Semantic version tags being applied to prod releases?
- Rollback procedure documented anywhere?
- Secrets: where they live (vault / Secret Manager / env files), `.env.example` present, any real `.env` committed to git history?
- Dependency scanning or vulnerability response notes?

### Area 2 — Lint and coding configs (feeds S6)

**Look at:** `pyproject.toml`, `setup.cfg`, `.ruff.toml`, `mypy.ini`, `.flake8`, `.pre-commit-config.yaml`, `package.json` (`scripts`, `devDependencies`), `.eslintrc*`, `.prettierrc*`, `tsconfig.json`, CI workflow files.

**Check:**
- **Python:** ruff / black / mypy configured? Enforced in CI (not just locally)?
- **Node.js / TS:** eslint / prettier configured? Enforced in CI?
- **SQL** (if present): any formatter (e.g. sqlfluff) configured?
- Pre-commit hooks installed and matching CI rules?
- Test coverage threshold defined anywhere? Where is coverage measured?
- Spot-check: `print()` calls in backend production paths? `console.log` in frontend production paths? Commented-out blocks left in main?

### Area 3 — Environments and deployment method (feeds S5.1 + S5.2)

This is a capture task — write down the real state so Sairaj can draft the section.

**Capture:**
- Environments that actually exist, with URL patterns
- Which deploys are automatic vs. manual, and on what trigger (PR merge, push to main, tag push)
- Approval requirements for prod
- Rollback procedure: documented? estimated time to rollback? can it realistically complete in under 5 minutes?
- DB migration strategy: run separately from app deploy? backward-compatible across one version?
- Versioning scheme (semver? date-based? other?)
- Secrets loading at startup vs. baked into the image

### Area 4 — Code and DB naming (feeds S10.1 + S10.3)

Inspect representative samples — don't try to be exhaustive.

**Code (Python):**
- Module/file naming (snake_case consistent?)
- Function naming (snake_case?)
- Class naming (PascalCase?)
- Constants (UPPER_SNAKE in a constants module / config?)

**Code (Node.js / TS):**
- File naming (kebab-case vs. camelCase vs. PascalCase — consistent?)
- Function naming (camelCase?)
- Component / class naming (PascalCase?)
- Constants

**Database:**
- Table names (singular vs. plural, snake_case)
- Column names
- Primary key naming pattern
- Foreign key naming pattern
- Index naming pattern

Flag any place where the same kind of thing is named two different ways in the same codebase.

---

## 5. How to work through it

1. List the top-level repo contents first. Identify backend / frontend / infra directories.
2. Look at the actual code and config files before trusting any doc. Where a doc disagrees with code, the doc is the suspect.
3. Cite file paths (and line numbers where useful) for every specific finding. The section owners need to be able to verify in seconds.
4. If a check does not apply (e.g. no SQL, no k8s), say so explicitly. Do not skip silently — silence reads as "didn't check."
5. If you find a security issue in scope (committed secret, exposed credential, etc.), flag it in the summary and stop there. **Do not write the secret into the report.** Hand it to your app's primary owner out-of-band.

---

## 6. Report format

Produce one file named `[AppName]_Audit_Report_[DD-MMM-YYYY].md` with the structure below. Keep each finding to one or two lines plus a file path. Anyone reading this three months from now should be able to act on it without re-running the audit.

```markdown
# [App Name] — Constitution Audit Report

[App context block from Section 3, filled in]

## Summary
[3–5 lines. How much is in good shape, biggest gap, biggest risk, anything that blocks Constitution drafting on 28 May, any way this app diverges from the typical Python+Node.js shape.]

## Area 1 — Infra docs (S4 / S5 / S9)
Present:
- [item] — `path/to/file`
Missing:
- [item] — why it matters

Inconsistent (doc vs. reality):
- [item] — `path/to/doc` says X, `path/to/code` does Y

## Area 2 — Lint and coding configs (S6)
Python:
- Linter: [tool, configured? in CI?] — `path`
- Formatter: [...]
- Type checker: [...]
Node.js:
- Linter: [...]
- Formatter: [...]
- TS config: [...]
Pre-commit: [present / absent / partial]
Coverage: [threshold / current / not measured]
Spot-check findings:
- [...]

## Area 3 — Environments and deployment method (S5.1 / S5.2)
Environments observed:
| Env | URL pattern | Auto-deploy? | Approval |
|---|---|---|---|
| local | ... | n/a | n/a |
| dev | ... | ... | ... |
| staging | ... | ... | ... |
| prod | ... | ... | ... |

Deployment method: [CI/CD tool, pipeline file path]
Rollback: [documented Y/N, estimated time, path]
Version tagging: [scheme observed]
DB migrations: [tool, decoupled Y/N, backward-compat policy]
Secrets: [where they live, .env.example present Y/N, anything committed]

Gaps to fill before S5.1 / S5.2 can be drafted:
- [...]

## Area 4 — Code and DB naming (S10.1 / S10.3)
Python conventions observed:
- Modules / files: [...]
- Functions: [...]
- Classes: [...]
- Constants: [...]
- Exceptions / inconsistencies: [...]

Node.js / TS conventions observed:
- Files: [...]
- Functions: [...]
- Components / classes: [...]
- Exceptions / inconsistencies: [...]

Database conventions observed:
- Tables: [...]
- Columns: [...]
- Keys / indexes: [...]
- Exceptions / inconsistencies: [...]

## Top 3 actions
1. [action] — owner: [name]
2. [action] — owner: [name]
3. [action] — owner: [name]

## Out of scope / not applicable
- [item] — reason
```

---

## 7. What happens with the reports

All per-app reports go to **Sairaj** (infra / deployment / security findings) and **Rutuj** (lint / coding / naming findings) before EOD 28 May. They will:

1. Compile a **convergence table** across apps — for each check, mark which apps already match and which diverge.
2. Where ≥80% of apps already do the same thing, that becomes the proposed standard in the Constitution draft.
3. Where apps split, the divergence is raised at Wednesday Technical Review (3 June) for the dept to pick a direction.
4. Apps that diverge from the eventual standard get a follow-up ticket to align — not in this audit, in the Week 3 rollout.

Findings in this audit are not blame — they are inputs.

---

## 8. Notes for the dev running this

- One pass per app. If something needs deep investigation, log it as a follow-up action.
- If your app diverges from the Python + Node.js assumption (different language, different shape), follow the spirit of each check and note the divergence in the Summary.
- If anything in the repo contradicts the Phase 1 plan assumptions, surface it in the Summary so it gets raised at the next Monday Planning.
- Hand the finished report to Sairaj and Rutuj before EOD 28 May so their drafting tasks aren't blocked.

---

# Genset Call Analyzer — Constitution Audit Report

*Audit filled in inline per the brief above (commit `3f9078d`, 28-May-2026, auditor: Varun Bhilare).*

| Field | Value |
|---|---|
| Application name | Genset Call Analyzer |
| Primary owner | Varun Bhilare |
| Secondary owner | *(unassigned)* |
| Tier | P2 (proposed) |
| Stage | Beta — deployed, in production-style use |
| Backend | Python 3.13 / FastAPI 0.115 / uvicorn / Pipecat 1.1 voice agent |
| Frontend | React 18.3 + Vite 5.4 (JS, not TS), standalone |
| Repo | `github.com/varunbhilare-balloon/genset-call-analyzer` |
| Commit | `3f9078d` |

## Summary

About half the bones of a deploy-able app are in place: env-var hygiene is clean
(`.env` gitignored, `.env.example` exists, no real `.env` in history), the docs
folder was just standardised with an applicability index, and CI workflow
drafts now exist at [`.github/workflows/`](.github/workflows/). The biggest gap
is **code quality enforcement** — zero lint config (no ruff/black/mypy on
Python, no eslint/prettier on the frontend), zero tests, and 17 raw `print()`
calls in backend production paths even though `loguru` is in `requirements.txt`.
The biggest risk for Constitution drafting is **naming inconsistency at the DB
layer**: the legacy ERP table `dbo.Enquiry` uses PascalCase columns
(`EnqNo`, `CustName`), the new `dbo.AICallSummary` uses snake_case
(`call_uuid`, `customer_name`) — the same kind of thing is named two ways in
the same codebase. Divergences from the typical Python+Node.js shape: no
Docker/K8s/Terraform anywhere (deploy is direct-to-server via the new CI
workflows), frontend is plain JS (not TS), and the backend carries real-time
voice infra (Plivo media streams + Sarvam STT/TTS + Pipecat) that none of the
other dept apps have.

## Area 1 — Infra docs (S4 / S5 / S9)

Present:
- `README.md` — quick-start with Sarvam, Anthropic, Plivo, ngrok — `./README.md`
- `docs/README.md` — index + applicability matrix + app registry (added 27-May) — `./docs/README.md`
- `docs/` — 8 KALA platform reference docs (backend-standardizations, version-standardization, deployment-runbook, first_deploy, github-actions-integration, federation-cookbook, auth-contract, app-dev-guide)
- `backend/.env.example` — sanitised env template — `./backend/.env.example`
- `backend/requirements.txt` — pinned mix (`==` and `>=`) — `./backend/requirements.txt`
- `frontend/package.json` + `package-lock.json` — caret-ranged deps — `./frontend/package.json`
- `.github/workflows/deploy-backend.yml` + `deploy-frontend.yml` — added 27-May, draft, never run — `./.github/workflows/`
- `.gitignore` — covers `.env`, `*.pem`, `*.key`, `node_modules`, `__pycache__`, `catalogs/`, etc. — `./.gitignore`
- `RINGG_MIGRATION.md`, `KALA-design-guide.md`, `KNOWLEDGE_BASE.md`, `OPTIMISATIONS.md`, `deployment-plan.md`, `LOAD_STUDY_CALCULATIONS.md`, `DOCS.md` — informal design + planning notes at repo root

Missing:
- No `Dockerfile` / `docker-compose.yml` — app deploys without containers; matters if dept standardises on container builds
- No `k8s/` / `helm/` / `terraform/` — no IaC at all
- No per-app `RUNBOOK.md` or `ARCHITECTURE.md` (the `docs/deployment-runbook.md` is platform-generic, not app-specific) — matters because on-call need an app-scoped first-hit doc
- No `/api/ready` readiness endpoint despite `docs/backend-standardizations.md` §3 requiring it — matters for load balancer + deploy-time health gating
- No git tags / no semver applied — matters for rollback identification + release notes
- No dependency scanning (no `dependabot.yml`, no `pip-audit` / `npm audit` in CI) — matters for vuln response
- No secrets vault — secrets live in `.env` only — matters at scale / for shared deployments

Inconsistent (doc vs. reality):
- `docs/version-standardization.md` Tier-B requires Python deps pinned with `==`; `backend/requirements.txt` line 7-15 uses `>=` for `plivo`, `imageio-ffmpeg`, `pyodbc`, `pipecat-ai`, `pipecat-ai-small-webrtc-prebuilt`, `anthropic`, `deepgram-sdk`, `loguru` — only the first six are `==`. Same doc, ignored by this repo.
- `docs/version-standardization.md` Tier-1 requires `react@18.2.0` exact; `frontend/package.json` has `^18.3.1`. *(docs/README.md notes this app is standalone — Tier-1 doesn't strictly apply, but the divergence is real.)*
- `docs/backend-standardizations.md` §3 mandates `GET /api/ready`; `backend/main.py:530` has only `/api/health`.

## Area 2 — Lint and coding configs (S6)

Python:
- Linter (ruff / flake8): **NOT configured** — no `pyproject.toml`, no `setup.cfg`, no `.ruff.toml`, no `.flake8`
- Formatter (black): **NOT configured**
- Type checker (mypy): **NOT configured** — no `mypy.ini`
- CI enforcement: **NONE** — the new workflows don't run lint
- **Logging inconsistency**: `loguru` is in `requirements.txt` and Pipecat uses it, but `backend/main.py` uses raw `print()` for app-level logs (17 occurrences below)

Node.js / TS:
- Linter (eslint): **NOT configured** — no `.eslintrc*`
- Formatter (prettier): **NOT configured** — no `.prettierrc*`
- TS config: **N/A** — frontend is plain JS, no `tsconfig.json` / `jsconfig.json`
- CI enforcement: **NONE**

SQL: **N/A** — no `.sql` files in repo; queries live inline in `backend/erp.py`.

Pre-commit: **NOT configured** — no `.pre-commit-config.yaml`.

Coverage: **Not measured** — no `tests/` directory, no `test_*.py`, no `*.test.jsx`, no `*.spec.*` files anywhere. Coverage threshold undefined.

Spot-check findings:
- **17 `print()` calls** in backend production paths — all in `backend/main.py` (none in `backend/agent/`). Examples: `main.py:78` (`[startup] agent stack pre-warmed...`), `main.py:245` (`[save-lead] SQL persistence failed:...`), `main.py:307` (`[post-call extract] OPENROUTER_API_KEY missing...`), `main.py:394` (`[post-call extract] LLM call / JSON parse failed:...`). They're tagged with bracketed prefixes (better than raw debug) but still not structured. **Recommend migrating to `loguru` since it's already a dep.**
- **0 `console.log` / `console.warn` / `console.error`** in `frontend/src/` ✓
- No commented-out blocks left in main paths spot-checked.
- The two `backend/_pdf_dump.py` and `backend/_test_conn.py` files use leading-underscore filenames to flag "dev-only" — informal convention not used elsewhere; could move them under `backend/scripts/` or `backend/tools/` to be explicit.

## Area 3 — Environments and deployment method (S5.1 / S5.2)

Environments observed:

| Env | URL pattern | Auto-deploy? | Approval |
|---|---|---|---|
| local | `http://localhost:8000` (backend) + `http://localhost:5173` (frontend) | n/a | n/a |
| dev | *(not separated — local is the only non-prod env)* | n/a | n/a |
| staging | *(does not exist)* | n/a | n/a |
| prod | Server-deployed as of 28-May (URL not in repo); public ingress was `ocelot-boggle-retool.ngrok-free.dev` during dev | Push to `main` triggers `.github/workflows/deploy-{backend,frontend}.yml` (paths filtered to `backend/**` and `frontend/**`) | **None** — direct push to main with no PR review |

Deployment method:
- CI/CD: GitHub Actions workflows at `.github/workflows/deploy-backend.yml` and `deploy-frontend.yml`. Both target a self-hosted Windows runner (`runs-on: [self-hosted, Windows, kala-prod]`).
- Backend: deploy **from source** as a WinSW service (`KalaGensetBackend`) — NOT PyInstaller. Adapted from `docs/github-actions-integration.md` Template B because Pipecat + torch + silero + onnxruntime do not `--onefile` cleanly.
- Frontend: `npm ci` → `npm run build` → robocopy `dist/` to IIS site root (`D:\inetpub\wwwroot\kala\genset`) — standalone static deploy (NOT a federation remote).
- Both workflows have never executed against a real runner yet (added 27-May); they're drafts pending the WinSW service setup + IIS site config.

Rollback:
- Documented: **partial**. The CI workflows snapshot the prior build to `versions/v<sha>/` (backend) and `genset-versions/v<sha>/` (frontend) before each deploy, keeping the last 5. No top-level RUNBOOK explains how to invoke a rollback.
- Estimated time: <2 min for a manual `robocopy` of an old version back + `Restart-Service KalaGensetBackend`.
- Realistic completion in under 5 min: **Yes**, but only if someone knows where the snapshots live and the WinSW service name. Tribal knowledge today.

Version tagging: **None observed** — `git tag --list` is empty. `frontend/package.json` carries a `version` field but no tags push it as a release.

DB migrations:
- No migration tool (no Alembic / Flyway / Liquibase / no `migrations/` folder).
- Schema for `dbo.AICallSummary` (the app's write target) is created externally (not in the repo); `backend/erp.py` only runs `INSERT`/`UPDATE`/`SELECT`.
- `dbo.Enquiry` is a read-only legacy ERP table; this app does not own its schema.
- Backward-compat policy: **undefined**.

Secrets:
- Loaded at process startup from `backend/.env` via `python-dotenv` (`backend/main.py:36`).
- `backend/.env.example` present with sanitised placeholders ✓
- `.env` gitignored ✓; `git log --all --full-history -- '*.env'` returns empty — never committed to history ✓
- No vault / Azure Key Vault / AWS Secrets Manager — secrets are file-on-server.

Gaps to fill before S5.1 / S5.2 can be drafted:
- Define dev/staging in addition to local + prod (currently a two-environment shape).
- Document the rollback procedure end-to-end (where snapshots live, exact PowerShell to restore, who runs it).
- Pick a versioning scheme — `vMAJOR.MINOR.PATCH` semver on git tag with release notes is the dept default per Constitution draft direction.
- Decide on DB migration tooling — Alembic is the natural choice given SQLAlchemy adjacency, but this app uses raw `pyodbc`; even a `db/migrations/*.sql` ordered folder + a runner script would be enough.
- Move secrets behind a real store before multi-environment / multi-deploy.

## Area 4 — Code and DB naming (S10.1 / S10.3)

Python conventions observed:
- Modules / files: `snake_case` ✓ — `main.py`, `erp.py`, `agent/bot.py`, `agent/prompt.py`, `agent/products.py`
- Functions: `snake_case` ✓ — `build_pipeline`, `_save_agent_lead`, `_extract_lead_from_transcript`, `_is_female_name`
- Classes: `PascalCase` ✓ — `FillerAcknowledgmentProcessor` in `backend/agent/bot.py:59`
- Constants: `UPPER_SNAKE` ✓ — `SARVAM_API_KEY`, `OPENROUTER_API_KEY`, `PLIVO_AUTH_ID`, `PLIVO_AUTH_TOKEN`, `SERVER_BASE_URL`, `PLIVO_CALL_LANGUAGE`, `AGENT_MAPPING`, `CHUNK_MS`, `LANG_CONFIG`, `SYSTEM_PROMPT` (all in `backend/main.py`)
- Exceptions: the leading-underscore files `backend/_pdf_dump.py` + `backend/_test_conn.py` are an informal "dev-only" convention not used anywhere else; better moved under `backend/scripts/` or `backend/tools/`.

Node.js / JS conventions observed:
- Files: `App.jsx` (PascalCase for the app entry), `main.jsx` (Vite default lowercase entry), `index.css` (lowercase) — consistent with Vite/React community defaults, **not internally inconsistent**.
- Functions: `camelCase` ✓ — `formatFileSize`, `formatDateTime`, `formatDuration`, `statusBadgeClass`, `localizeAudioUrl` in `frontend/src/App.jsx`
- Components: `PascalCase` ✓ — `FieldRow`, `PipelineSteps`, `SummaryResults`, `UploadView`, `DialView`, `CallsView`, `CallCustomersView`, `SummaryView`, `App` (all in `frontend/src/App.jsx`)
- Constants: No module-level `UPPER_SNAKE` constants in JS files spot-checked; only local hooks state. Acceptable for this size.
- Exceptions: 0 — naming is internally consistent.

Database conventions observed:
- Tables:
  - `dbo.Enquiry` — **PascalCase singular** (legacy ERP table, this app is a read-only consumer)
  - `dbo.AICallSummary` — **PascalCase singular** (this app's write target — schema created externally)
- Columns:
  - `dbo.Enquiry`: **PascalCase** — `EnqNo`, `Dt`, `CustName`, `ContactPerson`, … (`backend/erp.py:215-220`)
  - `dbo.AICallSummary`: **snake_case** — `call_uuid`, `enq_no`, `from_number`, `to_number`, `is_outbound`, `customer_name`, `designation`, `company_name`, `business_type`, `location`, `purpose`, `new_or_replacement`, `capacity_kva`, `load_calculated`, `phase`, `fuel_type`, `amf_required`, `canopy_required`, `timeline`, `site_visit_ok`, `competitor_quotes_received`, `budget_range`, `callback_number`, `email`, `language_used`, `hot_lead`, `call_summary` (`backend/erp.py:394-402`)
- Keys / indexes:
  - `dbo.Enquiry` PK: `EnqNo` (PascalCase, looks like the natural PK)
  - `dbo.AICallSummary` PK: `id` (lowercase surrogate)
  - FK: `dbo.AICallSummary.enq_no` → `dbo.Enquiry.EnqNo` — case differs across the relationship
  - Index naming: not visible from code; would need a SQL Server schema dump
- **Exceptions / inconsistencies**:
  - Two tables in the same backend use **two different column casings** — this is the headline naming flag for S10.3. The split is explainable (Enquiry is legacy ERP, AICallSummary is new and follows modern Python ORM-adjacent naming), but if the Constitution picks one convention the new table is the only one this app can change.

## Top 3 actions

1. **Adopt Python + frontend lint configs and enforce them in CI.** Add `ruff` (lint + format) + optional `mypy` for Python with `pyproject.toml`; add `eslint` + `prettier` for the frontend. Wire both into a new `.github/workflows/lint.yml` that runs on PR + push. — owner: Varun Bhilare (with Rutuj on the standard config)
2. **Migrate `backend/main.py`'s 17 `print()` calls to `loguru`** (already a dep) and add `GET /api/ready` per `docs/backend-standardizations.md` §3 (also closes the deployment health-check gap). — owner: Varun Bhilare
3. **Resolve DB column naming** — keep `dbo.AICallSummary` on snake_case but document the divergence from `dbo.Enquiry` (legacy ERP, out of this app's control) in `docs/README.md` so the Constitution authors have the rationale. Add a migration tool (Alembic or ordered `db/migrations/*.sql`) so any future column changes are versioned. — owner: Varun Bhilare + DB owner of `call_analyzer` schema

## Out of scope / not applicable

- **Docker / Kubernetes / Helm / Terraform** — app deploys without containers, via CI workflows that run directly on a Windows self-hosted runner. Containerisation is a dept-level Constitution decision, not an app finding.
- **TypeScript conventions** — frontend is plain JS. If the Constitution mandates TS, this app needs a separate migration ticket, not an in-place fix.
- **Federation / Module-Federation Tier-1 React pin** — app is standalone, not embedded in the KALA shell. Documented in `docs/README.md` deviations.
- **End-user auth / SSO contract** — app has no end-user auth surface today (it's a phone-call agent + an internal-only dashboard reachable via ngrok / direct domain). `docs/auth-contract.md` does not apply.
- **TS config + SQL formatter** — no TS, no `.sql` files in repo; both checks N/A.

---

*Audit completed: 28-May-2026. Hand to Sairaj (infra/deployment/security) and Rutuj (lint/coding/naming) per Section 7.*
