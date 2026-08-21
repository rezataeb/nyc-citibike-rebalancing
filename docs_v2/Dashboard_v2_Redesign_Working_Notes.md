# Dashboard v2 — Redesign Working Notes & Open Decisions

**Status:** direction (§0) decided 2026-08-10. **Building has started** — incremental revision
of `dashboard2.html` in place, not a rewrite. Done so far: Individual/Grouped toggle removed
(S47), `route.json` retired (S48), then **clustering and the drawn route both cut entirely, plus
click tolerance and over-plot fade added (S49)**. **Metric set and layout locked 2026-08-21 (see
banner) — ribbon build starts next.** Handoff banner below refreshed 2026-08-21.
**Working file:** `dashboard2.html` (the canonical dashboard going forward, not `dashboard.html`).
**Purpose:** capture every concern and idea from the current design discussion so it can be
picked up tomorrow without re-deriving it. This is a living scratchpad, not a polished deliverable.
Also the raw material for an eventual **"metrics rationale" report** — what we chose, why, and how
each is sourced.

> ### ⭐ FIRST PRIORITY WHEN YOU'RE BACK  *(handoff refreshed 2026-08-21, after Session 50)*
> **The ribbon is BUILT (Session 50). Next: the other two thirds of Option A** — dock the
> Investigator, and turn Worst-stations into the pull-up drawer. One per session.
>
> Option A is three moves and only the first is done. The metric ribbon now occupies the masthead;
> the Investigator and the Worst lists are still in the right rail exactly as they were, so the
> "Investigator is below the fold" problem (§1 item 2) is **not yet fixed** — that was always the
> docking step, not the ribbon step.
>
> ⚠️ **One spec change happened during the build, and it is not a deferral — read §4B.** Live tile 3
> could not be built as locked (`Outage hrs / $ (24h)`); the snapshot log's cadence cannot support
> it. It shipped as an observed *rate* instead. This also un-blocked the equity-disparity metric
> (#6), which §4B had listed as depending on the tile that failed.
>
> **Do NOT re-open §0.** The "portfolio vs. ops-oversight tool" fork is **closed** (decided
> 2026-08-10 → *portfolio piece, but FOCUSED*). Any older handoff note pointing at §0 as the
> first decision is stale — the groundwork it was waiting on is done:
> - ✅ §0 direction decided
> - ✅ §4A real SLA sourced and citable (Comptroller "Riding Forward")
> - ✅ §4B metric shortlist drafted, counts re-verified against committed data
> - ✅ §6 two layout options wireframed, with a stated lean
>
> **The two decisions that gated the first line of code — both locked, both now built into
> `dashboard2.html` (Session 50):**
> 1. ✅ **Final metric set — §4B provisional set, as-is** (3 tiles/mode, mode-aware). Live mode:
>    `% Meeting Standard` (hero) · `Fill distribution` · `Outage hrs / $ (24h)`. Historical mode:
>    `Net flow imbalance` (hero) · `Need bikes / need docks` · `Bikes to move`. Equity disparity
>    (#6), MAE (#5), and rides-avoided (#7) stay drawer-only, not headline tiles.
> 2. ✅ **Layout — Option A** (§6): ribbon + permanently docked Investigator + pull-up Worst
>    drawer over the map. Confirms the stated lean; Option B (tabbed right rail) not taken.
>
> ~~3. Show route~~ — **closed in Session 49: the route was CUT, not relocated.** The tour was
> the one thing on the map that looked like an optimization output without being one (no depots,
> no travel times, and every truck hitting an arbitrary 45-stop cap at every fleet size). The
> fleet slider and its counts survive. Any §6 wireframe still showing a "Show route" button is
> out of date.
>
> ⚠️ **One thing Session 49 flagged and did NOT fix** — worth handling while you're in the
> Investigator anyway: the fleet counts ("145 of 1,037 … cleared") carry the same 45-stop-cap
> caveat the route was cut for, and `depot_assumption`/`max_stops_note` appear **nowhere in the
> UI**. That's the last unlabeled claim in the Investigator.
>
> Everything else in §7 is done (Individual/Grouped removal — Session 47) or explicitly deferred
> (metrics-rationale report).
>
> ~~**Read §10 before writing the metric ribbon.**~~ — **spent in Session 50.** Both of its
> findings were acted on: the ribbon's tests are built on the shared harness
> (`tests/test_dashboard2_ribbon.js`, plus a `reliability` option added to
> `tests/dashboard_harness.js`), and the inert-tile problem 10B measured is the thing the ribbon
> fixed. §10 has been corrected in place — it named two test files that no longer exist and a line
> number that had moved.

---

## 0. THE ONE BIG DECISION — ✅ DECIDED

**Decision (2026-08-10): Portfolio piece, but FOCUSED — not broad.**
Keep the reviewer as the audience, but earn credibility through *depth*, not breadth. A portfolio
piece that shows real understanding of the actual job (the enforced SLA, penalties, equity
disparity) beats one that fans out across every feature and clutters. This is a third path between
the two options below: **reviewer audience + ops-tool depth/discipline.** Concretely: don't try to
cover all 3 DOT jobs equally; lead with rebalancing + monitoring, keep the top bar tight.

**Who is this tool actually for?** *(original fork, kept for context)*

| | **Option 1 — Portfolio explorer** (current) | **Option 2 — Ops-oversight tool** (pivot) |
|---|---|---|
| Audience | A hiring reviewer skimming for range | "Imagine this is my actual DOT job" |
| Hero element | The map | A prioritized station **worklist** |
| Primary timescale | Climatology (typical patterns) | Now + next-few-hours first; chronic underneath |
| Top metrics | Equity / transit counts (planning concerns) | Load + live failures + service recovery |
| Equity's role | Headline count | Service-equity **accountability** (are priority stations fixed as fast?) |
| Differentiator | Investigator what-if sandbox | Live worklist + performance loop |
| Can it measure success? | No (no truck logs) | Built to, the moment truck logs exist |
| Scope | All 3 DOT jobs, generalist | One committed persona, the rest cut |

**Honest read:** the pivot is a more impressive, more defensible *real tool*; the portfolio
version is safer if the audience is a reviewer scanning for breadth. They pull in opposite
directions — pick one before designing.

---

## 1. The concerns that started this (from the design discussion)

1. **4 metrics top-right** feel like "one per data source," not "one per decision."
2. **Investigator Mode isn't obvious** — confirmed by screenshot at 1440×900: it sits below the
   two ranked lists, entirely below the fold. A first-time viewer never sees the "differentiator."
3. **Top-left controls** (Individual/Grouped, Show route) — are they necessary? (verdicts in §5)
4. **General design can be better.**

**My own alternative idea (the starting sketch):** metrics in a horizontal **top ribbon**,
Investigator Mode docked on the **right**, and the Worst-stations list able to **come and go**
(not permanently covering the dashboard).

---

## 2. The 3 DOT jobs — and who actually owns them

From Document 01, the tool claims to support three DOT decisions. Key realization: **they belong
to three different desks**, and only ~1.5 of them are the *rebalancing* person's job.

| Job | Plain meaning | Whose desk |
|---|---|---|
| **Rebalancing prioritization** | Given imbalance, where do the trucks go? Which stations/hours are chronically short of bikes/docks. | **Operations** ✅ (rebalancing) |
| **Expansion & equity targeting** | Where to add stations/docks, and is that growth fair to equity-priority areas? | **Planning / policy** (not rebalancing) |
| **Intervention sizing** | What would a change do before committing (more trucks, weather, thresholds)? | **Split** — fleet = ops/budget; equity thresholds = policy |

**Real-world wrinkle to remember:** Citi Bike rebalancing is *operated by Lyft*, not DOT. DOT's
role is **oversight** — contract performance, service standards, equity accountability, expansion
approval. So "DOT rebalancing staff" are not truck dispatchers; they're overseers asking
*"is the operator keeping the system balanced, everywhere, fairly?"* That reframes the whole tool.

**Why the current tool bundles all three:** it was built as a **portfolio showcase** (breadth =
range), not a role-specific instrument. That's the tension behind the §0 fork.

---

## 3. If built from scratch for a DOT rebalancing-oversight analyst

The persona is an *overseer*, not a dispatcher. Their tool would be:

1. **Worklist-first, not map-first.** The hero is a *ranked list of stations needing attention*
   (severity, bikes-to-move, chronic vs. one-off). The map is context around the list.
2. **Three explicit time horizons:** **Now** (live GBFS) · **Next few hours** (climatology applied
   forward) · **Chronic** (the climatology itself — what fails every week and never gets fixed).
3. **A performance feedback loop — the thing the current tool structurally cannot do.** Answering
   "did rebalancing work?" needs the operator's **truck logs** (non-public today). An honest tool
   is *architected to ingest them on day one* and foregrounds that gap. Without them the tool only
   ever shows demand *pressure*, never service *performance* — for an overseer, that's the whole game.
4. **Equity returns, transformed:** not "where to expand" (planning's job) but **service equity** —
   *are equity-priority stations recovering from failures as fast as everywhere else?* A real
   oversight metric, answerable with existing data (+ truck logs later), and a better home for
   equity than a headline count.
5. **Fleet/capacity sizing tied to a real question:** not "drag a slider," but "what rebalancing
   capacity does the contract need to hit service standards?"

---

## 4. Metric thinking (if value prop = rebalancing + monitoring)

**The key insight:** the biggest problem with today's 4 tiles isn't *which* four — it's that
they're **static**. "1,037 AM deficit baseline" doesn't change with the time slider and is the
same in Historical vs. Live mode. For a monitoring tool that's backwards. **Make the metrics
mode-aware and slice-aware.**

**Historical flow mode (planning / climatology):**
- **Need bikes** — deficit stations *at the current hour/slice* (not a fixed AM number)
- **Need docks** — surplus stations at current slice *(currently missing entirely — half the job)*
- **Bikes to move** — Σ|net| across flagged stations = the **workload** number, ties straight to
  the fleet simulator ("how many trucks clear this load?")

**Live docks mode (monitoring):** ribbon *swaps* to real failures —
- **Empty now** — stations at 0 bikes (a real observed failure, not a proxy)
- **Full now** — stations at 0 docks
- **Network health** — % of stations in a healthy fill band right now
- **As of [time]** — feed freshness (monitoring needs a timestamp)

**Anchor metric to fight for:** **"Bikes to move."** It creates the app's story spine:
> load (ribbon) → how many trucks clear it (Investigator) → the actual tour (route on map) →
> which stations (worst list).

**Equity tension to decide:** this framing *demotes* equity from a headline tile. Legit, but it
pulls against Document 01's equity framing. Recommendation: demote as a headline, **preserve
per-station** (worst-list rows + station detail) — which also stays consistent with the standing
"no map-level equity filter" rule. In the ops-pivot (§0 Option 2), equity comes back as *service
accountability* instead.

---

## 4A. THE REAL SLA — sourced from the NYC DOT / Lyft contract  ⭐

Researched externally 2026-08-10. These are **real, citable contract terms** (NYC Comptroller's
official review), not assumptions — a big credibility upgrade for any SLA-based metric.

| Requirement | Standard | Penalty |
|---|---|---|
| **Rebalancing — peak** | Stations must not be completely full/empty, **8am–8pm Mon–Fri** | **$50/hr/station after 60 min** — *only if adjacent stations also unavailable* |
| **Rebalancing — off-peak** | Same, **8:01pm–7:59am** any day | **$50/hr/station after 120 min** — *only if adjacent also unavailable* |
| **Fleet availability** | ≥90% of fleet Mar–Nov; ≥70% Dec–Feb | $15/bike |
| **Station uptime** | Comms/transaction systems functional | $8/min (system-wide) |
| **Dock repairs** | Within 48h of notification | $10/dock |
| **Customer service** | Answer ≥80% of calls, 7am–7pm | $95/call below threshold |
| **Data reporting** | On the agreed schedule | $50/day late |

**Killer equity finding (from the same report):** the contract has **NO neighborhood-level
performance requirement at all**, and **Bronx riders are 89% more likely to hit an unusable
station**; service is worse in Black, Latino, and low-income areas. → This turns our equity-
disparity metric from "nice idea" into "the exact gap the City Comptroller flagged and the
contract fails to enforce." Strong, citable narrative.

**Honesty note for the "% meeting standard" KPI:** the contract states the rule as a *time-based
penalty* (not full/empty past 60/120 min with neighbor also out), **not** as a "98%/96% target."
So a "% of stations currently within the availability rule" KPI is a legitimate *derived* metric,
but any specific % target is our framing, not contract text — label it as such.

**Sources (for the eventual rationale report):**
- NYC Comptroller — *Riding Forward: Overhauling Citi Bike's Contract for Better, More Equitable
  Service* — the SLA table above:
  https://www.comptroller.nyc.gov/reports/riding-forward-overhauling-citi-bikes-contract-for-better-more-equitable-service/
- NYC Comptroller newsroom — service-reliability declines, esp. low-income neighborhoods:
  https://comptroller.nyc.gov/newsroom/comptrollers-review-of-citi-bike-finds-worrying-decreases-in-service-reliability-under-lyfts-operation-especially-in-low-income-neighborhoods/
- Streetsblog 2026-07-23 — Lower Manhattan full-dock coverage (inside our data window, live-snapshot hook):
  https://nyc.streetsblog.org/2026/07/23/citi-bike-nowhere-to-park-lower-manhattan-lyft-docs-bike-share
- Streetsblog 2023 — what's in the new Citi Bike contract:
  https://nyc.streetsblog.org/2023/11/06/what-we-get-and-dont-get-in-the-new-citi-bike-deal

---

## 4B. Candidate metric shortlist (brainstormed — NOT all adopted)

Seven metric ideas were raised; these are suggestions to draw from, not a spec. Tiered by how real
/ buildable they are. **Final set still to be locked next session (first-priority task).**

| # | Metric | Verdict | Data status |
|---|---|---|---|
| 1 | **% Stations Meeting Standard (now)** | Strong **hero** candidate — SLA now sourced (§4A) | Buildable from live GBFS + nearest-neighbor (we already do NN). "% target" is derived, not contract text |
| 2 | **Outage Hours + Penalty $ (24h)** | Strong — "dollars-real" framing; SLA sourced | Buildable from the 10-min GBFS snapshot log (`snapshots.csv`). System-level OK; per-station reliability weaker (log density) |
| 3 | **Net Flow Imbalance (bikes/day)** | Keep — it's the existing core signal | ✅ real, already in the app |
| 4 | **Live Fill % Distribution** | Keep — instant health check | ✅ real (GBFS). ⚠️ live data *drifts* — never hardcode counts |
| 5 | **Forecast MAE** | Keep, but **drawer not top bar** (credibility anchor) | ✅ real: naive **1.954** / GAM **2.180** / guarded **2.096** |
| 6 | **Equity *disparity*** (outage rate in equity areas vs system avg) | Best equity framing — matches the Comptroller finding | Depends on #2. Counts verified below |
| 7 | **Trips Enabled / Rides Avoided** | Strongest *story*, weakest *ground* — counterfactual | Defer or mark **illustrative** (project can't see failed trips) |

**Numbers verified against committed data (2026-08-10):**
- Equity station counts (from `flows.json` context): **302** near NYCHA (≤300m), **1,537** near
  school (≤300m), **357** subway gap (≥800m), **1,584** NYCHA-or-school. *(User's cited figures
  were correct.)*
- Live fill (from `live_status.json`, snapshot 2026-07-23 23:07 UTC): **145 empty**, 225 full,
  of 2,419 stations with capacity. *(NOT "188 empty / 63 full" — live data drifts every refresh;
  lesson: a live tile is fine, a fixed hardcoded claim is not.)*
- MAE exact: naive **1.954**, GAM **2.180**, guarded GBM **2.096** (not "2.06").

**✅ LOCKED 2026-08-21 — top-bar set, as-is (3 tiles/mode, mode-aware). BUILT in Session 50,
with one forced change to live tile 3:**
- *Live docks mode:* `% Meeting Standard` (hero) · `Fill distribution` · ~~`Outage hrs / $ (24h)`~~
  → **`Observed unusable rate`** (see below — the locked version was not buildable)
- *Historical mode:* `Net flow imbalance` (hero) · `Need bikes / need docks` · `Bikes to move`
- *Drawer / dedicated:* MAE (#5), Equity disparity (#6), Rides-avoided (#7, illustrative)

### ⚠️ Metric #2 could not be built as specced — the snapshot log's cadence forbids it

Measured against the real artifact (`data/gbfs_log/snapshots.csv`) on 2026-08-21:

    190 snapshots over 12 days, ending 2026-07-26 (25 days before the build session)
    gap median 85 min · mean 98 min · max 236 min · only 3 of 189 gaps <= 10 min

`gbfs_logger.py`'s own docstring promised an `empty_minutes.py` to convert the log into empty/full
*minutes*, assuming the 10-minute cron it describes. **The log was never collected at that
cadence.** The contract's thresholds are 60 min (peak) / 120 min (off-peak), so outage hours — and
therefore any `$50/hr` penalty figure — would be an inference at roughly the same resolution as the
thing being measured: a station seen empty at 14:00 and again at 15:15 may have been refilled at
14:05. Separately the log ends 25 days before the build, so the `(24h)` window contained no data.

**What shipped instead — the observation-weighted unusable rate.** Share of station-observations
with 0 bikes or 0 docks; offline observations excluded from both numerator and denominator per the
fixed QC rule. Each row is an independent point-in-time read, needing no duration resolution.

| | |
|---|---|
| System-wide | **12.2%** (54,860 of 448,687 online observations) |
| Per-station | median 9.5% · p90 26.8% · max 76.8% (2,385 stations at ≥20 obs) |
| Source | `pipeline/reliability.py` → `data/reliability.json` |

A first attempt at the replacement was **wrong and worth recording**: "stations unusable in ≥1
snapshot" **saturates at 90.8%** (2,237 of 2,463). Over 12 days nearly every station fails at some
point, so that tile would have read ~91% forever and distinguished nothing. Checking it before
building it was the whole saving.

**This un-blocks #6.** The shortlist table lists Equity *disparity* as "Depends on #2" — i.e.
blocked behind the tile that could not be built. It is not: the Comptroller's own finding ("Bronx
riders are **89% more likely** to hit an unusable station") is a **ratio of observation-weighted
rates**, not a duration. `reliability.json` ships per-station rates, so #6 now needs only the equity
join it already has. **#6 is the best-supported remaining metric in the shortlist**, and it no
longer waits on a recollect.

**The `$ /hr` tile is deferred, not cancelled.** It stays fully specced here, and becomes buildable
the moment the GBFS collector reruns at 10-minute cadence for long enough — a pipeline task, not a
dashboard one.

### `% Meeting Standard` needed a number the contract does not give

The SLA's carve-out ("only if adjacent stations also unavailable") is stated in words; "adjacent"
is never defined in metres. `ADJACENCY_RADIUS_M = 500` in `dashboard2.html` is **ours**, chosen to
sit between this project's existing 300 m and 800 m equity distances, and labeled as ours in the
tile's own tooltip. Live result at the current snapshot: **89.9%** meeting.

### Reconciling the live counts against the verified figures above

The numbers verified above record 145 empty / 225 full of 2,419. The shipped ribbon reads **87
empty / 220 full of 2,317**, and both are right — `hasLiveData()` (the file's pre-existing
predicate, not a new one) requires a flows.json match *and* capacity > 0 *and*
`is_renting && is_returning`:

    2,419  capacity > 0                -> 145 empty, 276 full   (the basis used above)
    2,365  + online only               ->  91 empty, 224 full
    2,317  + present in flows.json     ->  87 empty, 220 full   (the ribbon's basis)

The 92 live stations with no flows.json entry and the 54 offline-and-empty stations are the whole
difference. Not a regression — the stricter denominator is the fixed QC rule.

---

## 5. Top-left controls — verdicts

- **Individual / Grouped toggle → gone, and so is grouped view itself.** ✅ **REMOVED (Session 47),
  then clustering removed outright (Session 49)** — every station is one dot at every zoom, so
  there is no view mode left to toggle. Over-plotting is now handled by zoom-aware marker alpha
  (`overplotAlphaForZoom`) instead of cluster bubbles, and the blue hover polygons went with it
  (they were markercluster's `showCoverageOnHover` default). Original Session 47 reasoning:
  It already auto-switches by zoom (Session 44: grouped when zoomed out, individual at street
  level), so the button was a second control for a decision the map already made. Removed
  outright rather than demoted to a tiny map control. The `state.viewModeUserForced` override
  went with it — a click set it permanently and it was deliberately never cleared on zooming, so
  one stray click silently pinned the view for the whole session. Zoom is now authoritative every
  time, asserted by a five-step zoom walk in the new test.
- **Show route → ❌ CUT ENTIRELY (Session 49).** Earlier notes argued to keep the capability and
  move it; that was overturned on validity grounds, not layout. The drawn tour implied a routing
  engine the project doesn't have: `plan_routes.py` has no depot data (its own `depot_assumption`
  says tours start at "whichever surplus station has the largest remaining forecast surplus -- a
  stopgap simplifying assumption, not a modeled depot"), no travel times, and a 45-stop cap that
  is "a rough single-shift estimate, not a modeled time/distance budget". Decisive evidence:
  **`any_truck_capped` is true at every fleet size 1-10**, so coverage scales linearly at ~14.5
  stations/truck (14 → 31 → 76 → 145) — that is the cap talking, not rebalancing. Everything
  route-related is deleted: the button, the polylines, the numbered stop markers, the legend row,
  `state.route*`, and Session 48's `historicalRouteFrom()`.
  - **What survives:** the fleet-size slider and `renderFleetStats()`. The *which-stations*
    serviced set is real (derived from real net-flow data); only the tour through them was made up.
  - **Still open (flagged S49, not built):** those surviving counts inherit the same cap caveat,
    and `depot_assumption`/`max_stops_note` are surfaced **nowhere in the UI**.
  - The earlier "decoupled from the fleet slider" worry is moot — there is nothing left to couple.

**Net effect of removing/relocating both:** the only top-left controls left are the genuine
time-context ones (weekday/weekend, period) — which could even move down to the timebar strip,
freeing the map's top-left entirely.

---

## 6. Layout options (low-fi wireframes)

> ⚠️ **Both wireframes below predate Session 49** and still show a `[ Show route ]` button in the
> Investigator panel. That control no longer exists. Read the boxes for *layout*, not contents.

### Option A — Ribbon + docked Investigator + pull-up Worst  *(closest to my instinct; current lean)*
```
+--------------------------------------------------------------------------+
| [LOGO] Rebalancing Explorer  [NEED BIKES][NEED DOCKS][TO MOVE][..]  H|Live|
+----------------------------------------------------+---------------------+
|  Weekday >   All-period v                           |  INVESTIGATOR       |
|                                                     |  -----------------  |
|                                                     |  Fleet   [1-10]     |
|                   M A P                             |  Weather [ v ]      |
|                                                     |  Equity  [<-->]     |
|                                                     |  ( diff vs base )   |
|  <---o---> 8AM          [ ___ legend ]              |  [ Show route ]     |
|                                                     |                     |
|      /------- Worst stations  ^ -------\            |                     |
|      (pull-up drawer over map, collapsed)           |                     |
+----------------------------------------------------+---------------------+
```
Metrics get the full-width top ribbon (always visible, mode+slice-aware). Investigator permanently
docked right (fixes visibility). Worst-stations = pull-up drawer from the map's bottom edge —
comes and goes instead of permanently occupying the rail.

### Option B — Ribbon + tabbed right rail  *(Worst and Investigator share space)*
```
+--------------------------------------------------------------------------+
| [LOGO] Title    [NEED BIKES][NEED DOCKS][TO MOVE][HEALTH]         H|Live  |
+--------------------------------------------------+-----------------------+
|                                                  | ( Worst )( Investigate)|  <- tabs
|                                                  | ---------------------- |
|                  M A P                           |  station      -26.6    |
|                                                  |  station      -22.1    |
|  <--o--> 8AM                    [ legend ]        |  ...                   |
+--------------------------------------------------+-----------------------+
```
Right rail has two tabs; Worst and Investigator are peers, one click apart. Solves "worst covers
everything" and "investigator buried," but you can't see both at once.

**✅ LOCKED 2026-08-21: Option A** — keeps Investigator permanently visible (its whole point is
discoverability); the pull-up Worst drawer is the cleanest "come and go." Option B was considered
(tidier, but hides Investigator behind a tab, undercutting the visibility goal) and not taken.

**Build status: 1 of 3 steps done.** Session 50 built the ribbon only. The Investigator and the
Worst lists are still in the right rail exactly as before, so **§1 item 2 ("Investigator isn't
obvious") is still open** — that was always the docking step, not the ribbon step. Don't read the
ribbon landing as Option A being finished.

---

## 7. Open decisions checklist (metrics + layout now decided; the ribbon is built)

- [x] ~~THE FORK: portfolio vs ops-pivot~~ → **DECIDED: portfolio but focused** (§0)
- [x] ~~Lock the final metric set~~ → **DONE 2026-08-21: §4B provisional set, as-is** (3 tiles/mode), and **BUILT (Session 50)** — with live tile 3 forced to change under measurement, see §4B
- [x] ~~Confirm the hero metric~~ → **`% Meeting Standard` (live) / `Net flow imbalance` (historical)**, confirmed by the lock above
- [x] ~~SLA metrics (#1/#2)~~ → **RESOLVED (Session 50), but not as planned.** #1 shipped at **89.9%**, with its 500 m adjacency radius labeled as ours, not the contract's. #2 was **not buildable at all** from the log's ~hourly cadence — replaced by the observed unusable rate (**12.2%**). The `$ /hr` framing is deferred, not cancelled. See §4B.
- [ ] Equity: the disparity metric (#6) stays **drawer-only**, not a headline tile, per the "as-is" lock — but it is **no longer blocked**: §4B had it depending on #2, and Session 50's per-station rates in `reliability.json` supply what it actually needs. Best-supported metric left unbuilt.
- [x] ~~Metric philosophy: adopt the mode-aware + slice-aware swap?~~ → yes, baked into the locked set (§4)
- [x] ~~Individual/Grouped: remove the manual toggle?~~ → **DONE (Session 47).** Toggle and the
      `viewModeUserForced` override field both deleted; view mode is zoom-only. Also produced
      dashboard2's first JS test coverage — that file was later merged into
      `tests/test_dashboard2_map_interaction.js` (Session 49); see §10A's correction.
- [x] ~~Show route: fold into Investigator, or keep separate?~~ → **RESOLVED (Session 48).**
      `route.json` retired; the historical route is now fleet scenario 1, one source, one layer.
      The button's physical placement is still open but is now cosmetic — see §5.
- [x] ~~Layout: Option A vs Option B~~ → **DONE 2026-08-21: Option A locked** (§6). **Step 1 of 3 built (Session 50: the ribbon).** Remaining: dock the Investigator; make Worst a pull-up drawer.
- [ ] (later) Write the **metrics-rationale report**: what we chose, why, sources (§4A) — for the portfolio

---

## 8. Constraints to respect (from CLAUDE.md — don't drift)

- **No map-level equity filter / recolor** — standing scope exclusion, any session.
- **Per-station equity context stays in scope** (detail panel, tooltips, worst-list rows).
- **`dashboard2.html` is the canonical file** for new work.
- **UI default state must be set in JS**, not implied by static HTML markup (recurred 3× before).
- Planning only right now — but the gate has moved: the §0 fork is **decided**, so what blocks
  building is now the metrics set + layout choice (see the ⭐ banner), not the direction.

---

## 9. Housekeeping — two doc folders  ✅ **CLOSED 2026-08-20**

**Resolved: the annotated copy won, and git now records it.** Option 1 of the three that were on
the table ("commit the move") was chosen and executed — nothing here is still open.

- `../docs V2/` (with a space, one level up) — **the doc set's home.** Annotated PDFs (highlights
  + margin comments on docs 01–03) plus `Project_QA_and_Annotations.pdf`. **Outside git, so it
  has no version control at all** — that's the accepted cost of keeping the annotations.
- `app/docs_v2/` — **only this working-notes file**, now tracked (commit `5fb7c3f`).

The 14 deletions were committed in `9fb1d0b`, after verifying where each file actually went:

| What | Where it is now |
|---|---|
| `00_*.{html,pdf}`, `01–04 *.html`, `04_*.pdf`, `assets/report.css` | byte-identical in `../docs V2/` |
| `01`, `02`, `03` `*.pdf` | in `../docs V2/` as **larger annotated re-renders** (e.g. `02`: 384,694 → 899,674 bytes) |
| `Dashboard_Planner_Review_and_Design_Options.md` | ✅ **restored and tracked** — see below |
| `Dashboard_Planner_Review_and_Design_Options.{html,pdf}` | history only, at `7e9f58d` (renders; regenerate from the `.md` if needed) |

✅ **The planner review `.md` was restored and is tracked again.** It is the source of the Session
46 accuracy analysis this document cites as raw material for the metrics-rationale report (§7's
last item): sign-confidence by slice ~93% all-period / ~82% season / ~60% single month, and the
weather regression's R² of only 0.04–0.07 despite very tight CIs. Keeping the *source* under git
while its `.html`/`.pdf` renders stay in history is effectively option 3 of the original three
("track the sources, not the renders"), reached by way of option 1.

⚠️ **Read it for Part 1, not Parts 2 and 4.** Part 1 (accuracy analysis) is durable and still the
best evidence in the project. Parts 2–4 are *proposals* written before Sessions 47–49 — Part 4
("Top-left controls: redesign options") in particular discusses controls that have since been
removed outright, not redesigned.

Not gitignored, deliberately: the paths are empty now, so there is nothing to keep resurfacing, and
an ignore rule would only make a future re-render silently invisible to `git status`.

The two root PDFs (`Project_Summary_and_Roadmap.pdf`,
`AI_Assisted_Development_Retrospective_and_Playbook.pdf`) stay untracked per the standing
instruction. `PROGRESS.md` and `CLAUDE.md` are in `.gitignore` by the same earlier decision.

---

## 10. Findings from Session 47 (verified against the code, not recalled)

Two things surfaced while removing the Individual/Grouped toggle. Both change how the metric-
ribbon work should be planned, so they're recorded here rather than only in PROGRESS.md.

### 10A. ⚠️ `dashboard2.html` had NO JavaScript test coverage

`tests/test_dashboard_slider.js` reads **`dashboard.html`** (`:411`, `:1303`), not
`dashboard2.html`. All 14 of its blocks pass and always would have — none of them load the
canonical file. Session 47 added the first dashboard2 test (`tests/test_dashboard2_view_mode.js`,
zoom-driven view mode only), and Session 48 added a second
(`tests/test_dashboard2_route_source.js`), so coverage was no longer zero — but it was
still narrow, and nothing yet covers the metric bar.

> ⚠️ **Correction (2026-08-21): both filenames above are stale.** Session 49 deleted both and
> replaced them with the single `tests/test_dashboard2_map_interaction.js` — its own header
> documents the merge, and `git log --all` confirms neither `test_dashboard2_view_mode.js` nor
> `test_dashboard2_route_source.js` exists under those names in any commit (they were replaced
> before ever being committed separately). The point stands — coverage is still narrow, nothing
> covers the metric bar yet — only the filenames were wrong.
>
> **Update (Session 50): the metric bar is now covered.** `tests/test_dashboard2_ribbon.js`
> (9 blocks) is built on the shared harness, which gained a `reliability` option and a
> `FAKE_RELIABILITY` fixture for it. Current dashboard2 coverage is that file plus
> `tests/test_dashboard2_map_interaction.js`.

**✅ The harness now exists (Session 48).** `tests/dashboard_harness.js` holds the shared sandbox:
it can boot dashboard2's full inline script (fake `flows.json` / `live_status.json` /
`fleet_scenarios.json`, every other optional file 404ing to exercise graceful degradation, plus
`window`/`location`/`URL`/`URLSearchParams` stubs and a dispatchable `map.on('zoomend')`), and
exposes `loadDashboard2(options)` which runs the script, waits for init, and asserts init logged
nothing. **Build the ribbon's tests on this** — pass payloads via options rather than writing a
third sandbox. `test_dashboard_slider.js` is deliberately not on it (different file, different
sandbox shape); leave that one alone.

Two harness gotchas worth not rediscovering:
- **An auto-creating DOM stub cannot prove absence** — it invents whatever element you ask for.
  Assert removed markup against the raw HTML string, and assert "nothing looks it up" against a
  recorded log of every id passed to `getElementById`.
- **dashboard2's top-level `.catch()` logs and continues**, so a broken init produces a *passing*
  test running on half-initialized state. This actually happened in Session 47. Always assert
  that nothing was `console.error`'d.

### 10B. The metric bar is worse than §4 assumed — 3 of 4 tiles ignore the hour slider

§4 says the tiles are "static." Traced through `renderReadout()` (`dashboard2.html:1431` — shifted
from the originally-cited `:1626` by Session 49's cuts; reverify against the file, don't trust
either line number blindly), the precise position is sharper:

| Tile | What it actually computes | Moves with the hour slider? |
|---|---|---|
| `readout-deficit` | fixed AM (6–9am) baseline, `fleet_scenarios` scenario 1 | ❌ |
| `readout-equity` | NYCHA/school count — responds to equity *thresholds* only | ❌ |
| `readout-subway-gap` | subway-gap count — same | ❌ |
| `readout-filter` | a **slice label** ("8:00 AM, weekday, All period") — not a metric | ✅ (it *is* the label) |

So dragging the hour slider — the dashboard's primary control — changes nothing in the metric bar
except the caption describing where you dragged it. That's a stronger version of §1's "one per
data source, not one per decision," and it's the single highest-value thing the ribbon fixes.

✅ **FIXED in Session 50.** All three ribbon tiles recompute from the live (mode, hour, dayType,
period) slice. `readout-filter` was **deleted rather than relocated**: it existed only because
nothing else moved, and a caption restating the slider position is the same duplication this
masthead already dropped once (see `#title-meta`'s note on the removed "Net flow at HH:MM" line).
Its misleading "current map filter" wording went with it, resolving this section's own rename note
below. The regression is now pinned by test — `test_dashboard2_ribbon.js` asserts the values
*change* across hours and day types, not merely that they render.

**Two corrections to earlier notes:**
- §4B worried the equity-threshold machinery might not exist. It does —
  `state.investigatorState.equityThresholds` is already wired and feeds `computeEquityCounts()`.
  Whatever Investigator Phase 2 still needs, threshold state isn't it.
- `readout-filter`'s label reads "current map filter", which sounds like the excluded equity
  filter. It is **not** — it's the time slice. No conflict with the standing §8 exclusion, and no
  equity filter exists anywhere in dashboard2. Worth renaming the label while building the ribbon,
  since the wording invites exactly the wrong assumption.

### 10C. Unrelated: "the map is empty" was `file://`, not data loss

Opening `dashboard2.html` directly (not over http) makes `fetch('data/flows.json')` fail, so the
page falls back to `DEMO_FLOWS_PAYLOAD`'s five illustrative stations — which reads as an empty map
at city zoom. All data verified intact: `flows.json` 15.6 MB / **2,516 stations** / all four
contract blocks, `live_status.json` 2,461 stations, all eight data files 200 over
`python3 -m http.server`. Always open via a server: `python3 -m http.server`, then
`http://localhost:8000/dashboard2.html`.

Related contract drift found while checking: CLAUDE.md's `flows.json` schema lists `capacity` per
station, but **0 of 2,516 stations carry it** — capacity lives in `live_status.json`. Nothing reads
it from `flows.json` (`contextLines()` documents the omission), so this is a docs fix, not a bug.
