# KALA AI Platform — Library Version Standardization

**Audience:** All frontend/backend engineers working on KALA Group apps (Corelytics, Nexora, Saarthi, Interviewer Bot, future apps), and anyone consuming the federated shell.

**Status:** Active — applies to all new code as of the date below.

**Owner:** Platform Architecture team. Changes to "Tier 1" versions require sign-off; lower tiers are advisory.

---

## 1. Why this exists

KALA's frontend is moving to a Module Federation architecture: a single shell app loads each product (Corelytics, Nexora, Saarthi, Interviewer) as a remote module at runtime. For this to work safely, certain libraries must be **byte-identical** across the shell and every remote — otherwise the browser ends up with two copies of React, hooks fail with "invalid hook call", and the platform crashes in subtle, hard-to-diagnose ways.

This document defines the canonical versions of those libraries, plus a tier system distinguishing what is mandatory, what is recommended, and what is each team's call.

---

## 2. Tier system

| Tier | What | Enforcement |
|---|---|---|
| **Tier 1 — Critical** | Federation `shared` dependencies. Loaded once by the runtime and reused. | **Mandatory.** Identical major + minor across shell and all remotes. CI fails on mismatch. |
| **Tier 2 — Build tooling** | Vite, federation plugin, React plugin. Affect the *output format* of `remoteEntry.js`. | **Recommended.** Same major required. Minor may differ; patch freely. |
| **Tier 3 — Shared libraries** | Charts, Excel parsing, etc. Bundled inside each remote, but standardized for predictable bundle size. | **Advisory.** Drift requires only an explainable reason in PR. |
| **Tier 4 — App-private** | Anything else (per-app feature libraries, tests, lint). | **Free.** Each team's call. |
| **Tier B — Backend** | Python libs in each service. Don't federate — but pinning unifies deploy scripts. | **Recommended.** |

---

## 3. Canonical versions

> **Source of truth:** `package-lock.json` files in each repo, after `npm install` against the versions below. The `package.json` declarations below are the *floor*; the lock file pins the exact resolved versions. Always commit the lock file.

### 3.1 Tier 1 — MUST match exactly

```json
{
  "react":            "18.2.0",
  "react-dom":        "18.2.0",
  "react-router-dom": "6.22.0"
}
```

**Pin without `^`.** Caret ranges (`^18.2.0`) allow `npm install` to resolve to any 18.x ≥ 18.2.0, which means the same `package.json` on different machines produces different trees.

If your linter complains about pinned versions, configure `save-exact=true` in `.npmrc`:

```
save-exact=true
```

### 3.2 Tier 2 — Build tooling (same major; same minor recommended)

```json
{
  "vite":                              "5.1.4",
  "@vitejs/plugin-react":              "4.2.1",
  "@originjs/vite-plugin-federation":  "1.3.6"
}
```

Different majors of `@originjs/vite-plugin-federation` produce incompatible manifests — the shell will fetch a `remoteEntry.js` it cannot parse. Stay on `1.x` everywhere until a coordinated upgrade.

### 3.3 Tier 3 — Shared libraries (recommended)

```json
{
  "axios":          "1.6.7",
  "recharts":       "2.12.0",
  "lucide-react":   "0.344.0",
  "papaparse":      "5.5.3",
  "xlsx":           "0.18.5",
  "html2canvas":    "1.4.1",
  "jspdf":          "4.2.1",
  "react-dropzone": "14.2.3",
  "react-markdown": "9.0.1",
  "clsx":           "2.1.0"
}
```

Multiple charting libraries (e.g. one app on `recharts` and another on `chart.js`) are tolerated but discouraged. PRs introducing a duplicate-purpose lib should explain why.

### 3.4 Tier 4 — Styling and dev (per-app; align where convenient)

```json
{
  "tailwindcss":               "3.4.1",
  "@tailwindcss/typography":   "0.5.12",
  "autoprefixer":              "10.4.18",
  "postcss":                   "8.4.35",

  "eslint":                    "8.56.0",
  "eslint-plugin-react":       "7.33.2",
  "eslint-plugin-react-hooks": "4.6.0",
  "@types/react":              "18.2.56",
  "@types/react-dom":          "18.2.19"
}
```

These don't ship to the browser via federation — they're build-time or local-only — so drift is harmless. Listed for convenience when a team is starting a new repo.

### 3.5 Tier B — Backend (`requirements.txt`)

```
flask==3.0.2
flask-cors==4.0.0
pandas==3.0.0
openpyxl==3.1.5
xlrd==2.0.1
pyxlsb==1.0.10
requests==2.31.0
python-dotenv==1.0.1
gunicorn==21.2.0; sys_platform != "win32"
gevent==24.11.1; sys_platform != "win32"
waitress==3.0.2;  sys_platform == "win32"
pymongo[srv]==4.7.3
dnspython==2.6.1
certifi==2024.2.2
bcrypt==4.2.1
```

