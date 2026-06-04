# KALA Standard Design v4 — Instructions
> **Inputs:** `KALA Design System v4.html` (visual reference) + `kala-system-v4.css` (stylesheet) + this document (implementation guide).
> **Usage:** *"Follow KALA-standard-design-instructions.md to build [app name]"* or *"migrate [existing app] to the KALA standard."*

---

## What is the KALA Standard Design?

The shared visual language for all nine KALA AI applications. Four stacked layers:

| Layer | z-index | Mechanism |
|-------|---------|-----------|
| Aurora gradient | `-2` | `position:fixed` — 4 **static** blobs (no animation) |
| Dot-grid texture | `-1` | `position:fixed` — 20px radial dots |
| Clay surfaces | `auto` | Softened `box-shadow: clay-out + offset` |
| Frost glass | varies | `backdrop-filter: blur()` on overlays & navigation |

### v4 Changes from v1
- Shadows softened ~40% (reduced offsets, blur, contrast)
- Aurora blobs are **static** (no animation) with reduced opacity
- Frost glass (`backdrop-filter: blur`) on sidebar, topbar, tooltips, toasts, dropdowns, modals, drawers, command palette
- New components: tooltips, toasts, breadcrumbs, pagination, accordion, stepper, tag input, file upload, code block, command palette, drawer, dividers, bottom nav, top bar
- New patterns: charts (8 types), error/404 pages, onboarding, loading/splash, settings layout
- Dark mode transitions: Fade, Circle reveal, Morph (via View Transitions API)

---

## 1. Design Tokens

Paste this `:root` block verbatim into every app. **Never hardcode colours.**

```css
:root {
  --base-h: 215; --base-s: 22%; --base-l: 93%;
  --acc1-h: 215; --acc1-s: 88%; --acc1-l: 50%;
  --acc2-h: 190; --acc2-s: 80%; --acc2-l: 46%;

  --accent1: hsl(var(--acc1-h), var(--acc1-s), var(--acc1-l));
  --accent2: hsl(var(--acc2-h), var(--acc2-s), var(--acc2-l));

  /* v4: Softened clay shadows (~40% lighter than v1) */
  --clay-out:
    4px 4px 11px hsl(var(--base-h), calc(var(--base-s) + 3%), calc(var(--base-l) - 8%)),
    -3px -3px 10px hsl(var(--base-h), var(--base-s), calc(var(--base-l) + 4%));
  --clay-in:
    inset 3px 3px 8px hsl(var(--base-h), calc(var(--base-s) + 3%), calc(var(--base-l) - 7%)),
    inset -2px -2px 7px hsl(var(--base-h), var(--base-s), calc(var(--base-l) + 3%));
  --clay-press:
    inset 4px 4px 10px hsl(var(--base-h), calc(var(--base-s) + 4%), calc(var(--base-l) - 8%)),
    inset -3px -3px 8px hsl(var(--base-h), var(--base-s), calc(var(--base-l) + 3%));
  --clay-out-sm: 3px 3px 9px hsl(var(--base-h), calc(var(--base-s) + 3%), calc(var(--base-l) - 8%));
  --offset: 2px 2px 0px hsl(var(--base-h), calc(var(--base-s) + 5%), calc(var(--base-l) - 12%));

  --text-1: hsl(215, 48%, 10%);
  --text-2: hsl(215, 22%, 34%);
  --text-3: hsl(215, 14%, 54%);
  --success: #1a7a50; --danger: #c43050; --warning: #886600;
  --divider: rgba(50, 90, 180, 0.09);
  --border:  rgba(50, 90, 180, 0.18);

  --r-sm: 8px; --r-md: 14px; --r-lg: 20px;
  --r-xl: 26px; --r-2xl: 34px; --r-pill: 999px;
  --font-scale: 1;

  /* Frost glass tokens */
  --glass-bg: hsla(var(--base-h), var(--base-s), var(--base-l), 0.72);
  --glass-blur: 16px;
  --glass-border: hsla(var(--base-h), calc(var(--base-s) + 10%), calc(var(--base-l) + 4%), 0.45);
}

[data-mode="dark"] {
  --base-l: 11%; --base-s: 18%;
  --text-1: rgba(210, 225, 252, 0.95);
  --text-2: rgba(130, 160, 220, 0.72);
  --text-3: rgba(75,  110, 180, 0.52);
  --clay-out:
    4px 4px 11px hsl(var(--base-h), var(--base-s), calc(var(--base-l) - 4%)),
    -3px -3px 10px hsl(var(--base-h), var(--base-s), calc(var(--base-l) + 3%));
  --clay-in:
    inset 3px 3px 8px hsl(var(--base-h), var(--base-s), calc(var(--base-l) - 4%)),
    inset -2px -2px 7px hsl(var(--base-h), var(--base-s), calc(var(--base-l) + 2%));
  --clay-press:
    inset 4px 4px 10px hsl(var(--base-h), var(--base-s), calc(var(--base-l) - 5%)),
    inset -3px -3px 8px hsl(var(--base-h), var(--base-s), calc(var(--base-l) + 2%));
  --offset: 2px 2px 0px rgba(0, 0, 0, 0.22);
  --divider: rgba(130, 160, 220, 0.07);
  --border:  rgba(130, 160, 220, 0.14);
  --glass-bg: hsla(var(--base-h), var(--base-s), var(--base-l), 0.65);
  --glass-border: hsla(var(--base-h), calc(var(--base-s) + 6%), calc(var(--base-l) + 6%), 0.22);
}
```

