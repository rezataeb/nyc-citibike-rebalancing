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

'heat_wave' IS DELIBERATELY EXCLUDED from this list -- see PROGRESS.md's
Deferred list. Not simply "outside the observed range" like an ordinary
extrapolation concern: pipeline/elasticities.py's own SPARSE_GRID_CAVEAT
found temp_mean_c has only 5 distinct values in the ENTIRE Feb+April
training panel (a per-month/day-type aggregate, not a per-observation
feature), and the partial dependence curve itself shows a sharp,
isolated jump right at the single highest observed point (18.7C) --
consistent with a sparse-grid artifact, not a real trend. A 95F/35C
heat_wave scenario would extrapolate from exactly that unreliable
segment, a qualitatively bigger problem than the milder extrapolation
the remaining presets already carry. If a warm-weather preset is wanted
later, a moderate "hot day" near the actual observed max (~18-20C)
would rest on much firmer ground.
"""

from __future__ import annotations

import json
from pathlib import Path

SCENARIO_PRESETS_PATH = Path(__file__).resolve().parent.parent / "data" / "scenario_presets.json"

# Converted from the Guideline's own Fahrenheit/inch example values:
# 60F->15.6C, 28F->-2.2C, 72F->22.2C; 0.4in->10.2mm, 0.3in->7.6mm.
PRESETS = [
    {"id": "rain_day", "label": "Steady rain", "temp_c": 15.6, "precip_mm": 10.2},
    {"id": "snow_day", "label": "Snow event", "temp_c": -2.2, "precip_mm": 7.6},
    {"id": "ideal", "label": "Ideal riding weather", "temp_c": 22.2, "precip_mm": 0.0},
]

REFERENCE_PRESET_ID = "ideal"

NOTES = (
    "Units are Celsius/mm, matching every other file in this project "
    "(Open-Meteo's native units) -- NOT the Fahrenheit/inches shown in "
    "Investigator_Mode_Guideline.md's own contract example. 'ideal' "
    "(reference_preset_id) is the fixed reference point the dashboard's "
    "weather-scenario projection subtracts every other preset against; "
    "it is not itself a projected adjustment. A 'heat_wave' preset (95F/"
    "35C in the original spec) is deliberately excluded -- see "
    "PROGRESS.md's Deferred list and pipeline/elasticities.py's own "
    "SPARSE_GRID_CAVEAT: the elasticity fit it would extrapolate from "
    "has only 5 distinct training points and a sharp, likely-artifactual "
    "jump right at its own top observed value (18.7C), a qualitatively "
    "bigger problem than the milder extrapolation rain_day/snow_day "
    "already carry."
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
