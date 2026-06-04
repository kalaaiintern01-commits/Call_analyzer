# KALA AI Platform — Federation Cookbook

**Audience:** Engineers who own a KALA app repo (Corelytics, Nexora, Saarthi, Interviewer, future apps) and need to integrate it as a federated remote into the platform shell.

**Status:** Active — supersedes ad-hoc iframe integration patterns.

**Owner:** Platform Architecture team.

**Companion doc:** [`version-standardization.md`](./version-standardization.md) — pin the library versions referenced here before starting.

---

## 1. Purpose

The KALA platform is built on **Module Federation**: a single shell app loads each product (Corelytics, Nexora, Saarthi, Interviewer Bot) as a runtime remote. Each product team continues to:

- own their own Git repo
- run their own CI/CD pipeline
- ship at their own cadence

…while the user experiences a single, consistent platform — one login, one navigation shell, one set of design tokens.

This document is the **end-to-end recipe** for converting an existing standalone app (Vite/React) into a federation-ready remote, plus the integration steps on the shell side.

---

## 2. Architecture overview

```
            ┌───────────────────────────────────────────────────┐
            │              Browser  (single-page)               │
            │                                                   │
   ┌────────│  KALA Shell  (loaded once at https://app.kala.com)│
   │        │   ├── Login / Landing                             │
   │        │   ├── Auth state                                  │
   │        │   └── Router                                      │
   │        │         /corelytics    →  lazy import             │
   │        │         /nexora        →  lazy import             │
   │        │         /saarthi       →  lazy import             │
   │        │         /interviewer   →  lazy import             │
   │        └─────────────┬─────────────────────────────────────┘
   │                      │ runtime fetch
   │                      ▼
   │         ┌────────────────────────────────────────────────┐
   │         │  Remote manifests (one per app, on a CDN)      │
   │         │                                                │
   │         │  https://cdn.kala.com/corelytics/remoteEntry.js│
   │         │  https://cdn.kala.com/nexora/remoteEntry.js    │
   │         │  https://cdn.kala.com/saarthi/remoteEntry.js   │
   │         │  https://cdn.kala.com/interviewer/remoteEntry.js│
   │         └────────────────────────────────────────────────┘
   │
   │  Each manifest is built independently by its own repo's CI.
   │  The shell never sees the source code — only the URL.
   └─────────────────────────────────────────────────────────────
```

**Key properties:**

- **One copy of React.** The federation `shared` config dedupes React, ReactDOM, React Router across all apps.
- **No iframe.** Remotes mount inside the shell's React tree — same DOM, same theme, same auth context.
- **Independent deploys.** A remote team pushing to their `main` branch updates production without the shell redeploying.

---

## 3. Prerequisites

Before starting, your repo must be:

- A Vite + React app (Webpack apps need a different plugin — out of scope here)
- Using React 18.2 + React Router 6.22 (see [version standardization](./version-standardization.md) — Tier 1)
- Building successfully on its own (`npm run build` produces a working `dist/`)

---

## 4. Convert your app to a federated remote

This is the bulk of the work — once a repo is converted, it can ship as both a standalone app *and* a federated remote from the same source.

### 4.1 Install the federation plugin

```bash
npm install --save-dev @originjs/vite-plugin-federation
```

Pin to the platform version listed in version-standardization.md Tier 2 (currently `1.3.6`).

### 4.2 Update `vite.config.js`

Add the federation plugin in **remote** mode:

