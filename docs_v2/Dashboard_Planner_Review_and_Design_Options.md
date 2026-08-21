# Dashboard Review for the DOT Planner — Data Accuracy & Design Options

*Working analysis and decision document. Citi Bike Rebalancing Explorer.*
*Prepared July 2026. Nothing here is implemented yet — these are proposals to react to.*

---

## Summary

This document answers four things asked of the dashboard, in the order they should be
read:

1. **How accurate the data and predictions actually are**, per Investigator feature — the
   grounded part, which should drive every design decision below.
2. **What the right sidebar should show** to be useful to a DOT planner.
3. **What value Investigator Mode can bring** to a planner checking scenarios.
4. **How to redesign the top-left controls** (Individual/Grouped + the time/period selector).

The headline finding, because it shapes everything else:

> **The dashboard is a *climatology* tool — what a station *typically* does — not a
> next-day forecaster.** Those are two different accuracy questions, and conflating them is
> the main way a planner could be misled by the numbers.

---

## Part 1 — How accurate is the data, per feature

### 1.1 Single-day flow is almost pure noise; the *averaged* curve is what's reliable

At the raw `(station, date, hour)` grain, the mean absolute net flow is **2.23 bikes**, and
the best forecast (seasonal-naive) has **MAE 1.95** — the error is roughly **88% of the
signal**. No model beats it out-of-sample (that is the honest headline shown in the model
panel). **You cannot tell a planner what a specific station will do next Tuesday at 8am.**

But the dashboard does not show single-day predictions — it shows the **averaged curve** (the
trailing mean over many real days). How reliable that displayed number is depends entirely on
the slice the planner has selected:

| View (period selector) | Days averaged (median) | Std. error of the shown number | Stations whose deficit/surplus **sign is statistically confident** (\|mean\| > 2·SE) |
|---|--:|--:|--:|
| **All-period average** (default) | ~84 | 0.19 bikes/hr | **93%** |
| **Season** | ~22 | 0.34 bikes/hr | **82%** |
| **Single month** | ~8 | 0.50 bikes/hr | **60%** |

**Implication:** the default view is trustworthy for prioritization, but drilling down to a
single month means **~40% of "signals" are no longer sign-confident.** That is a concrete
design cue (see Part 2C and Part 4).

### 1.2 The three Investigator features have fundamentally different accuracy *characters*

| Feature | What it really is | How much to trust it |
|---|---|---|
| **Equity thresholds** | Deterministic geometry (haversine distances) | **Exact** counts. The uncertainty is *data*, not statistics: schools are a **2019–2020** vintage layer; 216 NYCHA developments / 1,899 schools / 2,120 subway entrances; and "within 300 m" ≠ "actually served." Trust the number; caveat the data currency. |
| **Fleet simulator** | Deterministic *capacity math* on a reliable flag | The **flag** (which stations are AM-deficit) rests on the reliable all-period average, so *who needs a truck* is solid. But the **serviced counts** (14 → 145) come from a greedy heuristic with a placeholder depot and a 45-stop cap — read them as *"how many stations N trucks × 45 stops can physically touch,"* not an optimized operational plan. This is also why there are no diminishing returns. |
| **Weather scenario** | A genuine statistical model — **precise but weak** | The subtle one. Coefficients are *extremely* tight (temp **+0.034/°C**, CI ≈ ±0.0005) because n is huge — but the regression's **R² is only 0.04–0.07**. Weather explains **~5% of day-to-day variance.** So the scenario is a good estimate of the *average direction and rough size* (ideal→hot ≈ **+11%** magnitude; ideal→rain ≈ **−15%**), but a poor predictor of any single day. **A tight confidence interval ≠ an accurate forecast** — a planner will misread this unless it is shown as a directional band, not a precise number. |

**Trust summary for a planner:**
- **Equity** — trust the number (caveat the vintage).
- **Fleet** — trust *who* needs service; treat *how many get serviced* as capacity illustration.
- **Weather** — trust the *direction and rough size*, not the decimals.

### 1.3 Weather regression, in numbers

| Typology group | n (station-days) | Temp coef (/°C) | Precip coef (/mm) | **R²** |
|---|--:|--:|--:|--:|
| Commuter core | 230,915 | +0.034 | −0.017 | **0.059** |
| Residential feeder | 385,102 | +0.037 | −0.018 | **0.073** |
| Pooled | 616,017 | +0.028 | −0.014 | **0.044** |

Projected average magnitude for the presets (pooled fit): **Ideal (22 °C) ≈ 2.21 · Hot
(31 °C) ≈ 2.45 (+11%) · Steady rain (15.6 °C, 10.2 mm) ≈ 1.88 (−15%).** Real, directionally
sensible, modest — and dwarfed by the ~95% of variance the model does not explain.

---

## Part 2 — Right sidebar: what actually helps a DOT planner

Today the rail shows four readout tiles, two "worst now" lists, Investigator Mode, and the
model panel. It is good, but it is missing the metrics a planner makes decisions on.

### 2A. Upgrade the readout tiles to be decision-relevant

The biggest gap: **imbalance and equity never meet.** The most valuable single number for this
project's own framing is the **intersection**:

- **Deficit stations that are *also* equity-priority** — e.g. *"of 1,037 AM-deficit stations,
  N are within 300 m of NYCHA / a school."* This is the barrier-to-access number. Right now
  equity and deficit sit in separate tiles and never cross.