**Per-app accent** — override only `--acc1-h`:
```css
--acc1-h: 215; /* blue   = Corelytics (default) */
--acc1-h: 172; /* teal   = Saarthi              */
--acc1-h: 262; /* violet = Zest Force            */
--acc1-h: 195; /* cyan   = Nexora               */
```

---

## 2. Background system — copy verbatim

**HTML** — first two children of `<body>`, before everything else:
```html
<div class="aurora-bg">
  <div class="aurora-blob aurora-blob-1"></div>
  <div class="aurora-blob aurora-blob-2"></div>
  <div class="aurora-blob aurora-blob-3"></div>
  <div class="aurora-blob aurora-blob-4"></div>
</div>
<div class="dot-grid"></div>
```

**v4 note:** Aurora blobs are **static** — no `animation` or `@keyframes auroraDrift`. Blob opacities are reduced (~30% softer than v1) for a subtle, professional backdrop.

**Body** must be:
```css
body { background: transparent; overflow-x: hidden; }
```

**Layout** must have no `z-index` and no `overflow:hidden`:
```css
.layout { min-height: 100vh; }
.page-content { overflow: visible; }
```

---

## 3. Dark / light mode

### Option A: Simple fade (default)
```js
const html = document.documentElement;
let isDark = false;
function toggleMode() {
  isDark = !isDark;
  html.dataset.mode = isDark ? 'dark' : 'light';
  document.getElementById('iconSun').style.display = isDark ? 'none' : '';
  document.getElementById('iconMoon').style.display = isDark ? '' : 'none';
  if (isDark) { html.style.removeProperty('--base-l'); html.style.removeProperty('--base-s'); }
  else { html.style.setProperty('--base-l','93%'); html.style.setProperty('--base-s','22%'); }
}
```

### Option B: View Transitions API (Circle / Morph)
For advanced transitions, dynamically inject CSS into a `<style id="transition-css">` tag:

```js
const TRANSITION_CSS = {
  fade: `
    ::view-transition-old(root) { animation: vt-fade-out 0.5s ease forwards; z-index: 2; }
    ::view-transition-new(root) { animation: vt-fade-in 0.5s ease forwards; z-index: 999; }
    @keyframes vt-fade-out { from { opacity: 1; } to { opacity: 0; } }
    @keyframes vt-fade-in  { from { opacity: 0; } to { opacity: 1; } }
  `,
  circle: `
    ::view-transition-old(root) { animation: none !important; z-index: 2; }
    ::view-transition-new(root) {
      animation: vt-circle-in 0.8s cubic-bezier(0.4,0,0.2,1) forwards;
      z-index: 999; mix-blend-mode: normal;
    }
    @keyframes vt-circle-in {
      from { clip-path: circle(0% at var(--reveal-cx) var(--reveal-cy)); }
      to   { clip-path: circle(150vmax at var(--reveal-cx) var(--reveal-cy)); }
    }
  `,
  morph: `
    ::view-transition-old(root) {
      animation: vt-morph-old 0.9s cubic-bezier(0.4,0,0.2,1) forwards; z-index: 2;
    }
    ::view-transition-new(root) {
      animation: vt-morph-new 0.9s cubic-bezier(0.4,0,0.2,1) forwards; z-index: 999;
    }
    @keyframes vt-morph-old {
      0%   { transform: scale(1); filter: blur(0px); opacity: 1; }
      40%  { transform: scale(1.04); filter: blur(6px); opacity: 0.8; }
      100% { transform: scale(1.08); filter: blur(14px); opacity: 0; }
    }
    @keyframes vt-morph-new {
      0%   { transform: scale(0.92); filter: blur(14px); opacity: 0; }
      50%  { transform: scale(0.97); filter: blur(5px); opacity: 0.6; }
      100% { transform: scale(1); filter: blur(0px); opacity: 1; }
    }
  `,
};

function injectTransitionCSS(type) {
  document.getElementById('transition-css').textContent = TRANSITION_CSS[type];
}

function applyDarkMode(on) {
  const btn = document.getElementById('modeToggle');
  const rect = btn.getBoundingClientRect();
  html.style.setProperty('--reveal-cx', (rect.left + rect.width / 2) + 'px');
  html.style.setProperty('--reveal-cy', (rect.top + rect.height / 2) + 'px');
  injectTransitionCSS(currentTransition);
  if (document.startViewTransition) {
    const t = document.startViewTransition(() => { swapTheme(on); return Promise.resolve(); });
    t.ready.catch(() => {});
    t.finished.catch(() => {});
    t.updateCallbackDone.catch(() => {});
  } else { swapTheme(on); }
}
```

**Rule:** in dark mode, **never write** `--base-l` or `--base-s` to `html.style`. Always call `removeProperty` so the `[data-mode="dark"]` `:root` override can apply.

---

## 4. Frost Glass

Apply to overlays, navigation, and floating elements. Use the `--glass-*` tokens:

```css
.my-overlay {
  background: var(--glass-bg);
  -webkit-backdrop-filter: blur(var(--glass-blur));
  backdrop-filter: blur(var(--glass-blur));
  border: 1px solid var(--glass-border);
}
```

**Where to use frost glass:**
- Sidebar navigation (20px blur)
- Top bar / header
- Mode toggle button
- Command palette (24px blur + `saturate(1.3)`)
- Drawer panel + overlay backdrop (`blur(4px)` on overlay)
- Dropdown menus & date picker panels
- Tooltip content
- Toast notifications

**Where NOT to use:** Cards, stat cards, tables, alerts, inputs (these use solid `hsl(var(--base-h),...)` backgrounds).

---

## 5. Component Catalog

### Core Components
| Component | Class | Shadow | Notes |
|-----------|-------|--------|-------|
| Card | `.card` | `clay-out + offset` | `overflow: visible` always |
| Stat Card | `.stat-card` | `clay-out + offset` | KPI display |
| App Card | `.app-card` | `clay-out + offset` | Hover: lift + enlarged shadow |
| Button Primary | `.btn.btn-primary` | `clay-out-sm` + accent offset | Main CTA |
| Button Secondary | `.btn.btn-secondary` | `clay-out-sm + offset` | Secondary |
| Button Ghost | `.btn.btn-ghost` | none | Tertiary / table actions |
| Button Danger | `.btn.btn-danger` | `clay-out-sm` + red offset | Destructive |
| Button Icon | `.btn.btn-icon` | inherits variant | Square, icon only |
| Input | `.input` | `clay-in` | Focus: `+ accent ring` |
| Textarea | `.textarea` | `clay-in` | Resizable |
| Input w/ Icon | `.input-with-icon` | `clay-in` | Icon positioned left |
| Toggle | `.toggle` | `clay-in` | `aria-pressed` |
| Checkbox | `.chk` | `clay-in` | Checked: accent bg |
| Radio | `.radio` | `clay-in` | Checked: accent bg |
| Range Slider | `.clay-range` | `clay-in` | Thumb: accent + offset |
| Badge | `.badge` | offset + inset border | Variants: default/blue/green/orange/red |
| Chip | `.chip` | `clay-out-sm` + offset | `aria-pressed` |
| Avatar | `.avatar` | variant-dependent | Sizes: sm/default/lg/xl |
| Avatar Stack | `.av-stack` | border overlay | `isolation: isolate` |