```js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

export default defineConfig({
  // CRITICAL — must match the URL path under which the build is served.
  // Examples:
  //   CDN at https://cdn.kala.com/nexora/    →  base: '/nexora/'
  //   Bucket root with no prefix             →  base: '/'
  //   Local preview at localhost:5174/       →  base: '/'
  base: '/nexora/',

  plugins: [
    react(),
    federation({
      // The name the shell uses to import this remote.
      // Must match the key in the shell's `remotes` config.
      name: 'nexora',

      // The manifest filename. Always 'remoteEntry.js' by convention.
      filename: 'remoteEntry.js',

      // What this remote exposes to the shell. Each entry becomes
      // an importable module: `import App from 'nexora/App'`.
      exposes: {
        './App': './src/App.jsx',

        // Optional: expose finer-grained pieces if the shell wants
        // to mount specific routes/widgets without the full app.
        // './widgets/RiskMeter': './src/widgets/RiskMeter.jsx',
      },

      // Libraries that the shell + this remote both load. Federation
      // ensures only ONE copy is loaded in the browser. If versions
      // mismatch, you'll see "invalid hook call" errors at runtime.
      shared: ['react', 'react-dom', 'react-router-dom'],
    }),
  ],

  build: {
    target: 'esnext',     // federation requires modern output
    modulePreload: false, // disable Vite's preload — federation handles it
    minify: false,        // optional; easier to debug remote bugs
    cssCodeSplit: false,  // keep CSS in a single file the shell can apply
  },

  server: {
    port: 5174,           // standalone dev port — pick something unused
    strictPort: true,
  },
  preview: {
    port: 5174,
    strictPort: true,
  },
})
```

### 4.3 Restructure the entry point

The exposed file (`src/App.jsx`) **must not** contain its own `<BrowserRouter>` — the shell already wraps everything in one. Conflicting routers break navigation silently.

**`src/App.jsx`** — the exposed component (router-less):

```jsx
import { Routes, Route } from 'react-router-dom'
import HomePage from './pages/HomePage'
import DetailPage from './pages/DetailPage'

// No <BrowserRouter> here — shell provides it.
// All routes are RELATIVE to the shell's mount point (e.g. /nexora).
export default function App() {
  return (
    <Routes>
      <Route index element={<HomePage />} />
      <Route path="detail/:id" element={<DetailPage />} />
    </Routes>
  )
}
```

**`src/main.jsx`** — only used for standalone `npm run dev`:

```jsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App.jsx'

// This file is the standalone entry — never executed when consumed
// via federation. It wraps the same App component in its own router
// so the team can `npm run dev` and develop in isolation.
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
)
```

This dual setup is the key idea: **same `App.jsx`, two ways to mount it.**

### 4.4 Set the `base` path correctly

`vite.config.js`'s `base` controls where the build expects its own assets to live. Get this wrong and the browser tries to fetch `https://cdn.kala.com/assets/index.js` instead of `https://cdn.kala.com/nexora/assets/index.js` and everything 404s.

| Deploy target | `base` value |
|---|---|
| `https://cdn.kala.com/nexora/` (subfolder on CDN) | `/nexora/` |
| `https://nexora.cdn.kala.com/` (own subdomain) | `/` |
| Standalone preview at `localhost:5174/` | `/` (use a separate `vite.preview.config.js`, or override) |

If your standalone dev/preview also needs to work, use Vite's mode-conditional config:

```js
export default defineConfig(({ command, mode }) => ({
  base: mode === 'production' ? '/nexora/' : '/',
  // … rest of config …
}))
```

### 4.5 Auth contract — how the shell passes user to remotes

The shell does login. Remotes never display a login screen. The user reaches the remote pre-authenticated, and the remote receives identity through one of:

#### 4.5.1 URL params (primary)

When the shell mounts a remote, it does `navigate(\`/nexora?email=...&role=...&autoLogin=true\`)`. Inside your `App.jsx`, read params on first render:

```jsx
import { useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useAuth } from './context/AuthContext'

export default function App() {
  const [searchParams] = useSearchParams()
  const { setUser } = useAuth()

  useEffect(() => {
    if (searchParams.get('autoLogin') === 'true') {
      setUser({
        email: searchParams.get('email'),
        displayName: searchParams.get('displayName'),
        role: searchParams.get('role'),
        appAdmin: searchParams.get('appAdmin') === 'true',
        adminScope: (searchParams.get('adminScope') || '').split(',').filter(Boolean),
      })
    }
  }, [searchParams, setUser])

  return /* routes */
}
```

#### 4.5.2 `postMessage` (backup, for iframe deployments)

Some legacy integrations still iframe the remote. Listen for the `AUTH_USER` event:

```jsx
useEffect(() => {
  function handleMessage(e) {
    if (e.data?.type === 'AUTH_USER') setUser(e.data.user)
  }
  window.addEventListener('message', handleMessage)
  return () => window.removeEventListener('message', handleMessage)
}, [setUser])
```

#### 4.5.3 Shared `sessionStorage` (when same domain)

If shell and remote run on the same origin (e.g. `app.kala.com`), the simplest auth handoff is just letting the remote read `sessionStorage.getItem('auth_user')` directly. The shell already wrote it during login.

#### 4.5.4 `appAdmin` and `adminScope`

The shell evaluates whether the user is an admin **for this specific app** and passes the resolved boolean as `appAdmin`. The remote should never re-check; just trust:

```jsx
const { user } = useAuth()
const isAdminHere = !!user?.appAdmin   // resolved by the shell, not by us
```

`adminScope` is a comma-joined list of app IDs the user administers globally — useful if your app needs to display "you are also admin of: corelytics, saarthi" badges, but for access control just use `appAdmin`.

### 4.6 `package.json` scripts

```json
{
  "name": "kala-nexora",
  "version": "1.4.2",
  "type": "module",
  "scripts": {
    "dev":     "vite",
    "build":   "vite build",
    "preview": "vite preview --port 5174 --strictPort"
  }
}
```

### 4.7 `.npmrc` — pin exact versions

Federation breaks on minor-version drift. Force exact pinning:

```
save-exact=true
```

Then re-pin Tier-1 deps in `package.json`:

```json
{
  "dependencies": {
    "react":            "18.2.0",
    "react-dom":        "18.2.0",
    "react-router-dom": "6.22.0"
  }
}
```

Run `rm -rf node_modules package-lock.json && npm install`, commit the new lock file.

---

## 5. Register the remote in the shell

This is on the **shell repo** side — the platform team usually owns this, but you need to coordinate when adding a new remote.

### 5.1 Update `shell/.env.production`

```
VITE_NEXORA_URL=https://cdn.kala.com/nexora/v1.4.2/assets/remoteEntry.js
```

For staging, use a `latest` rolling pointer:

```
# .env.staging
VITE_NEXORA_URL=https://cdn-staging.kala.com/nexora/latest/assets/remoteEntry.js
```

### 5.2 Update `shell/vite.config.js`

The shell already loads URLs from env vars. Add a slot for your app:

```js
const remotes = {
  corelytics:    env.VITE_CORELYTICS_URL    || 'http://localhost:5176/assets/remoteEntry.js',
  nexora:        env.VITE_NEXORA_URL        || 'http://localhost:5174/assets/remoteEntry.js',
  saarthi:       env.VITE_SAARTHI_URL       || 'http://localhost:5175/assets/remoteEntry.js',
  interviewer:   env.VITE_INTERVIEWER_URL   || 'http://localhost:5177/assets/remoteEntry.js',
}
```

### 5.3 Add the route in `shell/src/App.jsx`

```jsx
import React, { Suspense, lazy } from 'react'
import RemoteErrorBoundary from './RemoteErrorBoundary.jsx'

const NexoraApp = lazy(() => import('nexora/App'))

// Inside the shell's Routes:
<Route path="/nexora/*" element={
  <RemoteErrorBoundary name="Nexora">
    <Suspense fallback={<RemoteFallback name="Nexora" />}>
      <NexoraApp />
    </Suspense>
  </RemoteErrorBoundary>
} />
```

The `/*` matters — it tells React Router to delegate ALL `/nexora/...` paths to the remote, so the remote's internal routes work naturally.

---

## 6. Local testing — running shell + remote together

Federation requires the remote to be **built**, not just `vite dev` — the dev server doesn't emit `remoteEntry.js`. So local testing is:

```
Terminal 1 — remote (build + preview, watches src changes):
  cd /path/to/nexora-repo
  npm run build && npm run preview        # serves on :5174

Terminal 2 — shell (regular dev):
  cd /path/to/shell-repo
  npm run dev                              # serves on :5173
```

For faster iteration on the remote, use `vite build --watch`:

```bash
npm run build -- --watch &
npm run preview
```

