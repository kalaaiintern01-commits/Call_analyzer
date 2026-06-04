# KALA AI Platform — Backend Standardizations

**Audience:** Backend engineers shipping services on the KALA platform — current Flask services (Corelytics, Nexora gateway, Saarthi) and current/future FastAPI services (Interviewer Bot, future ML services).

**Status:** Active — new services must conform; existing services migrate opportunistically.

**Owner:** Platform Architecture team.

**Companion docs:**
- [`version-standardization.md`](./version-standardization.md) — library versions
- [`federation-cookbook.md`](./federation-cookbook.md) — frontend integration

---

## 1. Purpose

KALA backends are written in multiple frameworks today (Flask + FastAPI) and that's allowed to continue. But the platform falls apart if two services answer "what's the user?" differently, log in incompatible formats, or expose health checks at different paths.

**This document defines the HTTP-level conventions every backend service must follow, regardless of Python framework.** It's a contract layered *above* the framework, so a service can change from Flask to FastAPI without breaking anything outside it.

The rule of thumb: **frameworks differ; conventions don't.**

---

## 2. Architectural principle

Standardize at the **HTTP layer**, not the Python layer.

✗ Don't try to share auth-decorator code between Flask and FastAPI services.
✓ Do define one auth-header format and let each framework implement it natively.

✗ Don't try to share an `errorhandler` mixin.
✓ Do define one JSON error shape and let each framework emit it via its own mechanism.

This means the same Python code typically gets written twice (once per framework) — but the *behavior the rest of the platform sees* is identical.

---

## 3. Health and readiness endpoints

Every service exposes the same two endpoints. They are uncached, unauthenticated, idempotent, and cheap.

### 3.1 `GET /api/health` — liveness

Returns `200 OK` with:

```json
{ "status": "ok", "service": "<service-name>", "version": "<git-sha-or-semver>" }
```

If the process is alive at all, this returns 200. It does **not** check downstream dependencies — that's readiness's job.

### 3.2 `GET /api/ready` — readiness

Returns `200 OK` if the service is ready to serve real traffic, `503 Service Unavailable` if not. Checks:

- MongoDB connection (ping)
- OpenRouter reachability (only if the service makes AI calls)
- Any other critical downstream

Response shape (regardless of status):

```json
{
  "status": "ready",
  "checks": {
    "mongodb":     { "status": "ok",   "latency_ms": 12 },
    "openrouter":  { "status": "ok",   "latency_ms": 240 },
    "disk":        { "status": "ok" }
  }
}
```

On failure:

```json
{
  "status": "not_ready",
  "checks": {
    "mongodb": { "status": "fail", "error": "ServerSelectionTimeoutError: ..." }
  }
}
```

### 3.3 Why two endpoints?

- IIS / WinSW / load balancers ping `/api/health` to know if the process is alive (restart if not).
- Frontends + ops dashboards hit `/api/ready` to know if it's safe to send real traffic.

Conflating the two leads to restart loops when MongoDB has a transient hiccup.

### 3.4 Implementation

**Flask:**
```python
@app.route('/api/health')
def health():
    return {'status': 'ok', 'service': 'corelytics', 'version': os.getenv('GIT_SHA', 'dev')}, 200

@app.route('/api/ready')
def ready():
    checks = {}
    try:
        get_db().command('ping')
        checks['mongodb'] = {'status': 'ok'}
    except Exception as e:
        checks['mongodb'] = {'status': 'fail', 'error': str(e)}
    overall = all(c['status'] == 'ok' for c in checks.values())
    return {'status': 'ready' if overall else 'not_ready', 'checks': checks}, (200 if overall else 503)
```

**FastAPI:**
```python
@app.get('/api/health')
async def health():
    return {'status': 'ok', 'service': 'interviewer', 'version': os.getenv('GIT_SHA', 'dev')}

@app.get('/api/ready')
async def ready():
    checks = {}
    try:
        await db.command('ping')
        checks['mongodb'] = {'status': 'ok'}
    except Exception as e:
        checks['mongodb'] = {'status': 'fail', 'error': str(e)}
    overall = all(c['status'] == 'ok' for c in checks.values())
    if not overall:
        return JSONResponse({'status': 'not_ready', 'checks': checks}, status_code=503)
    return {'status': 'ready', 'checks': checks}
```

