# KALA AI Platform — Auth Contract

**Audience:** Engineers building or integrating any KALA frontend or backend that participates in the platform's single-sign-on flow — the shell, every remote (Corelytics, Nexora, Saarthi, Interviewer), and every backend service.

**Status:** Active — new code follows this contract; existing code migrates opportunistically (see §13 for compatibility shims).

**Owner:** Platform Architecture team. Changes require sign-off because every app + service depends on this.

**Companion docs:**
- [`version-standardization.md`](./version-standardization.md) — library versions
- [`federation-cookbook.md`](./federation-cookbook.md) — frontend integration
- [`backend-standardizations.md`](./backend-standardizations.md) — backend HTTP conventions

---

## 1. Purpose

The KALA platform is a federation of independent apps with a single user identity. A user logs in **once at the shell**; every embedded remote and every downstream backend service trusts that identity without re-asking for credentials.

This document defines:

1. The canonical **User object** shape the platform passes around
2. The **handoff mechanisms** by which the shell delivers identity to remotes
3. The **token format** (JWT) that backends validate
4. The **authorization rules** for per-app admin checks
5. The **lifecycle** of login, refresh, and logout

Every other auth question (which header to use, what the user object looks like, who validates JWTs) is derived from this contract. Don't invent variations.

---

## 2. Scope

**In scope:**
- User identity propagation between shell, remotes, and backend services
- Session lifetime, expiry, and refresh
- Per-app admin authorization (`adminScope`)
- Logout semantics across all components