### Navigation
| Component | Class | Notes |
|-----------|-------|-------|
| Sidebar | `.nav-mock` | Clay card with `.nav-item` children |
| Tab Bar | `.tabs` | Container owns shadow; `.tab` has `box-shadow: none` |
| Segmented | `.seg` | Animated `.seg-blob` slides between options |
| Breadcrumbs | `.breadcrumbs` | `.breadcrumb-item` + `.breadcrumb-sep` |
| Pagination | `.pagination` | `.page-btn` with active state |
| Top Bar | `.top-bar` | **Frost glass**; search + actions + avatar |
| Bottom Nav | `.bottom-nav` | Mobile tab bar |

### Data Display
| Component | Class | Notes |
|-----------|-------|-------|
| Table | `.tbl-scroll > table` | Single container, no inner wrapper |
| Alert | `.alert` | Variants: blue/green/orange/red (left border) |
| Toast | `.toast` | **Frost glass**; Variants: blue/green/red/orange |
| Tooltip | `.tooltip-wrap > .tooltip-content` | **Frost glass**; Positions: top/right/bottom/left |
| Progress | `.progress-track > .progress-bar` | `clay-in` track |
| Spinner | `.spinner` | Sizes: sm/default/lg |
| Skeleton | `.skeleton` | `clay-in` + pulse animation |
| Empty State | `.empty` | Centered card with icon + CTA |

### Overlays
| Component | Class | Notes |
|-----------|-------|-------|
| Modal | `.modal-stage > .modal` | Desktop: centered; Mobile: bottom sheet |
| Drawer | `.drawer-overlay + .drawer` | **Frost glass** on both overlay and panel |
| Command Palette | `.cmd-overlay > .cmd-palette` | **Frost glass** (24px blur); searchable list |

### Form Components
| Component | Class | Notes |
|-----------|-------|-------|
| Select | `.clay-select` | Custom dropdown, **frost glass** on list |
| Date Picker | `.clay-date` | Custom calendar, **frost glass** on panel |
| Tag Input | `.tag-input-wrap` | `clay-in`; `.tag` children |
| File Upload | `.file-upload` | Dashed border, drag & drop |

### Content
| Component | Class | Notes |
|-----------|-------|-------|
| Chat Bubble | `.bubble` | Clay out + offset |
| Composer | `.composer` | `clay-in`; textarea + send button |
| Code Block | `.code-block` | `clay-in`; mono font + copy button |
| Accordion | `.accordion` | Clay out + offset; collapsible items |
| Stepper | `.stepper` | `.step` with circle + label + connector |
| Activity Feed | timeline pattern | Avatar + text + timestamp + badge |
| Divider | `.divider` | Plain line or `.divider-label` (labeled) |
| Notification Dot | `.notif-dot` | Danger dot on avatar/icon |
| Notification Count | `.notif-count` + `.notif-count-badge` | Count badge on avatar/icon |

---

## 6. Charts (8 types)

Use Chart.js styled with the KALA palette:
```js
const KPAL = [
  'hsl(215,88%,50%)',   // accent1 blue
  'hsl(190,80%,46%)',   // accent2 teal
  'hsl(145,55%,38%)',   // green
  'hsl(25,72%,55%)',    // warm orange
  'hsl(270,50%,58%)',   // purple
  'hsl(340,60%,52%)',   // rose
  'hsl(50,75%,50%)',    // gold
  'hsl(200,60%,55%)',   // sky
];
```

**Chart.js defaults for KALA:**
- Font: `'Geist', system-ui, sans-serif`
- Tooltip: glass-like (`bg: base color`, `border: accent1`, `cornerRadius: 8`)
- Grid: `var(--divider)` color
- Bar border radius: `6`
- Doughnut border: `3px` in base color, cutout `60%`

**Available chart components** (see `ds-charts.jsx`):
| Type | Component | Notes |
|------|-----------|-------|
| Bar | `<BarChart>` | Vertical bars with labels |
| Horizontal Bar | `<HBarChart>` | Horizontal with clay-in track |
| Line | `<LineChart>` | Multi-series, smooth curves, dots |
| Area | `<AreaChart>` | Filled gradient under curve |
| Donut / Pie | `<DonutChart>` | `donut={true/false}`, legend |
| Gauge | `<GaugeChart>` | 260° arc with value |
| Scatter | `<ScatterPlot>` | Variable-radius dots |
| Sparkline | `<Sparkline>` | Inline mini chart |

---

## 7. Page Patterns

### Error / 404
Large status code (faded), icon, title, description, action buttons. Two variants: not-found and generic error with ref code.