---

## 4. Auth contract

### 4.1 Identity propagation

The shell handles login once. Downstream services receive the user identity via:

**Primary: shared session cookie** (when shell + service share a domain)

```
Cookie: kala_session=<opaque-id>
```

The session ID resolves to a user record in the shared MongoDB `login_credentials` collection. Each service reads the session via the same helper:

```python
# session lookup — same code in every service
def get_user_from_session(session_id: str) -> dict | None:
    record = sessions_collection.find_one({'session_id': session_id})
    if not record or record['expires_at'] < datetime.utcnow():
        return None
    return users_collection.find_one({'email': record['email']})
```

**Secondary: bearer token** (when shell and service are on different domains)

```
Authorization: Bearer <jwt>
```

JWT payload:

```json
{
  "sub": "user@kalagroup.com",
  "name": "User Name",
  "role": "admin",
  "adminScope": ["all"],
  "appAdmin_<service>": true,
  "iat": 1733000000,
  "exp": 1733003600
}
```

JWT is signed by the shell's auth backend with a shared secret available to all services via the env var `JWT_SECRET`.

### 4.2 What every backend must do

1. **Look for `Authorization: Bearer ...`** first. If present, validate JWT.
2. **Fall back to session cookie** if JWT is absent.
3. **Reject** with 401 if neither produces a valid user.

### 4.3 Authorization (per-app admin checks)

Each service knows its own ID (`corelytics`, `nexora`, `saarthi`, `interviewer`). It checks the user's `adminScope` array:

```python
def is_app_admin(user, app_id):
    scope = user.get('adminScope') or []
    return 'all' in scope or app_id in scope
```

A request that requires admin returns `403 Forbidden` if `is_app_admin(user, 'this-service-id')` is false. Never trust an `appAdmin` flag from the request — always re-derive from `adminScope`.

### 4.4 401 vs 403

- **401 Unauthorized** — no/invalid auth header. The frontend should redirect to login.
- **403 Forbidden** — auth was valid but user lacks permission for this resource. The frontend should show "access denied", not redirect.

Don't use 401 for permission failures. Don't use 403 for missing tokens.

---

## 5. Response shape

### 5.1 Success responses

Every successful response (2xx) returns a JSON object. **Never return bare strings, numbers, or arrays** at the root — always wrap.

Pattern:

```json
{ "data": <whatever-the-endpoint-returns> }
```

Examples:

```json
{ "data": { "id": 42, "name": "..." } }
{ "data": [ {...}, {...} ] }
{ "data": { "items": [...], "total": 247, "page": 1 } }
```

This keeps response shape extensible — adding fields like `data`/`meta`/`warnings` later is non-breaking.

> **Existing exception:** Many of our current Flask services return the payload directly (e.g., `jsonify(dashboard_config)`). Keep them as-is until a major version bump; new services follow the `{ data: ... }` convention from day one.

### 5.2 Error responses

Every 4xx/5xx response returns:

```json
{
  "error": {
    "type":    "<machine-readable-code>",
    "message": "<human-readable-string>",
    "detail":  "<optional, longer explanation or trace-id>",
    "field":   "<optional, when error is field-specific>"
  }
}
```

Common `type` values (use these exact strings):

| `type` | When |
|---|---|
| `auth_required` | 401 — no/invalid auth |
| `forbidden` | 403 — valid auth, insufficient permission |
| `not_found` | 404 |
| `validation_error` | 400 — malformed body or invalid field |
| `conflict` | 409 — already exists / version mismatch |
| `rate_limited` | 429 |
| `db_timeout` | 503 — downstream DB unreachable |
| `db_connection_error` | 503 |
| `ai_error` | 502 — OpenRouter/LLM failed |
| `internal_error` | 500 — unhandled |

Always include both `type` and `message`. `detail` is optional but useful for debugging (include trace IDs there).

### 5.3 Implementation

**Flask:**
```python
@app.errorhandler(Exception)
def handle_exception(e):
    return {
        'error': {
            'type': 'internal_error',
            'message': str(e),
            'detail': traceback.format_exc(),
        }
    }, 500
```

**FastAPI:**
```python
@app.exception_handler(Exception)
async def handle_exception(request, exc):
    return JSONResponse({
        'error': {
            'type': 'internal_error',
            'message': str(exc),
            'detail': traceback.format_exc(),
        }
    }, status_code=500)
```

