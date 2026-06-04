# Load Study — Calculation Reference (as implemented)

This is the complete, code-grounded explanation of every calculation the Load Study tab currently performs. It documents the actual implementation in [genset-app/src/App.jsx:912-1053](../genset-app/src/App.jsx#L912-L1053) (the `computeLoadStudy` function), the constants that feed it, and how the resulting numbers are surfaced in the UI and the exported PDF.

For the *theoretical* justification of each step (where the 0.577 √3 factor comes from, why 0.8 PF, etc.) see [LOAD_STUDY_METHODOLOGY.md](./LOAD_STUDY_METHODOLOGY.md). This doc focuses on *what runs* — the formulas, in the order the code executes them, with the exact constants.

---

## 0. Inputs

The calculation takes three inputs:

| Input | Source | Shape |
|---|---|---|
| `items` | `lsItems` state (editable equipment grid) | Array of rows; each row: `{ equipment, qty, kw_each, phase, category, starter, hp, usage }` |
| `dgRatingKva` | Optional override | Number; if null, the auto-selected frame is used |
| `diversityFactor` | `lsDiversity` slider (default `0.75`) | Number in `(0, 1]` |

Row fields:

- `qty` — count of identical units
- `kw_each` — running kW per unit (after BEE-modernised defaults, see [LOAD_STUDY_METHODOLOGY.md §7](./LOAD_STUDY_METHODOLOGY.md))
- `phase` — `"1"` or `"3"` (1-phase vs 3-phase)
- `category` — one of `motor`, `lift`, `compressor`, `lighting`, `nonlinear`, `heating`, `general`, `ac`
- `starter` — one of `DOL`, `StarDelta`, `VFD`, `Soft`, `AutoXfmr40`, `AutoXfmr60`, `AutoXfmr80`, `RotorR`, `UPS`, `None`
- `hp` — only used for lifts (drives the `3 × HP` rule)
- `usage` — display-only string (`Continuous`, `Intermittent`, etc.) — not part of math

---

## 1. Constants

Defined at [App.jsx:881-910](../genset-app/src/App.jsx#L881-L910):

### 1.1 Starter inrush multipliers (`STARTER_MULTIPLIERS`)

| Starter | Multiplier | Notes |
|---|---:|---|
| `DOL` | **6×** | Conservative; PDF allows 6–7× |
| `StarDelta` | **3×** | Slightly higher than the 2.5× in the methodology doc — kept conservative |
| `VFD` | **2×** | |
| `Soft` | **2×** | |
| `AutoXfmr40` | **1.2×** | 40% tap |
| `AutoXfmr60` | **4×** | 60% tap |
| `AutoXfmr80` | **4.5×** | 80% tap |
| `RotorR` | **2×** | Slip-ring with rotor resistance |
| `UPS` | **1.5×** | Electronic, modest in-rush |
| `None` | **1×** | Resistive / no inrush |

> Note: the methodology doc lists Star-Delta at 2.5×; the implementation uses **3×** as a safety-side rounding. If you change it, update [STARTER_MULTIPLIERS](../genset-app/src/App.jsx#L881-L892) and the PDF label string at [App.jsx:1691](../genset-app/src/App.jsx#L1691).

### 1.2 Category → default starter (`CATEGORY_DEFAULT_STARTER`)

Used when a row's `starter` is missing:

| Category | Default starter |
|---|---|
| `motor` | `DOL` |
| `lift` | `VFD` |
| `compressor` | `StarDelta` |
| `lighting`, `nonlinear`, `heating`, `general`, `ac` | `None` |

### 1.3 Power-factor & derate constants

| Constant | Value | Where |
|---|---:|---|
| `PF` | **0.8** lagging | Real-load → apparent-power conversion |
| `DIVERSITY_DERATE` | **0.75** | Second-stage derate (diversity + future headroom) |
| `NONLINEAR_CAP_PERCENT` | **60%** | Rule-of-thumb cap on non-linear share of DG |
| `AC_KW_PER_TON` | `{ "1": 1.75, "3": 3.5 }` | Reserved; not currently invoked inside `computeLoadStudy` — AC rows are entered directly in kW |

### 1.4 Standard Kirloskar frame sizes (snap-up list)

Defined inline at [App.jsx:997](../genset-app/src/App.jsx#L997):

```
3.5, 5, 5.5, 6.3, 7.5, 10, 15, 20, 25, 30, 35, 40, 45, 50,
58.5, 62.5, 82.5, 100, 117, 125, 160, 200, 250, 320, 400,
500, 600, 625, 640, 750, 900, 910, 1000, 1010, 1250, 1500,
2020, 2250, 2500
```

The selection picks the **first frame ≥ minimum required**; if the requirement exceeds 2500 kVA, it caps at 2500.

---

## 2. Row normalisation (per equipment row)

For each input row, the engine computes:

```
totalKw  = qty × kw_each                        // running kW for this row
mul      = STARTER_MULTIPLIERS[starter]         // inrush factor
startKw  = totalKw × mul                        // peak instantaneous kW at switch-on
isMotorLike = category ∈ {motor, lift, compressor}
isNonlinear = category == nonlinear
```

[App.jsx:913-934](../genset-app/src/App.jsx#L913-L934)

---

## 3. Continuous load — the `÷3 → ×√3` phase math

Group rows by phase, then convert the 1-phase total into 3-phase equivalent kW:

```
A = Σ totalKw   over rows with phase == "3"      // 3-phase running kW
B = Σ totalKw   over rows with phase == "1"      // 1-phase running kW
perPhase    = B / 3                              // 1-phase load spread across the three phases
C = 1.732 × perPhase                             // equivalent 3-phase loading (√3 factor)
totalContinuous_kw  = A + C
totalContinuous_kva = totalContinuous_kw / PF    // PF = 0.8
```

[App.jsx:937-944](../genset-app/src/App.jsx#L937-L944)

**Why √3:** an alternator's nameplate kVA is `√3 × V_LL × I_phase`. A balanced 1-phase B kVA presents `B/3` per phase, so its 3-phase equivalent is `√3 × B/3 ≈ 0.577 × B`. Mixed installations get a discount vs. flat summing.

---

## 4. Diversity / future-headroom derate

The second-stage derate from the real customer load study spreadsheet:

```
safeDiversity        = diversityFactor if 0 < diversityFactor ≤ 1 else 0.75
recommendedKva_raw   = totalContinuous_kva / safeDiversity
```

[App.jsx:945-946](../genset-app/src/App.jsx#L945-L946)

- At default `0.75`, this multiplies the continuous kVA by **1.333×**.
- The slider exposes the diversity factor so a sales engineer can tighten or loosen the assumption per site.

This is intentionally **more conservative** than the older PDF "+20% reserve" flat-add convention (which would multiply by 1.20). The bigger derate bakes in diversity, ambient/altitude derate, fuel/quality margin, and 3–5 year future growth.

---

## 5. Lift rule (S4 duty)

Lifts cycle on/off frequently and dissipate real heat in the alternator on each start. The methodology requires:

```
liftHpTotal         = Σ (hp × qty)   over rows with category == "lift"
liftKvaRequirement  = liftHpTotal × 3
```

[App.jsx:949-951](../genset-app/src/App.jsx#L949-L951)

This requirement is **applied to frame selection** (not just checked after the fact) — see §7.

---

## 6. Sequential start simulation (input order)

The most sophisticated piece of the engine. Instead of "all motors start at once" (which never happens in real buildings), the simulation walks the motor-like rows **in the order the user entered them** and tracks the worst single-step demand.

```
motorRows           = rows where isMotorLike AND totalKw > 0
nonMotorBaseline_kw = Σ totalKw   over rows that are NOT motor-like

cumulativeMotorRunning_kw = 0
peakDemand_kva            = nonMotorBaseline_kw / PF     // initial peak with no motors yet
peakStep                  = null

For each motor row r at index idx (input order):
    baseAtStep_kw  = nonMotorBaseline_kw + cumulativeMotorRunning_kw
    stepDemand_kw  = baseAtStep_kw + r.startKw           // already-running + this motor's surge
    stepDemand_kva = stepDemand_kw / PF
    if stepDemand_kva > peakDemand_kva:
        peakDemand_kva = stepDemand_kva
        peakStep       = { idx, equipment, starter, mul, startKw, totalKw, baseAtStep_kw }
    cumulativeMotorRunning_kw += r.totalKw               // this motor now joins the running set
```

[App.jsx:953-988](../genset-app/src/App.jsx#L953-L988)

After the loop:

```
surgeRequirement_kva = peakDemand_kva
surgeRequirement_kw  = peakDemand_kva × PF
```

The **peak step** is the moment the DG is most stressed. It's what gets shown in the "Peak Step" card in the UI and PDF.

**Important:** the simulation iterates in *input order*. The user is expected to enter motors in the actual start sequence (typically biggest motor last so it gets the lightest surge contribution relative to a fully-warm system — but the simulation respects whatever order is given).

---

## 7. Frame selection

The recommended frame must satisfy **three** constraints simultaneously:

```
minFrameKva = max(
    recommendedKva_raw,        // continuous-load sizing (§3–§4)
    liftKvaRequirement,        // lift rule (§5)
    surgeRequirement_kva       // worst-case sequential start (§6)
)

finalKva = first frame in FRAMES list that is ≥ minFrameKva
           (or 2500 if none qualify)
dg       = dgRatingKva (override) ?? finalKva
```

[App.jsx:993-999](../genset-app/src/App.jsx#L993-L999)

This is a key correctness property: surge requirement and lift HP-rule are **inputs to the snap**, not after-the-fact warnings. A site dominated by a single large DOL motor will get an upsized DG, not a "warning" badge on an undersized one.

---

## 8. Per-step margin (after dg is known)

Once the frame is fixed, walk the sequential steps again to compute remaining headroom at each step:

```
For each sequential step s:
    s.remainingKva = dg − s.stepDemandKva

minMargin      = min over all steps of s.remainingKva   (initialised to dg)
sequentialOk   = minMargin ≥ 0
```

[App.jsx:1001-1009](../genset-app/src/App.jsx#L1001-L1009)

Negative margin on any step means that motor cannot start with everything before it already running, given the chosen frame.

---

## 9. Surge OK check

```
surgeOk = surgeRequirement_kva ≤ dg
```

[App.jsx:1008](../genset-app/src/App.jsx#L1008)

Note: this is a nameplate check (kVA ≤ rated). Alternators can momentarily supply ~2.5× rated for transients (see [methodology §0.5](./LOAD_STUDY_METHODOLOGY.md#05-surge-analysis--biggest-motor-started-last)), but the implementation enforces the stricter nameplate comparison — combined with the surge requirement being a *true input* to frame selection, this means by construction `surgeOk` is `true` whenever there's no manual `dgRatingKva` override below the recommendation.

---

## 10. Non-linear cap (electronic-load share)

```
nonlinear_kw      = Σ totalKw   over rows with category == "nonlinear"
nonlinear_kva     = nonlinear_kw / PF
nonlinear_percent = (nonlinear_kva / dg) × 100        // 0 if dg = 0
nonlinearOk       = nonlinear_percent ≤ 60            // NONLINEAR_CAP_PERCENT
```

[App.jsx:1012-1015](../genset-app/src/App.jsx#L1012-L1015)

The 60% cap is the working-sheet rule of thumb. More granular bridge-specific caps (12-pulse 90%, 6-pulse 60%, 3-pulse 35%, VFD-controlled drive 50%) are documented in [methodology §0.7](./LOAD_STUDY_METHODOLOGY.md#07-non-linear-ups-bridge-caps-pdf-reference) but not currently differentiated in code.

---

## 11. Lift rule pass/fail

```
liftOk = (liftHpTotal == 0)  OR  (dg ≥ liftKvaRequirement)
```

[App.jsx:1018](../genset-app/src/App.jsx#L1018)

Always `true` by construction when frame selection isn't manually overridden, because `liftKvaRequirement` is already a `max(...)` input to `minFrameKva`.

---

## 12. % Loading (sanity band 60–80%)

```
loadingPercent = (totalContinuous_kva / dg) × 100     // 0 if dg = 0
```

[App.jsx:1021](../genset-app/src/App.jsx#L1021)

Interpretation (used by the PDF colour-coding at [App.jsx:1740](../genset-app/src/App.jsx#L1740)):

| Band | Meaning | Display |
|---|---|---|
| < 60% | Over-spec, wasted capex + diesel | Warning (amber) |
| **60–80%** | **Optimal band** | Success (green) |
| > 80% | Tight — no headroom for future load or peak events | Danger (red) |

---

## 13. Outputs returned by `computeLoadStudy`

The function returns one object with everything the UI and PDF consume:

| Field | Type | Source |
|---|---|---|
| `rows` | array | Normalised equipment rows with `mul`, `startKw`, `isMotorLike`, `isNonlinear` flags |
| `A_3phase_kw` | number | 3-phase running kW total |
| `B_1phase_kw` | number | 1-phase running kW total |
| `perPhase_kw` | number | `B / 3` |
| `C_3phase_equiv_kw` | number | `1.732 × perPhase_kw` |
| `totalContinuous_kw` | number | `A + C` |
| `totalContinuous_kva` | number | `totalContinuous_kw / 0.8` |
| `recommendedKva_raw` | number | `totalContinuous_kva / safeDiversity` |
| `minFrameKva` | number | `max(recommendedKva_raw, liftKvaRequirement, surgeRequirement_kva)` |
| `finalKva` | number | Snapped to next standard Kirloskar frame |
| `peakStep` | object\|null | Details of the worst single step (equipment, starter, surge kW, base load) |
| `surgeRequirement_kw` / `surgeRequirement_kva` | number | Peak instantaneous demand |
| `surgeOk` | bool | `surgeRequirement_kva ≤ dg` |
| `nonlinear_kw` / `nonlinear_kva` / `nonlinear_percent` | number | Non-linear share |
| `nonlinearOk` | bool | `nonlinear_percent ≤ 60` |
| `liftHpTotal` | number | Σ lift HP × qty |
| `liftKvaRequirement` | number | `3 × liftHpTotal` |
| `liftOk` | bool | `dg ≥ liftKvaRequirement` (or no lifts) |
| `sequentialSteps` | array | Per-motor step record: `{ name, runningKva, startKva, cumulativeBeforeKva, stepDemandKva, remainingKva }` |
| `sequentialOk` | bool | `minMargin ≥ 0` |
| `minMargin` | number | Smallest remaining kVA across all motor steps |
| `loadingPercent` | number | `(totalContinuous_kva / dg) × 100` |
| `PF` | number | 0.8 (for display) |
| `DIVERSITY_DERATE` | number | The factor actually used (echoes input or 0.75) |
| `NONLINEAR_CAP_PERCENT` | number | 60 (for display) |

[App.jsx:1023-1052](../genset-app/src/App.jsx#L1023-L1052)

---

## 14. End-to-end worked example

Replaying the one-minute scenario from [methodology §0.10](./LOAD_STUDY_METHODOLOGY.md#010-worked-example-one-minute) through the actual code:

**Inputs:**

| Equipment | Qty | kW each | Phase | Category | Starter | HP |
|---|---:|---:|:-:|---|---|---:|
| HVAC compressor | 1 | 15.0 | 3 | compressor | StarDelta | — |
| 1φ misc loads | 1 | 7.5 | 1 | general | None | — |
| Lift | 1 | 7.5 | 3 | lift | VFD | 10 |
| Water pump | 1 | 3.7 | 3 | motor | DOL | 5 |
| Server rack | 1 | 8.0 | 3 | nonlinear | None | — |

**Row normalisation (§2):**

| Equipment | totalKw | mul | startKw |
|---|---:|---:|---:|
| HVAC compressor | 15.0 | 3 | 45.0 |
| 1φ misc | 7.5 | 1 | 7.5 |
| Lift | 7.5 | 2 | 15.0 |
| Water pump | 3.7 | 6 | 22.2 |
| Server rack | 8.0 | 1 | 8.0 |

**Phase math (§3):**

```
A = 15 + 7.5 + 3.7 + 8 = 34.2 kW
B = 7.5 kW
perPhase = 7.5 / 3 = 2.5 kW
C = 1.732 × 2.5 = 4.33 kW
totalContinuous_kw  = 34.2 + 4.33 = 38.53 kW
totalContinuous_kva = 38.53 / 0.8 = 48.16 kVA
```

**Derate (§4) — default 0.75:**

```
recommendedKva_raw = 48.16 / 0.75 = 64.21 kVA
```

**Lift rule (§5):**

```
liftHpTotal        = 10
liftKvaRequirement = 30
```

**Sequential start (§6) — motors in input order: HVAC compressor, Lift, Water pump:**

```
nonMotorBaseline_kw = 7.5 (1φ misc) + 8.0 (server) = 15.5 kW
                       → 15.5 / 0.8 = 19.4 kVA initial peakDemand_kva

Step 1  HVAC compressor:
    base = 15.5
    stepDemand_kw  = 15.5 + 45.0 = 60.5 kW
    stepDemand_kva = 60.5 / 0.8 = 75.6 kVA  ← new peak
    after: cumulativeMotorRunning_kw = 15.0

Step 2  Lift:
    base = 15.5 + 15.0 = 30.5
    stepDemand_kw  = 30.5 + 15.0 = 45.5 kW
    stepDemand_kva = 56.9 kVA
    after: cumulativeMotorRunning_kw = 22.5

Step 3  Water pump:
    base = 15.5 + 22.5 = 38.0
    stepDemand_kw  = 38.0 + 22.2 = 60.2 kW
    stepDemand_kva = 75.3 kVA

surgeRequirement_kva = 75.6 kVA  (HVAC step)
peakStep = HVAC compressor step
```

**Frame selection (§7):**

```
minFrameKva = max(64.21, 30, 75.6) = 75.6 kVA
finalKva    = next ≥ 75.6 in frames list = 82.5 kVA
dg          = 82.5 kVA
```

**Per-step margin (§8):**

```
Step 1 remainingKva = 82.5 - 75.6 = 6.9 kVA
Step 2 remainingKva = 82.5 - 56.9 = 25.6 kVA
Step 3 remainingKva = 82.5 - 75.3 = 7.2 kVA
minMargin = 6.9 kVA  → sequentialOk = true
```

**Non-linear (§10):**

```
nonlinear_kva     = 8 / 0.8 = 10 kVA
nonlinear_percent = 10 / 82.5 × 100 = 12.1%   → nonlinearOk = true
```

**% Loading (§12):**

```
loadingPercent = 48.16 / 82.5 × 100 = 58.4%   → just below the 60-80% band (over-spec by ~2%)
```

**Verdict:** 82.5 kVA frame, surge-limited (HVAC start drives the size), 12% non-linear (well within cap), 58% loading (slightly over-spec — could justify dropping to next-smaller frame only if surge requirement is reduced via softer starter on HVAC).

---

## 15. What this implementation does *not* yet do

Items from the methodology doc that aren't enforced in code today:

| Methodology rule | Status |
|---|---|
| Bridge-specific non-linear caps (12-pulse 90%, 6-pulse 60%, 3-pulse 35%, VFD-controlled 50%) | Single 60% cap used regardless of bridge type |
| Reciprocating compressor `running ≤ 33% / 66% of DG` (squirrel cage / slip-ring) | Not enforced (category exists, no rule check) |
| Transient cap = `2.5 × DG_kVA` as the surge headroom (vs nameplate) | Code compares surge against nameplate kVA, which is stricter |
| `AC_KW_PER_TON` ton-based AC sizing | Constant defined but unused — AC rows are entered as kW directly |
| Alternative PDF "+20% reserve" flat-add convention | Not exposed; only the `÷ diversityFactor` two-stage chain is used |

These are tracked as potential extensions; current behaviour errs on the conservative side in every case.

---

## 16. Where the numbers surface in the UI

| Number | UI location | PDF location |
|---|---|---|
| Equipment grid (editable) | Load Study tab — top section | "Equipment Load Breakdown" table |
| A / B / perPhase / C | Live-recalc panel — "Phase Math" row | "Phase Math (per LOAD_STUDY_METHODOLOGY)" 4-up |
| Total continuous kW / kVA | Live-recalc panel — sizing chain | "Sizing Chain" 3-up |
| Recommended kVA (raw) | Live-recalc panel — middle of sizing chain | Same |
| Final frame | Live-recalc panel — highlighted | "Sizing Chain" + "Best Kirloskar Match" hero |
| Peak step (equipment, starter, base, surge, verdict) | Live-recalc panel — "Peak Step" card | "Peak Step in Input Sequence" table |
| % Loading, non-linear %, lift rule, sequential margin | Live-recalc panel — constraint chips | "Constraint Checks" table |
| Best Kirloskar match | Top hero in panel | "Best Kirloskar Match" hero box |

The PDF export at [App.jsx:1687-1808](../genset-app/src/App.jsx#L1687-L1808) prints the same numbers in print-safe styling (CSS variables replaced with hex literals because they don't resolve in a popup print window).