Or a one-shot script in the remote's `package.json`:

```json
{
  "scripts": {
    "dev:federated": "vite build --watch"
  }
}
```

…and run a static file server (e.g. `npx serve dist -l 5174`) alongside.

### 6.1 Confirm the manifest is reachable

```bash
curl -I http://localhost:5174/assets/remoteEntry.js
```

Expect `200 OK` with `Content-Type: application/javascript`. If you get HTML or 404, your `base` path is wrong (see §4.4).

### 6.2 Confirm the shell loads it

In the browser DevTools Network tab, after navigating to `/nexora` in the shell, you should see:

```
GET http://localhost:5174/assets/remoteEntry.js                   200
GET http://localhost:5174/assets/__federation_expose_App-XXX.js   200
GET http://localhost:5174/assets/<chunk>-XXX.js                   200  (multiple)
```

If you see `text/html` content-type on any of those, asset paths are wrong — go back to §4.4.

---

## 7. CI/CD — automate the publish

Each remote ships its own pipeline. Copy this template:

### 7.1 GitHub Actions example

```yaml
# .github/workflows/deploy.yml
name: Build and Deploy Nexora Remote

on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with: { node-version: '20' }

      - name: Verify Tier-1 versions
        run: |
          node -e "const v={'react':'18.2.0','react-dom':'18.2.0','react-router-dom':'6.22.0'};
            for(const[p,want] of Object.entries(v)){
              const got=require(p+'/package.json').version;
              if(got!==want){console.error(\`✗ \${p}: \${got} ≠ \${want}\`);process.exit(1);}
              console.log(\`✓ \${p} \${got}\`);
            }"

      - run: npm ci
      - run: npm run build

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id:    ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: ap-south-1

      # Versioned (immutable) — pinned by the shell in production
      - run: aws s3 sync dist/ s3://kala-frontends/nexora/v${{ github.sha }}/ --delete

      # Latest pointer — used by staging / dev
      - run: aws s3 sync dist/ s3://kala-frontends/nexora/latest/ --delete

      - name: Notify
        run: |
          echo "Deployed: https://cdn.kala.com/nexora/v${{ github.sha }}/assets/remoteEntry.js"
          echo "          https://cdn.kala.com/nexora/latest/assets/remoteEntry.js"
```

### 7.2 IIS / on-prem alternative

If you ship to a Windows IIS server instead of S3, the equivalent is a robocopy step:

```powershell
robocopy dist \\fileserver\sites\cdn\nexora\v$env:GITHUB_SHA /E /MIR
robocopy dist \\fileserver\sites\cdn\nexora\latest /E /MIR
```

Then your `web.config` serves the static files (the same web.config patterns we already use for sub-app bundles).

---

## 8. CORS configuration

The shell at `https://app.kala.com` fetches `remoteEntry.js` from `https://cdn.kala.com/nexora/...`. That's a cross-origin request — your CDN/server **must** include:

```
Access-Control-Allow-Origin: https://app.kala.com
```