### Onboarding / Walkthrough
Step-through wizard with icon, title, description, animated progress dots (clay-in inactive, accent active with offset), and back/continue buttons.

### Loading / Splash
Two variants:
1. **Spinner**: Brand name + spinner + loading text
2. **Progress**: App icon + progress bar + percentage

Also includes **skeleton loading pattern** with `clay-in` placeholder blocks.

### Settings / Preferences
Sectioned form with profile, notification toggles, segmented controls, and action footer. Uses `SettingRow` helper (label + description + control).

---

## 8. Component Rules

### Cards
```css
.card {
  padding: 28px;
  background: hsl(var(--base-h), var(--base-s), var(--base-l));
  border-radius: var(--r-xl);
  border: 1px solid var(--border);
  box-shadow: var(--clay-out), var(--offset);
  overflow: visible; /* NEVER hidden */
}
```

### Buttons
Press state: `box-shadow: none; transform: translate(Xpx, Ypx)`.
All buttons: `touch-action: manipulation; -webkit-tap-highlight-color: transparent;`.
In a row: use `--clay-out-sm`, never `--clay-out`.

### Inputs
```css
.input, .textarea { background: transparent; border: none; box-shadow: var(--clay-in); }
.input:focus { box-shadow: var(--clay-in), 0 0 0 3px color-mix(in srgb, var(--accent1) 16%, transparent); }
```

### Tabs — container pattern (mandatory)
```css
.tabs { /* container owns the shadow */
  display: flex; gap: 6px; padding: 6px;
  box-shadow: var(--clay-out), var(--offset);
}
.tab { box-shadow: none; } /* individual tabs = no shadow */
.tab[aria-selected="true"] {
  background: var(--accent1); color: #fff;
  box-shadow: 4px 5px 0 hsl(var(--acc1-h),var(--acc1-s),calc(var(--acc1-l) - 22%)),
              inset 0 1px 0 rgba(255,255,255,.22);
}
```

### Tables
```html
<div class="tbl-scroll"><table>...</table></div>
```
Single container. No nested `overflow:hidden` wrapper.

### Avatar stacks
```css
.av-stack { display: flex; isolation: isolate; }
.av-stack .avatar { border: 3px solid hsl(var(--base-h),var(--base-s),var(--base-l)); margin-left: -9px; }
.av-muted { box-shadow: inset 0 0 0 1.5px var(--border); } /* no clay-out */
```

### Modal
Desktop: centered, `border-radius: var(--r-2xl)`, `max-width: 430px`.
Mobile: bottom sheet, `border-radius: var(--r-2xl) var(--r-2xl) 0 0`.

---

## 9. Mobile

```css
.btn    { min-height: 44px; touch-action: manipulation; }
.btn-sm { min-height: 38px; }

@media (max-width: 768px) {
  .card, .app-card, .stat-card, .tbl-scroll, .alert, .nav-mock, .bubble, .modal, .toast, .accordion, .top-bar, .bottom-nav {
    box-shadow:
      3px 3px 9px hsl(var(--base-h),calc(var(--base-s) + 3%),calc(var(--base-l) - 8%)),
      2px 2px 0px hsl(var(--base-h),calc(var(--base-s) + 5%),calc(var(--base-l) - 12%));
  }
}

@media (hover: hover) and (pointer: fine) { /* hover rules */ }
```

Viewport meta:
```html
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
```

---

## 10. Typography

```html
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600;700&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">
```

| Element | Font | Weight | Size |
|---------|------|--------|------|
| Body | Geist | 400/500/600 | `calc(14px * var(--font-scale))` |
| Monospace | Geist Mono | 400/500 | 10–12px |
| Page title | Geist | 700 | `calc(54px * var(--font-scale))` |
| Section heading | Geist | 700 | `calc(28px * var(--font-scale))` |
| Card title | Geist | 700 | `calc(16px * var(--font-scale))` |
| Stat values | Geist | 700 | `calc(28px * var(--font-scale))` |
| Section labels | Geist | 700 | 10px, uppercase, 0.14em tracking |
| Caption | Geist | 400 | 11px |

---

## 11. Radius + Spacing

| Token | Value | Usage |
|-------|-------|-------|
| `--r-sm` | 8px | checkboxes, small chips, chart bars |
| `--r-md` | 14px | avatars, icon buttons |
| `--r-lg` | 20px | buttons, inputs |
| `--r-xl` | 26px | cards, tables, alerts |
| `--r-2xl` | 34px | app cards, modals |
| `--r-pill` | 999px | badges, toggles, seg controls |