For specific error types, raise a typed exception and convert in one place. Don't sprinkle `jsonify({'error': ...})` calls throughout route handlers — fat-fingered messages drift over time.

---

## 6. Logging format

### 6.1 Format

JSON Lines (one object per log line) — `stdout` only. WinSW/Docker/journald all capture stdout natively and handle rotation themselves, so the app should NEVER manage log files directly.

Required fields per line:

```json
{
  "timestamp":  "2026-05-04T08:42:31.123Z",
  "level":      "INFO",
  "service":    "corelytics",
  "request_id": "abc-123-def",
  "user":       "user@kalagroup.com",
  "method":     "POST",
  "path":       "/api/generate-dashboard-summary",
  "status":     200,
  "duration_ms": 412,
  "message":    "dashboard generated"
}
```

`request_id` is generated per-request (UUID4), echoed back in the response header `X-Request-Id`, and propagated to downstream services in their request headers. This makes cross-service tracing possible without a real distributed-tracing system.

### 6.2 Levels

- `DEBUG` — developer-only, off in prod
- `INFO` — every request gets one INFO line
- `WARNING` — degraded behaviour (retried, fell back, deprecation hits)
- `ERROR` — request failed; include the stack
- `CRITICAL` — service is unhealthy

### 6.3 What NOT to log

- Passwords, tokens, JWT bodies (log only the user email)
- Full request bodies on POST (too noisy; log size + content-type)
- Full response bodies (same)
- PII beyond the user's email and role

### 6.4 Implementation

**Flask:**
```python
import logging, json
class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            'timestamp': self.formatTime(record),
            'level': record.levelname,
            'service': 'corelytics',
            'message': record.getMessage(),
            **getattr(record, 'extra', {}),
        })
```