(Or `*` if you're OK with any caller. For a closed platform, prefer the explicit origin.)

### S3 bucket CORS policy

```json
[
  {
    "AllowedHeaders": ["*"],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedOrigins": [
      "https://app.kala.com",
      "https://staging.kala.com",
      "http://localhost:5173"
    ],
    "ExposeHeaders": []
  }
]
```

### CloudFront response-headers policy

Attach a custom response-headers policy to the distribution that adds the same `Access-Control-Allow-Origin` header.

### IIS web.config

```xml
<system.webServer>
  <httpProtocol>
    <customHeaders>
      <add name="Access-Control-Allow-Origin" value="https://app.kala.com" />
    </customHeaders>
  </httpProtocol>
</system.webServer>
```

---

## 9. Common gotchas

| Symptom | Cause | Fix |
|---|---|---|
| `Failed to load module script: ... text/html` | Remote's asset paths don't include the deploy subfolder | Set `base: '/<app>/'` in `vite.config.js` (§4.4) |
| `Loading chunk N failed` after deploy | Browser cached the manifest pointing at old chunk hashes | Set short `Cache-Control: max-age=60` on `remoteEntry.js`; long-cache the hashed asset chunks |
| `Cannot read properties of null (reading 'useState')` / "Invalid hook call" | Two copies of React loaded — version mismatch between shell and remote | Pin Tier-1 versions exactly (see version-standardization doc) |
| Remote's `<BrowserRouter>` fights with shell's | Both have their own router | Remove `<BrowserRouter>` from `App.jsx`; keep it only in `main.jsx` (§4.3) |
| CORS error on `remoteEntry.js` | Server not sending `Access-Control-Allow-Origin` | §8 |
| Shell loads but routes inside remote don't work | Shell's `<Route path="/nexora">` (no `*`) catches only the root | Use `<Route path="/nexora/*">` so child paths delegate to the remote |
| Remote shows blank with no errors | `exposes: { './App': './src/App.jsx' }` doesn't have a default export | `App.jsx` must `export default function App()` |
| Old `remoteEntry.js` served after deploy | CDN edge cache | Invalidate CDN path or set short max-age on the manifest |
| Two copies of `react-router-dom` end up in bundles | Tier 1 not pinned, one app upgraded a minor version | Pin exactly; CI checks should catch |
| `Cannot find module 'nexora/App'` at build time | Shell's `remotes` key doesn't match remote's `name` | Both must use exactly `nexora` (case-sensitive) |

---

## 10. Per-app migration recipes

These are compressed walkthroughs — they all follow §4 above with app-specific details.

### 10.1 Corelytics

```js
// vite.config.js
federation({
  name: 'corelytics',
  filename: 'remoteEntry.js',
  exposes: {
    './App': './src/App.jsx',
  },
  shared: ['react', 'react-dom', 'react-router-dom'],
})
```

- `base: '/corelytics/'`
- Standalone port: `5176` (frontend), `5101` (backend)
- The `App.jsx` is already router-less after the recent refactor — just remove `<BrowserRouter>` if any remains
- LoginPage is unused once integrated — remove or keep behind a dev-flag (see auth-contract doc)

### 10.2 Nexora

The Nexora landing page is currently HTML rendered by the Flask backend at `/nexora/`. Two paths:

**Path A — keep landing on backend**
The shell links to `https://api.kala.com/nexora/` directly. The federated remote only exposes Pulse/Zestforce/GrowX subapps. Less work.

**Path B — convert landing to React**
Migrate the inline HTML in `app.py` to a React route in the Nexora frontend repo. The whole Nexora experience (landing + subapps) becomes one federated remote. More work, cleaner result.

### 10.3 Saarthi (Sales Training)

```js
federation({
  name: 'saarthi',
  filename: 'remoteEntry.js',
  exposes: {
    './App': './src/App.jsx',
    './Scenario': './src/components/Scenario.jsx',  // expose a specific widget too
  },
  shared: ['react', 'react-dom', 'react-router-dom'],
})
```

Saarthi has its own backend on port 5000 — keep it as-is, change only the frontend.

### 10.4 Interviewer Bot

The build is currently a Vite app. Confirm `vite.config.js` and apply §4 verbatim.

---

## 11. Validation checklist before merging

Before opening a PR that converts your repo to a remote, verify:

- [ ] `package.json` Tier-1 versions match version-standardization.md exactly
- [ ] `.npmrc` contains `save-exact=true`
- [ ] `package-lock.json` is committed
- [ ] `vite.config.js` has `federation()` with `name`, `filename`, `exposes`, `shared`
- [ ] `vite.config.js` has correct `base` for the deployment URL
- [ ] `src/App.jsx` is router-less and has `export default`
- [ ] `src/main.jsx` wraps `<App />` in `<BrowserRouter>` for standalone dev
- [ ] `npm run dev` still works (standalone)
- [ ] `npm run build && npm run preview` produces a `remoteEntry.js`
- [ ] `curl http://localhost:<port>/assets/remoteEntry.js` returns 200 with JS content-type
- [ ] Shell's `.env` updated (or coordinated with platform team)
- [ ] CI workflow added for upload-on-merge
- [ ] CORS configured on the deploy target
- [ ] Auth flow tested — opening `/<app>?email=...&autoLogin=true` populates the user without a login screen

---

## 12. FAQ

**Q. Can my remote depend on a different React version than the shell?**
No. Tier 1 of [version-standardization.md](./version-standardization.md) is mandatory. Two Reacts in the same browser tab is a runtime crash.

**Q. What if my app uses Webpack / CRA instead of Vite?**
Use `module-federation/webpack` plugin. The principles are identical (`name`, `filename`, `exposes`, `shared`); the syntax differs. Out of scope for this doc — open a Slack thread with platform-architecture for help.

**Q. Can I expose more than one component?**
Yes — add more entries to `exposes`. The shell can `import('myremote/Widget')` for any of them. Pattern: expose `./App` (full app) plus `./widgets/X` for any reusable widget the shell wants to embed elsewhere.

**Q. What about Tailwind / global CSS?**
Each remote builds its own CSS bundle. With `cssCodeSplit: false`, federation ships a single `.css` file that the browser appends when the chunk loads. This usually "just works" but watch for:
- Tailwind class-name collisions (rare)
- Global selectors (`body`, `*`) overriding shell styles — keep them scoped
- Reset CSS applied twice — shell should own the reset, remotes should not include their own

**Q. Can a remote navigate users to another remote?**
Yes — call `navigate('/saarthi/some-route')` (use `useNavigate()` from React Router). The shell's router catches it and lazy-loads Saarthi.

**Q. How do I share state (e.g. theme) across remotes?**
Two patterns:
1. **Via the shell's React context** — the shell wraps remotes in `<ThemeProvider>`, remotes consume `useTheme()` from the same `react` instance (which they share via federation).
2. **Via a small npm package** — `kala-shared-contracts` published to your private registry, providing types + a default theme.

Both work; (1) is simpler if the shell is the single owner of theme.

**Q. The remote loads fine, but its API calls fail with 401.**
Browser is making a cross-origin XHR to an API that doesn't accept the shell's cookies. Either deploy the remote's backend on the same domain (subdomain works with `credentials: 'include'`) or use a token-based auth that the shell injects via header.

**Q. How big should `remoteEntry.js` be?**
Typically 5–15 KB. It's just a manifest. The actual code lives in the chunks it points at. If yours is 200 KB+, your `shared` config is wrong — you're bundling React into the manifest instead of sharing it.

---

## 13. Worked example — full mini-walkthrough

Here's a real conversion of a tiny Vite app, end-to-end, in 8 minutes.

```bash
# Starting point: a regular Vite + React + Router app
cd ~/repos/kala-toy-app

# 1. Install plugin
npm install --save-dev @originjs/vite-plugin-federation@1.3.6

# 2. Pin Tier-1 versions
echo "save-exact=true" > .npmrc
npm install react@18.2.0 react-dom@18.2.0 react-router-dom@6.22.0

# 3. Edit vite.config.js — add federation block (see §4.2)

# 4. Edit src/App.jsx — remove BrowserRouter wrapper (see §4.3)
# 5. Edit src/main.jsx — add BrowserRouter wrapper (see §4.3)

# 6. Build and verify
npm run build
ls dist/assets/  # confirm remoteEntry.js exists
npm run preview &
curl -I http://localhost:5174/assets/remoteEntry.js
# Expected: HTTP/1.1 200 OK, Content-Type: application/javascript

# 7. In the shell repo:
echo "VITE_TOY_URL=http://localhost:5174/assets/remoteEntry.js" >> .env.development
# Add `toy` to remotes object in vite.config.js
# Add a route: <Route path="/toy/*" element={<ToyApp />} />

# 8. Run shell
cd ~/repos/kala-shell
npm run dev
# Open http://localhost:5173/toy — your remote loads inside the shell
```

Done. Now ship it via CI to a real CDN URL, update `.env.production`, redeploy the shell.

---

## 14. Document history

| Date | Change | By |
|---|---|---|
| 2026-05-04 | Initial version. Covers Vite remote conversion + shell registration + CI + CORS + per-app recipes. | Platform Architecture |