Card padding: `28px` desktop / `18px 16px` mobile.
Section margin-bottom: `56px` desktop / `32px` mobile.

---

## 12. What NOT to do

| ❌ | ✅ |
|----|-----|
| `overflow: hidden` on `.card` | `overflow: visible` always |
| `z-index` on `.layout` | No z-index on layout |
| Individual shadow on each `.tab` | Shadow on `.tabs` container only |
| Nested `overflow:hidden` div inside `.tbl-scroll` | Single `.tbl-scroll`, no wrapper |
| Native `<select>` | `.clay-select` custom component |
| Native `<input type="date">` | `.clay-date` custom calendar |
| `color-mix(..., transparent)` for card backgrounds | Solid `hsl(var(--base-h),...)` |
| `display: none` for overlay hide/show | `visibility:hidden; pointer-events:none; opacity:0` |
| `overflow-y: hidden` on body | `overflow-x: hidden` only |
| Setting `--base-l` in JS during dark mode | `removeProperty('--base-l')` |
| `--clay-out` on inline/row buttons | `--clay-out-sm` for side-by-side elements |
| Animated aurora blobs | Static blobs only (v4) |
| Solid backgrounds on overlays | Use `--glass-*` tokens with `backdrop-filter` |
| Loading spinner flex layout persisting | Clear container `style` before rendering content |

---

## 13. New app — 8 steps

1. Create your HTML file, link `kala-system-v4.css` and Geist fonts
2. Add aurora + dot-grid HTML as first children of `<body>`
3. Set the app accent hue in `:root` (override `--acc1-h`)
4. Add the dark mode toggle (floating button, nav bar, or header)
5. Build navigation using `.nav-mock` / `.nav-item` or `.top-bar` pattern
6. Build pages using only components from Section 5
7. For charts, use Chart.js with KPAL palette and KALA tooltip/grid styling
8. Test at 375px, 768px, 1024px, 1440px

---

## 14. Migration checklist

- [ ] Link `kala-system-v4.css` (replaces all previous versions)
- [ ] Add aurora HTML as first children of `<body>`
- [ ] Set `body { background: transparent; overflow-x: hidden }`
- [ ] Add dark mode toggle + JS from Section 3
- [ ] Replace hardcoded colours with CSS variable tokens
- [ ] Replace all shadows with `var(--clay-*)` tokens (v4 softened values)
- [ ] Replace `border-radius` values with `var(--r-*)` tokens
- [ ] Replace `<select>` with `.clay-select` (frost glass dropdown)
- [ ] Replace `<input type="date">` with `.clay-date` (frost glass calendar)
- [ ] Single `.tbl-scroll` for each table — remove any inner wrapper
- [ ] Tabs: shadow on `.tabs` container, `box-shadow:none` on `.tab`
- [ ] Add `touch-action: manipulation` to all interactive elements
- [ ] Apply frost glass (`--glass-*`) to overlays, nav, and floating elements
- [ ] Ensure aurora blobs are static (no `animation` property)
- [ ] Verify: no `z-index` on `.layout`
- [ ] Verify: no `overflow: hidden` on `.card`
- [ ] Verify: dark mode uses `removeProperty`, not `setProperty`
- [ ] Clear container inline styles before injecting dynamic content
- [ ] Test 375px viewport

---

## 15. File Reference

| File | Purpose |
|------|---------|
| `kala-system-v4.css` | Complete stylesheet — all tokens, components, responsive rules |
| `KALA Design System v4.html` | Interactive component reference (browse all components) |
| `Material Inward Dashboard.html` | Production example — complex analytics dashboard |
| `ds-tokens.jsx` | Token visualization components (colors, type, shadows, radii, spacing) |
| `ds-components.jsx` | Core component demos (buttons, inputs, controls, badges, avatars, cards, tables) |
| `ds-extras.jsx` | Extended components (tooltips, toasts, breadcrumbs, pagination, accordion, etc.) |
| `ds-charts.jsx` | Chart components (bar, line, area, donut, gauge, scatter, sparkline) |
| `ds-patterns.jsx` | Page patterns (error/404, onboarding, loading, settings) |

---

*Reference: `KALA Design System v4.html` — KALA Standard Design v4.0*