**FastAPI:** Use [`structlog`](https://www.structlog.org/) for clean JSON output, or the same Python `logging` module configured identically.

Either way, the **fields and field names** are the standard — not the implementation.

---

## 7. Configuration via environment variables

### 7.1 Naming conventions

All configuration through env vars. Never hardcode secrets, URLs, or feature flags.

| Var | Purpose | Notes |
|---|---|---|
| `PORT` | HTTP port the service listens on | Override the default per-service |
| `HOST` | Bind address (default `0.0.0.0`) | |
| `MONGODB_URI` | App-data Mongo URI | Per-service database |
| `USER_URI` | Auth Mongo URI | Shared across all services for SSO |
| `OPENROUTER_API_KEY` | LLM API key | |
| `JWT_SECRET` | JWT signing key | Shared across all services |
| `LOG_LEVEL` | DEBUG / INFO / WARNING / ERROR | Default INFO |
| `GIT_SHA` | Build commit SHA | Set by CI; surfaces in /api/health |
| `SERVICE_NAME` | The canonical name | `corelytics`, `nexora`, etc. |
| `FLASK_DEBUG` | Werkzeug reloader (Flask only) | **Never set to `1` in prod** |

### 7.2 Loading

`.env` file in the service's backend directory (gitignored), loaded via `python-dotenv` at startup. **Never** import os.environ at module-level — load first, then read:

```python
# At the top of run.py — before any app imports
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / '.env')

# Now safe to import the app
from app import create_app
app = create_app()
```

### 7.3 `.env.example` is mandatory

Every backend repo has a `.env.example` listing every env var with example values, committed to git. Without this, new contributors don't know what to set.

---

## 8. Port assignments

To avoid local-dev collisions, each service has a **canonical default port**. Override via `PORT` env var when needed.

| Service | Frontend | Backend |
|---|---|---|
| Main shell (dynamic_dashboard) | 5173 | 5001 |
| Corelytics | 5176 | 5101 |
| Nexora gateway | 5174 | 3500 |
| Saarthi (Sales Training) | 5175 | 5000 |
| Interviewer Bot | 5177 | 8001 |
| **Genset Call Analyzer** | **5178** | **8006** |
| Federation shell | 5173 | (none — frontend only) |
| Federation remote-nexora | 5174 | (n/a) |
| Federation remote-saarthi | 5175 | (n/a) |

> **Note (Genset Call Analyzer):** Federation remote (`name: 'call-center'`,
> mounted by the shell at `/call-center/*`). Vite dev server on 5178 (chosen
> as the next free slot after Interviewer Bot's 5177 — its earlier 5173 dev
> port collided with the shell). FastAPI/uvicorn backend on 8006
> (`backend/main.py`). Standalone dev still works at `localhost:5178/`; the
> federated build lands at `<host>/cdn/call-center/latest/` and reads identity from the
> shell via URL params per [`auth-contract.md`](./auth-contract.md).

Add this table to a new service's onboarding before claiming a port. Pick the next free 4-digit slot in the same range.

---

## 9. CORS

Backends serve API calls from the shell at `ai.kalapms.com` and from local dev at `localhost:5173`. Configure CORS to allow:

```python
# Flask
from flask_cors import CORS
CORS(app, origins=[
    'http://localhost:5173',
    'http://localhost:5176',
    'https://ai.kalapms.com',
    'https://staging.kala.com',
], supports_credentials=True)
```

```python
# FastAPI
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(CORSMiddleware,
    allow_origins=[
        'http://localhost:5173', 'http://localhost:5176',
        'https://ai.kalapms.com', 'https://staging.kala.com',
    ],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)
```

`supports_credentials=True` (Flask) / `allow_credentials=True` (FastAPI) is required for the session-cookie auth pattern. Don't use `*` for `allow_origins` when credentials are enabled — browsers reject the response.

---

## 10. Timeouts

### 10.1 Per-call timeouts

Every outbound HTTP call has an explicit timeout. No exceptions:

```python
requests.post(url, json=payload, timeout=240)   # not just `timeout=None`
```

### 10.2 Standard timeout values

| Call | Timeout |
|---|---|
| MongoDB ping (readiness) | 5s |
| MongoDB connect/server-selection | 10s |
| MongoDB socket | 30s |
| OpenRouter / LLM call | 240s (per call; some apps do multiple calls per request) |
| Service-to-service RPC | 30s default |
| Inbound request — IIS/ARR proxy | 600s for endpoints that orchestrate LLM calls; 60s default |

See the deployment runbook for IIS/ARR proxy timeout configuration — without bumping ARR, the 600s app-side timeout is meaningless.

---

## 11. Graceful shutdown

When WinSW signals shutdown (Ctrl+Break by default on Windows), the service should:

1. Stop accepting new connections
2. Finish in-flight requests
3. Close MongoDB connections cleanly
4. Exit with status 0

WinSW gives the app 30 seconds (configurable in the service XML's `<stoptimeout>`) before sending SIGKILL. Most WSGI/ASGI servers handle this automatically — Waitress catches Ctrl+Break and drains, Uvicorn does the same. **Don't** trap signals manually to do custom cleanup unless absolutely necessary — it usually breaks more than it fixes. Stick with default Waitress / Uvicorn behavior.

If your shutdown is legitimately slow (e.g., flushing a large batch to disk), bump `<stoptimeout>` in the WinSW XML rather than racing the kill signal.

---

## 12. API versioning

When a backend's HTTP contract changes incompatibly, version the path:

```
/api/v1/generate-dashboard          ← original
/api/v2/generate-dashboard          ← new contract, runs alongside
```

Run both for at least one release cycle. Frontends migrate at their own pace; back-compat removal is a coordinated event (announced, then enforced).

For backward-compatible additions (new fields in response, new optional query params), no version bump needed.

---

## 13. Windows service naming

Backends ship as PyInstaller-built exes wrapped by WinSW (see [`deployment-runbook.md`](./deployment-runbook.md) §6). The Windows service name follows this exact pattern:

```
Kala<App>Backend
```

Examples:

- `KalaCorelyticsBackend`
- `KalaNexoraBackend`
- `KalaSaarthiBackend`
- `KalaInterviewerBackend`

This makes `Get-Service Kala<App>Backend` predictable across machines. The WinSW XML config in each repo sets `<id>` to match.

```xml
<!-- <App>-svc.xml -->
<service>
  <id>KalaCorelyticsBackend</id>
  <name>KALA Corelytics Backend</name>
  <executable>%BASE%\KalaCorelytics.exe</executable>
  ...
</service>
```

The exe filename and the service name don't have to match (the exe is `KalaCorelytics.exe`, the service is `KalaCorelyticsBackend`) — but they should be obviously related.

---

## 14. Validation checklist

Before merging a new backend service or a major refactor of an existing one, confirm:

- [ ] `GET /api/health` returns `{ status, service, version }` with status 200
- [ ] `GET /api/ready` checks downstream dependencies and returns 503 on failure
- [ ] Auth: respects `Authorization: Bearer ...` JWT first, falls back to session cookie
- [ ] Returns 401 for missing/invalid auth, 403 for permission denied (not the other way around)
- [ ] Error responses match the `{ error: { type, message, detail? } }` shape
- [ ] Success responses are JSON objects (never bare strings/arrays at root)
- [ ] Logs to stdout in JSON-Lines format with the required fields
- [ ] Every outbound HTTP call has an explicit `timeout=`
- [ ] CORS middleware configured with the four canonical origins (+ staging if applicable)
- [ ] `.env.example` lists every env var with sample values
- [ ] No `FLASK_DEBUG=1` in production; PyInstaller spec excludes any `app.run(debug=True)` entrypoint
- [ ] Windows service name is `Kala<App>Backend` (set in the WinSW XML's `<id>`)
- [ ] No secrets hardcoded
- [ ] PII redaction in logs (no passwords, tokens, full request bodies)

---

## 15. FAQ

**Q. We have a Flask service that already returns `jsonify(big_object)` directly without a `data:` wrapper. Do we have to refactor?**
No — keep it as-is. New services follow the wrapper convention; old services migrate at the next major-version bump. Mixing is fine because frontends know per-endpoint what to expect.

**Q. Why JSON-Lines logging instead of just `print()`?**
JSON-Lines is parseable. Once you ship to a real log aggregator (ELK, Loki, Datadog), every line becomes a queryable event with structured fields. `print()` outputs become opaque blobs and you'll regret it the first time prod has an incident.

**Q. Can my service have its own user store?**
No. All services share the auth Mongo collection via `USER_URI`. One user identity across the platform. If you need service-specific user metadata (e.g., per-user preferences for the Interviewer Bot), store it in your own collection keyed by email — not a separate auth pool.

**Q. What about gRPC / protobuf for service-to-service?**
Out of scope. KALA is HTTP/JSON end-to-end today. If a service has internal high-throughput needs, that's a per-service decision behind the public HTTP/JSON facade. The HTTP boundary is the platform contract.

**Q. Streaming / SSE — does the contract apply?**
Mostly yes. SSE responses are still HTTP and use the same auth + CORS rules. The success/error response shape doesn't apply mid-stream — emit your protocol's events — but the initial response should follow normal status code rules (2xx for opened, 4xx for rejected before stream starts).

**Q. WebSockets?**
Not currently in use. When a service adds WS, document its message envelope shape in this file (or a sibling doc). Standardize early.

**Q. We're adding a new service. What's the minimum we need before we can ship?**
1. `/api/health` and `/api/ready` working
2. JWT or session-cookie auth working against `USER_URI`
3. Error response shape conformant
4. CORS for the four origins
5. JSON-Lines logging to stdout
6. `.env.example` committed
7. Windows service name follows the `Kala<App>Backend` pattern (set in WinSW XML)
8. Validation checklist (§14) checked

Anything beyond that is per-service.

**Q. The Interviewer Bot uses FastAPI. Do all the snippets in this doc apply to it?**
Yes — every convention has a FastAPI implementation listed. The auth contract, error shape, logging format, port assignment, etc. all apply identically. The only difference is the Python code that implements them.

**Q. We discovered a violation in an existing service. Do we hot-fix or schedule it?**
If the violation breaks platform interop (wrong error type, wrong CORS origin, wrong port), hot-fix. If it's "we return `data:` on most endpoints but one returns the bare object", schedule it for the next sprint.

---

## 16. Document history

| Date | Change | By |
|---|---|---|
| 2026-05-04 | Initial version. Covers health/readiness, auth, response shape, logging, env vars, ports, CORS, timeouts, NSSM naming, API versioning. | Platform Architecture |
| 2026-05-08 | Updated §6 logging, §11 graceful shutdown, §13 service naming for the WinSW + PyInstaller exe deployment model. Service name pattern unchanged; how it's installed changed. | Platform Architecture |