Already exact-pinned in our existing repos — keep it that way. `==` (not `>=`) matters under pip-tools / Docker layer caching.

---

## 4. How to apply this in your repo

### 4.1 New repo (starting from scratch)

1. Copy the relevant blocks above into your `package.json` `dependencies` / `devDependencies`.
2. Add `.npmrc`:
   ```
   save-exact=true
   ```
3. Run `npm install`. Commit the resulting `package-lock.json`.
4. Add the federation plugin to `vite.config.js` (see *Federation Cookbook* doc).
5. Open a PR. CI will validate Tier-1 match.

### 4.2 Existing repo (migration)

1. Open `package.json` and the lock file side-by-side.
2. Replace any `^X.Y.Z` for Tier-1 dependencies with the exact pinned version from this document.
3. `rm -rf node_modules package-lock.json && npm install` to regenerate.
4. Run the app locally — verify nothing broke.
5. Commit the new `package-lock.json`.

### 4.3 Verification — paste into your terminal

```bash
node -e "const v = ['react','react-dom','react-router-dom']; \
  v.forEach(p => console.log(p.padEnd(20), require(p+'/package.json').version))"
```

Expected output:

```
react                18.2.0
react-dom            18.2.0
react-router-dom     6.22.0
```

Any other number → fix before pushing.

---

## 5. Upgrade procedure

### Tier 1 (react / react-dom / react-router-dom)

1. Platform Architecture proposes the upgrade in the `#kala-platform` channel.
2. A trial branch upgrades the **shell** + one remote together, run end-to-end.
3. If green, all remote owners get a 2-week window to bump.
4. After the window, the new version becomes mandatory; CI is updated; this document is amended.

**Never upgrade Tier 1 in one repo unilaterally — it WILL break the federated runtime.**

### Tier 2 (build tooling)

Same process, but lower urgency. Coordinate via PR approval; no fixed window.

### Tier 3 / 4 / B

Per-team. Bump in your repo, regenerate the lock file, ship.

---

## 6. CI checks (recommended)

Add to your CI pipeline:

```yaml
# .github/workflows/version-check.yml
- name: Verify Tier-1 versions
  run: |
    node <<'EOF'
    const required = {
      "react":            "18.2.0",
      "react-dom":        "18.2.0",
      "react-router-dom": "6.22.0",
    };
    for (const [pkg, want] of Object.entries(required)) {
      const got = require(`${pkg}/package.json`).version;
      if (got !== want) {
        console.error(`✗ ${pkg}: required ${want}, got ${got}`);
        process.exit(1);
      }
      console.log(`✓ ${pkg} ${got}`);
    }
    EOF
```

Drop this into every remote's CI. Mismatch = red build = nothing merges to main.

---

## 7. FAQ

**Q. My Tailwind / ESLint version is different. Do I need to change?**
No — Tier 4. Local concern.

**Q. I want to use a different charting library. Allowed?**
Tier 3 — yes, but explain in the PR why `recharts` is insufficient. Two libs of the same purpose across the platform doubles the team's mental overhead.

**Q. Can I use React 19 / React Router 7 / Vite 6?**
Not yet. Wait for a coordinated Tier-1 / Tier-2 upgrade announcement. A unilateral upgrade will break federation.

**Q. We're a brand-new app — can we skip federation?**
Yes if the app is fully isolated (own domain, own auth, no shell integration). The moment you want to be embedded in the shell, you're back in Tier-1 land.

**Q. What about TypeScript types `@types/*`?**
Tier 4. Use whatever matches your editor / lint setup. They never ship.

**Q. The shared lib I need isn't in the table. What version do I pick?**
Use the latest stable major. If two apps end up needing the same lib, the second team to add it should match the first team's version and ask Platform Architecture to add it to Tier 3.

**Q. How is this enforced for the backend?**
Pip resolves the same way npm does — `requirements.txt` in our repos uses `==` already, which means lock-step versions per environment. Each service is independently deployed, so cross-service mismatch only matters for shared libraries (none today).

---

## 8. Quick-reference cheat sheet

Copy-pasteable summary for new contributors:

> **TL;DR**
> - Pin `react`, `react-dom`, `react-router-dom` to exact versions in `package.json` (no `^`)
> - Match `vite`, `@vitejs/plugin-react`, `@originjs/vite-plugin-federation` to the platform versions
> - Run `npm install`, commit `package-lock.json`
> - Don't upgrade React or React Router without sync from Platform Architecture
> - Run the verification snippet in §4.3 before opening a PR

---

## 9. Document history

| Date | Change | By |
|---|---|---|
| 2026-05-04 | Initial version. Tier 1 = React 18.2.0 / RR 6.22.0. Tier 2 = Vite 5.1.4 / federation 1.3.6. | Platform Architecture |