**Out of scope:**
- Initial credential validation (username/password) — handled by the main app's `/api/auth/login` endpoint, internal implementation detail
- 2FA / SSO with external IDPs — future work; this doc will be amended
- Service-to-service authentication (services calling other services) — currently done via shared `JWT_SECRET`; documented in [`backend-standardizations.md`](./backend-standardizations.md#4-auth-contract)

---

## 3. The User object — canonical shape

Every component of the platform stores and passes the user as an object with these fields. **No new fields without amending this doc.**

```ts
type User = {
  email:       string                          // unique identifier; primary key
  displayName: string                          // human-readable name shown in UI
  role:        'admin' | 'user'                // legacy field; prefer adminScope checks
  adminScope:  Array<'all' | AppId>            // admin power; see §6
  access:      Array<AppId>                    // which apps the user can launch
  level?:      'W' | 'S' | 'M' | 'E' | 'BOD'   // hierarchy level (optional)
  designation?: string
  team?:       string
  department?: string
}

type AppId = 'corelytics' | 'kala-coach' | 'nexora' | 'kala-interviewer'
```

**Notes:**

- `email` is the primary key. Two records with the same email = same user.
- `role` is kept for backward compatibility. New code should ignore it and use `adminScope`.
- `adminScope = ['all']` ⇒ super-admin (all current admin privileges in the main app).
- `adminScope = ['corelytics', 'nexora']` ⇒ app-admin for Corelytics + Nexora only; no main-app admin powers.
- `adminScope = []` ⇒ regular user.
- `access` controls which app cards are clickable on the landing page; super-admins (`'all' in adminScope`) implicitly have access to everything.

The canonical app IDs are fixed (`corelytics`, `kala-coach`, `nexora`, `kala-interviewer`). Adding a new app requires updating this list in this doc, the auth backend, and every existing service that does access checks.

---

## 4. Auth flow

```
                ┌──────────┐
                │  user    │
                └────┬─────┘
                     │
                     │ 1. POST /api/auth/login (email, password)
                     ▼
                ┌────────────┐                    ┌──────────────┐
                │   Shell    │ ─── 2. validate ─→ │ Auth backend │
                │  frontend  │                    │ (Mongo: user)│
                │            │ ←── 3. User obj ── │              │
                └────┬───────┘    + JWT signed    └──────────────┘
                     │
                     │ 4. Stores in sessionStorage + sets cookie
                     │ 5. Renders landing page with app tiles
                     │
                     │ 6. user clicks 'Open Corelytics'
                     ▼
                ┌────────────────────────────────┐
                │   Shell loads remote            │
                │   /corelytics?email=...&        │
                │     role=...&adminScope=...&    │
                │     appAdmin=true&              │
                │     autoLogin=true              │
                │                                 │
                │   …or postMessage(AUTH_USER)…   │
                │   …or shared sessionStorage     │
                └────┬───────────────────────────┘
                     │
                     │ 7. Remote populates AuthContext
                     ▼
                ┌──────────────┐
                │ Remote (e.g.,│
                │ Corelytics)  │ ─── 8. fetch /api/data ──→  ┌──────────────┐
                │              │     Cookie: kala_session=…   │  Corelytics  │
                │              │     Authorization: Bearer …  │   backend    │
                │              │                              │              │
                │              │ ←── 9. JSON response ─────── │ validates JWT│
                └──────────────┘                              └──────────────┘
```

The numbered steps:

1. **User logs in** at the shell (only place the password is ever entered).
2. Shell calls **`POST /api/auth/login`** on the auth backend with credentials.
3. Backend validates against Mongo's `login_credentials` collection, returns `{ User, jwt, sessionId }`.
4. Shell **persists**: `sessionStorage.setItem('auth_user', JSON.stringify(user))`, plus the auth backend sets `Set-Cookie: kala_session=<id>; HttpOnly; SameSite=Lax`.
5. Shell **renders** the landing page (4 app tiles).
6. User clicks an app → shell **launches the remote** with the User attached (one of the methods in §5).
7. Remote **reads identity** and populates its local `AuthContext` (no login screen displayed).
8. Remote makes API calls to its backend; **session cookie** rides along automatically; **JWT** is added to the `Authorization` header for cross-domain calls.
9. Backend **validates** the cookie/JWT and serves the request.

---

## 5. Identity handoff — shell to remote

The shell delivers the User to a remote via one of three mechanisms, in priority order:

### 5.1 URL params (primary; works everywhere)

When the shell navigates to (or opens an iframe of) a remote:

```
/corelytics?email=user@kalagroup.com
           &displayName=User%20Name
           &role=admin
           &adminScope=all
           &appAdmin=true
           &access=corelytics,kala-coach,nexora
           &autoLogin=true
```

**Required params:** `email`, `displayName`, `role`, `adminScope`, `appAdmin`, `autoLogin=true`

**Optional params:** `access`, `level`, `designation`, `team`, `department`

The remote reads these on first render and populates its `AuthContext`. See §9.2 for code.

### 5.2 `postMessage` (for iframe deployments)

After embedding a remote in an iframe, the shell `postMessage`s the User object to it:

```js
iframeRef.current.contentWindow.postMessage(
  {
    type: 'AUTH_USER',
    user: { email, displayName, role, adminScope, appAdmin, access }
  },
  '*'
)
```

This is sent **multiple times** with delays (immediately, after 500ms, after 1500ms) because the iframe may not have its `message` listener attached yet on first paint. The current `NexoraPage.jsx` and `SalesTrainingPage.jsx` already use this pattern.

### 5.3 Shared `sessionStorage` (when same origin)

When the shell and remote run on the same domain, the remote can read directly:

```js
const user = JSON.parse(sessionStorage.getItem('auth_user') || 'null')
```

Use this as a fallback when URL params and postMessage haven't fired (e.g., the user opens the remote URL directly in a new tab during dev).

### 5.4 Resolution priority

A remote should pick the first available source, in this order:

1. URL params with `autoLogin=true` (most explicit; per-launch)
2. `postMessage` AUTH_USER event (subsequent updates from shell — e.g., user data refreshed)
3. `sessionStorage` (fallback for direct opens)
4. Nothing → render an empty state ("Open me from the KALA shell") — **never** show a login form on a remote

---

## 6. Authorization — `adminScope`

### 6.1 The model

`adminScope: Array<'all' | AppId>` controls platform-wide admin privileges.

| `adminScope` value | What it grants |
|---|---|
| `['all']` | Super admin: all current main-app admin privileges (user management, login history, all per-app admin powers). |
| `['corelytics']` | App admin for Corelytics only. Cannot manage users in the main app, cannot see logs. |
| `['corelytics', 'nexora']` | App admin for Corelytics + Nexora. Can do per-app admin actions in those two apps. |
| `[]` (or absent) | Regular user. Can launch apps based on `access` array; no admin powers anywhere. |

### 6.2 The `appAdmin` flag (resolved per-call)

When the shell launches a remote, it pre-resolves the boolean `appAdmin` for *that specific app* and includes it in the URL params:

```js
const appAdmin = adminScope.includes('all') || adminScope.includes(thisAppId)
```

The remote uses this flag directly without re-checking. It's pre-computed for a reason — the shell knows which app it's launching, the remote shouldn't have to.

### 6.3 The check in code (frontend)

```jsx
const { user } = useAuth()
const canEditConfig = !!user?.appAdmin    // resolved by shell, trust it
```

Do **not** re-derive from `adminScope` on the remote frontend. The shell already did. Mismatches cause confusing UX (admin button appears, then 403s on click).

### 6.4 The check in code (backend)

Backends **must** re-derive from `adminScope`. The frontend can be tampered with; the backend is the source of truth.

```python
def is_app_admin(user: dict, app_id: str) -> bool:
    scope = user.get('adminScope') or []
    return 'all' in scope or app_id in scope

# In a route handler:
if not is_app_admin(current_user(), 'corelytics'):
    return error_response('forbidden', 'Admin role required for this action', 403)
```

Never trust an `appAdmin` flag from the request body or query string — always re-derive server-side.

### 6.5 Restricted actions

Some actions are **always** super-admin-only, regardless of which app the user is admin of:

- Adding/removing users (main app)
- Viewing login history (main app)
- Viewing platform-wide audit logs

These are gated by `adminScope.includes('all')`, not by `appAdmin`.

---

## 7. JWT specification

Used for cross-domain backend calls (where the session cookie can't ride along).

### 7.1 Algorithm and signing

- **Algorithm:** `HS256` (HMAC SHA-256)
- **Secret:** the env var `JWT_SECRET`, identical across the auth backend and every consumer service
- **Issuer:** the auth backend (`iss: 'kala-auth'`)

### 7.2 Claims

```json
{
  "iss":         "kala-auth",
  "sub":         "user@kalagroup.com",
  "iat":         1733000000,
  "exp":         1733003600,
  "displayName": "User Name",
  "role":        "admin",
  "adminScope":  ["all"],
  "access":      ["corelytics", "kala-coach", "nexora", "kala-interviewer"]
}
```

**Required claims:** `iss`, `sub`, `iat`, `exp`, `adminScope`
**Recommended:** `displayName`, `role`, `access`

### 7.3 Lifetime

- **Access JWT:** 1 hour (`exp - iat = 3600`)
- **Refresh token:** opaque, 30 days, stored as HttpOnly cookie
- Backends should **reject** tokens older than 1 hour. The shell silently refreshes via the refresh token (see §8).

### 7.4 Validation rules

Every backend validates each incoming JWT:

1. **Signature** — verify against shared `JWT_SECRET`
2. **`exp`** — reject if expired
3. **`iss`** — must equal `'kala-auth'`
4. **`sub`** — must be a non-empty string

If any fail → 401 with `error.type: 'auth_required'`.

---

## 8. Session lifecycle

### 8.1 Login

1. Shell `POST /api/auth/login` with `{ email, password }`
2. Auth backend returns:
   - User object in body
   - JWT in `body.jwt`
   - Refresh token via `Set-Cookie: kala_refresh=<opaque>; HttpOnly; SameSite=Lax; Path=/api/auth; Max-Age=2592000`
   - Session cookie via `Set-Cookie: kala_session=<id>; HttpOnly; SameSite=Lax; Max-Age=3600`
3. Shell stores the User in `sessionStorage` for cross-tab sharing within the same origin

### 8.2 Active session

- **Each backend call:** include `Authorization: Bearer <jwt>` header (cross-domain) AND/OR the cookies (same-domain).
- **JWT expires after 1 hour.** Frontend gets 401 on next call.
- On 401, frontend transparently calls `POST /api/auth/refresh` (refresh-token cookie rides along) → gets a new JWT → retries the original call.
- If refresh fails → user is logged out, redirected to login.

### 8.3 Logout

1. Shell calls `POST /api/auth/logout`
2. Auth backend invalidates the session: deletes `kala_session` doc, blacklists the refresh token
3. Auth backend sends `Set-Cookie: kala_session=; Max-Age=0` and the same for `kala_refresh` (browser deletes them)
4. Shell removes `sessionStorage.auth_user`
5. Shell broadcasts logout to all open remote iframes via `postMessage({ type: 'LOGOUT' })`
6. Each remote clears its `AuthContext` and shows the "open me from the shell" empty state (NOT a login form)
7. Shell navigates to `/login`

### 8.4 Cross-tab logout

When the user logs out in one tab, all other tabs of the same origin should also log out. Achieve via the `storage` event:

```js
window.addEventListener('storage', e => {
  if (e.key === 'auth_user' && e.newValue === null) {
    setUser(null)
    navigate('/login')
  }
})
```

Logging out clears `sessionStorage.auth_user`, which fires `storage` events in every other tab.

---

## 9. Implementation snippets

### 9.1 Shell — launching a remote with user attached

```jsx
function launchRemote(appId, user) {
  const params = new URLSearchParams({
    email:       user.email,
    displayName: user.displayName,
    role:        user.role,
    adminScope:  (user.adminScope || []).join(','),
    appAdmin:    String(isAppAdmin(user, appId)),
    access:      (user.access || []).join(','),
    autoLogin:   'true',
  })
  navigate(`/${appId}?${params.toString()}`)
}
```

### 9.2 Remote frontend — receiving the user

```jsx
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

function useAuthHandshake() {
  const [user, setUser] = useState(null)
  const [searchParams] = useSearchParams()

  useEffect(() => {
    // 1. URL params (primary)
    if (searchParams.get('autoLogin') === 'true') {
      setUser({
        email:       searchParams.get('email'),
        displayName: searchParams.get('displayName'),
        role:        searchParams.get('role'),
        adminScope:  (searchParams.get('adminScope') || '').split(',').filter(Boolean),
        appAdmin:    searchParams.get('appAdmin') === 'true',
        access:      (searchParams.get('access') || '').split(',').filter(Boolean),
      })
      return
    }

    // 2. postMessage (fallback / refresh)
    function handleMessage(e) {
      if (e.data?.type === 'AUTH_USER' && e.data.user) {
        setUser(e.data.user)
      } else if (e.data?.type === 'LOGOUT') {
        setUser(null)
      }
    }
    window.addEventListener('message', handleMessage)

    // 3. sessionStorage (last resort)
    const stored = sessionStorage.getItem('auth_user')
    if (stored) {
      try { setUser(JSON.parse(stored)) } catch { /* ignore */ }
    }

    return () => window.removeEventListener('message', handleMessage)
  }, [searchParams])

  return user
}
```

### 9.3 Backend (Flask) — validating an incoming request

```python
import jwt, os
from functools import wraps
from flask import request, g

JWT_SECRET = os.environ['JWT_SECRET']

def auth_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        token = None
        # 1. Authorization: Bearer ...
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]
        # 2. session cookie
        elif request.cookies.get('kala_session'):
            user = get_user_from_session(request.cookies['kala_session'])
            if user:
                g.user = user
                return f(*args, **kwargs)
        if not token:
            return {'error': {'type': 'auth_required', 'message': 'No auth provided'}}, 401
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'], issuer='kala-auth')
        except jwt.PyJWTError as e:
            return {'error': {'type': 'auth_required', 'message': str(e)}}, 401
        g.user = {
            'email':       payload['sub'],
            'displayName': payload.get('displayName', ''),
            'role':        payload.get('role', 'user'),
            'adminScope':  payload.get('adminScope', []),
            'access':      payload.get('access', []),
        }
        return f(*args, **kwargs)
    return wrapped

def app_admin_required(app_id):
    def decorator(f):
        @wraps(f)
        @auth_required
        def wrapped(*args, **kwargs):
            scope = g.user.get('adminScope') or []
            if 'all' not in scope and app_id not in scope:
                return {'error': {'type': 'forbidden', 'message': f'Admin role for {app_id} required'}}, 403
            return f(*args, **kwargs)
        return wrapped
    return decorator
```

### 9.4 Backend (FastAPI) — same logic

```python
from fastapi import Depends, HTTPException, Header, Cookie
import jwt, os

JWT_SECRET = os.environ['JWT_SECRET']

async def current_user(
    authorization: str | None = Header(None),
    kala_session:  str | None = Cookie(None),
) -> dict:
    if authorization and authorization.startswith('Bearer '):
        try:
            payload = jwt.decode(authorization[7:], JWT_SECRET, algorithms=['HS256'], issuer='kala-auth')
            return {
                'email':       payload['sub'],
                'displayName': payload.get('displayName', ''),
                'role':        payload.get('role', 'user'),
                'adminScope':  payload.get('adminScope', []),
                'access':      payload.get('access', []),
            }
        except jwt.PyJWTError as e:
            raise HTTPException(status_code=401, detail={'type': 'auth_required', 'message': str(e)})
    if kala_session:
        user = await get_user_from_session(kala_session)
        if user:
            return user
    raise HTTPException(status_code=401, detail={'type': 'auth_required', 'message': 'No auth provided'})

def app_admin_required(app_id: str):
    def dep(user: dict = Depends(current_user)) -> dict:
        scope = user.get('adminScope') or []
        if 'all' not in scope and app_id not in scope:
            raise HTTPException(status_code=403, detail={'type': 'forbidden', 'message': f'Admin role for {app_id} required'})
        return user
    return dep

# Usage:
# @app.post('/api/admin/whatever')
# async def admin_route(user = Depends(app_admin_required('interviewer'))):
#     ...
```

---

## 10. Standalone-dev workflow

When a developer runs a remote on its own (`npm run dev` from the remote's repo, with no shell), the auth handshake won't fire because no params arrive and no `postMessage` happens.

Two acceptable workarounds:

**A. URL params manually**
```
http://localhost:5176/?email=admin@kalagroup.com&role=admin&adminScope=all&appAdmin=true&autoLogin=true
```

**B. Dev-only env var**

In the remote's `.env.development`:
```
VITE_DEV_USER={"email":"admin@kalagroup.com","displayName":"Dev Admin","role":"admin","adminScope":["all"],"appAdmin":true,"access":["corelytics","kala-coach","nexora","kala-interviewer"]}
```

The remote's `useAuthHandshake` checks `import.meta.env.VITE_DEV_USER` as a *last* fallback (after URL/postMessage/sessionStorage). Production builds drop the env var; this only works in dev.

Document the convention in the remote's README.

---

## 11. Cross-app navigation while staying logged in

A user in Corelytics clicks "Open Saarthi" — what happens?

1. Corelytics calls `navigate('/kala-coach')` via React Router (the shell's router catches it)
2. Shell unmounts Corelytics, mounts Saarthi
3. Shell re-launches Saarthi with the same URL params (User-attached) — see §9.1
4. Saarthi reads the params and renders its own AuthContext
5. No re-login; same User everywhere

Because all remotes share the shell's router, cross-app navigation is just a `navigate()` call — no full page reload, no token re-fetch.

---

## 12. Migration from existing auth

The platform's existing apps each have variations of this contract today:

| App | Today's mechanism | Migration step |
|---|---|---|
| Main app (`dynamic_dashboard`) | Custom `AuthContext` reading from `sessionStorage`; backend validates via `/api/auth/login` | Update backend to issue JWTs alongside the User on login. Frontend already correct. |
| Corelytics (newly extracted) | Same `AuthContext` as main app | Migrate first — copies main app's flow exactly. |
| Nexora (`NexoraPage.jsx`) | URL params + postMessage to iframe | Already conformant for params. Add JWT to the param set so its backend can validate. |
| Saarthi (`SalesTrainingPage.jsx`) | Same as Nexora | Same migration. |
| Interviewer Bot | Currently no auth integration with shell | Implement §9.2 + §9.4 from this doc. |

Migration order: auth backend first (issues JWTs), then each remote backend (validates JWTs), then each remote frontend (consumes the new param). Old apps that don't yet validate JWTs continue to work via the session-cookie path during the transition.

---

## 13. Backward compatibility

While migrating, both auth styles must coexist:

- A remote that doesn't yet validate JWTs falls back to session-cookie validation
- A remote backend that doesn't yet receive JWTs trusts URL-passed `email` (legacy) — **only in same-domain deployments where IIS guarantees no spoofing**
- Cross-domain calls without a JWT are rejected outright

When all remotes have migrated, set `JWT_REQUIRED=true` env var on every backend to disable the legacy fallback. This is announced as a coordinated cutover.

---

## 14. Validation checklist

Before merging any auth-related change, confirm:

- [ ] User object has the canonical fields (§3) — no new ones invented
- [ ] All app IDs come from the canonical list (§3) — no typos like `'corelytics-bot'`
- [ ] `appAdmin` flag is RESOLVED by the shell (not the remote)
- [ ] Backend re-derives `appAdmin` from `adminScope` (never trusts the request)
- [ ] 401 used for auth failure, 403 for permission failure
- [ ] JWT validated: signature + exp + iss + sub
- [ ] `JWT_SECRET` read from env var, never hardcoded
- [ ] Logout broadcasts to remotes via `postMessage` AND clears sessionStorage
- [ ] Cross-tab logout works (storage event listener)
- [ ] Standalone-dev workflow documented (§10) in remote's README
- [ ] No login form on any remote

---

## 15. FAQ

**Q. Can a remote refresh the JWT itself?**
No. Only the shell handles refresh — it owns the refresh-token cookie. The remote just fails on 401, the shell catches it (or the user navigates back), and the next call after refresh succeeds.

**Q. What if the shell's session expires while the user is deep in a remote?**
The remote's next backend call gets 401 → returned to the user as a non-fatal error → user clicks "back" → shell detects expired session → silent refresh OR redirect to login. Don't try to handle expiry inside the remote; bubble it up.

**Q. Why not OAuth / Auth0 / Cognito?**
Out of scope for now — the platform is small and internal. The current cookie/JWT approach with shared MongoDB works. A future migration to a proper IDP is captured in §2 (out of scope).

**Q. Why HS256 instead of RS256?**
HS256 is simpler — one shared secret. RS256 (asymmetric) is better when third parties need to validate tokens without knowing your signing key. KALA backends are all internal; HS256 is fine. Switch later if the threat model changes.

**Q. Can a user's `adminScope` change mid-session?**
Yes — when an admin updates a user's permissions in the user-management page, the change is persisted to MongoDB immediately, but the affected user's *current* JWT keeps its old scope until expiry (1 hour max). For instant revocation, the platform should validate the JWT against the live MongoDB record instead of trusting the JWT in isolation. Not currently implemented; trade-off to consider.

**Q. The standalone-dev `VITE_DEV_USER` is committed to git — is that secure?**
Only commit it in `.env.development`, never `.env.production`. The dev user has zero access to anything outside `localhost`. The user object is well-known and contains no real credentials.

**Q. I'm building a brand-new internal tool — can I skip this contract?**
If it's standalone (own domain, own users, no shell embedding) — yes, do whatever. The moment you want to be embedded in the shell or share users with KALA's user pool, you're back in this contract.

**Q. What about API tokens for non-browser clients (CI scripts, integrations)?**
Future work. Currently, scripts hit the auth backend's `/api/auth/login` directly and use the returned JWT. A proper API-token system (with revocable, longer-lived tokens) is a separate doc.

**Q. Can two apps share `kala_session` cookies on different subdomains?**
Yes if the cookie is set with `Domain=.kala.com`. Same-site cookies don't cross domains, so cross-domain SSO requires the JWT path (Bearer token).

---

## 16. Document history

| Date | Change | By |
|---|---|---|
| 2026-05-04 | Initial version. Defines User schema, URL params + postMessage + sessionStorage handoff, JWT spec, login/refresh/logout lifecycle. | Platform Architecture |
