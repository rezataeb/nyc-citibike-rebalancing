"""Weather scenario presets for Investigator Mode Phase 4.

Illustrative fixed scenarios, not computed from data -- but still
produced by a pipeline module (matching every other data/*.json file's
convention) rather than hand-written JSON, for consistency and
reproducibility of how the file gets regenerated.

UNITS: Celsius/mm internally, matching every other file in this project
(weather.py/gbm.py/elasticities.json all use Open-Meteo's native units)
-- NOT the Fahrenheit/inches shown in Investigator_Mode_Guideline.md's
own contract example, which doesn't match this project's actual units
anywhere. The dashboard converts to degF only for display, never for
the underlying projection math.

'ideal' IS THE FIXED REFERENCE POINT, not just another preset -- Phase
4's dashboard projection subtracts every other preset's values against
it (delta_feature = scenario_value - ideal_value), so "ideal" always
produces exactly zero projected adjustment by construction, stronger
than the Guideline's own "roughly matches baseline" verify wording.

'heat_wave' WAS DELIBERATELY EXCLUDED through Session 25, RE-EVALUATED AND
UNBLOCKED in Session 43 -- not simply forgotten about or always fine. The
original concern (pipeline/elasticities.py's old SPARSE_GRID_CAVEAT) was
real for the pipeline that existed then: temp_mean_c had only 5 distinct
values in the ENTIRE Feb+April training panel (a per-month/day-type
aggregate), and gbm.py's partial dependence curve showed a sharp, isolated
jump right at the single highest observed point (18.7C) -- a sparse-grid
artifact, not a real trend. That specific failure mode cannot occur with
the CURRENT pipeline: Session 27 replaced the PDP-off-a-GBM approach with
a direct linear regression (temp_elasticity is one global slope fit across
every real daily observation, not a tree's partial dependence), and gbm.py
itself was deleted outright in Session 33 -- there is no partial dependence
curve left to show an artifact in. Checked the real current full-year data
before unblocking this, not assumed fixed: OBSERVED_TEMP_RANGE_C below is
verified against 788,979 real per-(station,date) rows spanning the full
Jul 2025-Jun 2026 year, 435 distinct real daily temperatures, not 5. A
'hot_day' preset near the real observed max is genuinely within the
observed range now, not an extrapolation -- see NOTES and
tests/test_scenario_presets.py for the specific values.
"""

from __future__ import annotations

import json
from pathlib import Path

SCENARIO_PRESETS_PATH = Path(__file__).resolve().parent.parent / "data" / "scenario_presets.json"

# Real observed daily temp_mean_c range across the full Jul 2025-Jun 2026
# year, all 18 real weather zones -- verified 2026-07-24 (Session 43) via
# pipeline.elasticities.load_daily_weather_panel(), 788,979 real rows, 435
# distinct real values. A dated fact, not a permanent guarantee: re-verify
# against the real data if the full-year window ever rolls forward.
OBSERVED_TEMP_RANGE_C = (-14.3, 32.3)

# Converted from the Guideline's own Fahrenheit/inch example values:
# 60F->15.6C, 28F->-2.2C, 72F->22.2C; 0.4in->10.2mm, 0.3in->7.6mm.
# hot_day: 31.0C, deliberately inside OBSERVED_TEMP_RANGE_C's real max
# (32.3C, 2025-07-29) rather than reaching for the original spec's 35C --
# staying inside the observed range is the entire point of unblocking this.
PRESETS = [
    {"id": "rain_day", "label": "Steady rain", "temp_c": 15.6, "precip_mm": 10.2},
    {"id": "snow_day", "label": "Snow event", "temp_c": -2.2, "precip_mm": 7.6},
    {"id": "ideal", "label": "Ideal riding weather", "temp_c": 22.2, "precip_mm": 0.0},
    {"id": "hot_day", "label": "Hot day", "temp_c": 31.0, "precip_mm": 0.0},
]

REFERENCE_PRESET_ID = "ideal"

NOTES = (
    "Units are Celsius/mm, matching every other file in this project "
    "(Open-Meteo's native units) -- NOT the Fahrenheit/inches shown in "
    "Investigator_Mode_Guideline.md's own contract example. 'ideal' "
    "(reference_preset_id) is the fixed reference point the dashboard's "
    "weather-scenario projection subtracts every other preset against; "
    "it is not itself a projected adjustment. 'hot_day' (31.0C) was added "
    "in Session 43 after re-checking a Session 25 exclusion against the "
    "current pipeline: the original concern (pipeline/gbm.py's sparse "
    "partial-dependence curve, only 5 distinct training points) was real "
    "at the time but cannot occur with the current direct linear-"
    "regression elasticity fit, and gbm.py itself was deleted in Session "
    "33. The real full-year daily temperature range is -14.3C to 32.3C "
    "(788,979 real observations, 435 distinct values, verified 2026-07-24) "
    "-- 31.0C sits inside that range, not beyond it."
)


def build_scenario_presets() -> dict:
    return {
        "presets": PRESETS,
        "reference_preset_id": REFERENCE_PRESET_ID,
        "notes": NOTES,
    }


if __name__ == "__main__":
    payload = build_scenario_presets()

    SCENARIO_PRESETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCENARIO_PRESETS_PATH.write_text(json.dumps(payload, indent=2))

    for preset in PRESETS:
        print(f"  {preset['id']}: {preset['label']} -- {preset['temp_c']}C, {preset['precip_mm']}mm")
    print(f"wrote {SCENARIO_PRESETS_PATH}")