- **Total system pressure** — the sum of \|deficit\| bikes across flagged stations: one
  "how big is the problem" number that moves as the planner scrubs time.
- **Concentration** — *"top 20 stations = X% of total deficit,"* which tells a planner whether
  targeting a handful of stations is worthwhile.
- **Confidence / coverage** — *"this slice averages ~8 days"* when they drill to a month, so
  they know the number just got noisier.

### 2B. Make the "worst now" lists planner-grade

Add, per row: an **equity flag icon** (near NYCHA / school), and a **reliability marker**
(is this a chronic imbalance or a noisy single-month reading?). Optionally a **peak hour** so
they know *when* to send a truck.

### 2C. Add a confidence surface

When the sign is not statistically confident (`|mean| < 2·SE`, common at the month grain),
de-emphasize or mark those stations rather than showing them as equal-weight deficits.

---

## Part 3 — Investigator Mode: where the planning value is

Reframe the three existing controls around planner decisions, and consider new modes.

- **Fleet → "Coverage & gap" tool.** The most useful reframing: *"N trucks reach X of 1,037
  deficit stations — and Y of the equity-priority ones. Here are the stations left unserved."*
  The **residual gap** and the **equity reach** are the real planning outputs, not the serviced
  count alone.
- **Weather → "Stress test."** Given the low R², do not sell precision. Sell *"on a hot day,
  how many more stations tip into deficit?"* as a **range**. Value = resilience planning, not
  point prediction.
- **Equity thresholds → "Policy sensitivity."** Value = *"if we define 'near NYCHA' as 500 m
  instead of 300 m, the flagged population changes by N"* — genuinely useful for setting the
  policy threshold itself.
- **New ideas worth considering:** a **borough / district filter** (planners work by district);
  an **"expansion targeting" mode** (high transit-gap + equity + latent demand); a **peak-hour
  finder** (which hour has the worst system-wide imbalance).

---

## Part 4 — Top-left controls: redesign options

**Problems today:** Individual/Grouped is now *auto-by-zoom*, so the manual toggle is largely
redundant; the period dropdown crams three granularities (All / seasons / months) into one flat
list; and the temporal controls are split between the top-left card and the bottom time slider.

**How comparable tools handle this (reference):** transportation-planning tools like **Replica /
StreetLight / Remix** separate *"what am I looking at"* (layer/view) from *"what time context"*
(a dedicated date/time panel); weather scrubbers like **Windy** use a single timeline filmstrip;
**MTA / Citi Bike** data views lean on a granularity segmented control (Year → Month → Day). The
common pattern is **one coherent time-context panel, separate from view/layer controls.**

| Option | Idea | Trade-off |
|---|---|---|
| **A — Unified time-context panel** | Group *all* temporal controls together: granularity as a segmented control (**All · Season · Month**) + a secondary picker + day-type + the hour scrubber, in one panel. View/route controls move elsewhere. | Cleanest mental model; biggest layout change. |
| **B — Demote the view toggle** | Since view is auto-by-zoom, replace the manual toggle with a small map chip ("Grouped — zoom in for detail") that only reveals the override on click. Frees the card for temporal controls. | Small change, quick win; keeps most of the current layout. |
| **C — Month filmstrip** | Replace the period dropdown with a horizontal **season/month strip** you click across (and can animate), instead of a hidden dropdown. | Great for "watch the year change"; needs horizontal space. |

**Recommendation:** **B + a lighter version of A** (demote the view toggle, and turn the period
dropdown into a two-level segmented control) is the highest value-to-effort, paired with a
**confidence indicator** driven by the reliability numbers in Part 1.

---

## Decisions on the table

1. **Sidebar** — add the **imbalance × equity intersection** metric (top recommendation)? And
   reliability markers on the worst-now lists?
2. **Investigator** — reframe the existing three (coverage-gap / stress-test / policy-sensitivity),
   or also add a new mode (district filter / expansion targeting)?
3. **Weather honesty** — present it as a **directional band** rather than a precise % (given
   R² ≈ 0.05)?
4. **Top-left** — Option A, B, C, or the B+A blend?

---

## Appendix — how the accuracy numbers were computed (reproducible)

All figures derive from committed data; nothing is estimated.

- **Signal magnitude & MAE grain:** `data/daily_net_flow.parquet` (11,777,290 rows). Mean
  \|net\| and quantiles computed directly on the `net` column. The model MAE (naive 1.95) is
  scored in `pipeline/demand_model.py` against the same single-day `net`, confirming the
  forecast-error-vs-signal ratio.
- **Displayed-curve reliability:** grouped `net` by `(station, [period], day_type, hour)`;
  standard error = std / √n_days; "sign-confident" = `|mean| > 2·SE`, restricted to actionable
  buckets (`|mean| ≥ 1`). Computed at three grains: all-period, season (quarter proxy), month.
- **Weather R²:** OLS of `magnitude ~ 1 + temp_mean_c + precip_mm` on
  `pipeline.elasticities.load_daily_weather_panel(...)` (788,979 station-days across 18 weather
  zones), per typology group and pooled; R² = 1 − SS_res / SS_tot.
- **Equity / fleet counts:** `data/flows.json` equity-join block, `data/route.json`,
  `data/fleet_scenarios.json`.

Reproduce the whole pipeline with `python3 -m pipeline.reproduce_all --skip-download`.
