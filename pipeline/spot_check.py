"""Ground-truth spot checks: assert pipeline output against facts a reviewer
could verify independently, without touching this repo's own code.

Every real bug found across recent sessions -- corrupted LA/(0,0) station
coordinates, the stale school-vintage caveat, weather zones skewed toward
station-dense areas -- was caught by checking a specific claim against
reality, not by trusting a prior result. This module turns that into a
standing, repeatable check instead of an accident of investigation.

Each check below is deliberately picked to be verifiable from outside this
pipeline: real NYC geography (the Financial District is an office district;
Stuyvesant Town is a large purely-residential complex -- public knowledge,
not derived from any clustering this pipeline does), a real federal holiday
(Thanksgiving), and a real seasonal fact (fewer people bike in NYC winters
than summers). None of these draw on pipeline/station_typology.py's own
cluster_name labels -- checking a station's data against its own derived
label would be circular, not independent verification.

Failures here are real findings to report, not thresholds to quietly loosen
until they pass -- see CLAUDE.md's "never invent numbers" rule.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FLOWS_PATH = DATA_DIR / "flows.json"
DAILY_NET_FLOW_PATH = DATA_DIR / "daily_net_flow.parquet"

AM_HOURS = (7, 8, 9)
PM_HOURS = (17, 18, 19)


class SpotCheckResult(NamedTuple):
    name: str
    passed: bool
    detail: str


# Real, independently-identifiable NYC locations -- not derived from this
# pipeline's own clustering. Financial District stations are a real office
# core (NYSE is literally on Broad St); Stuyvesant Town is a real, large,
# purely-residential apartment complex (no offices). Expected direction is
# stated from that real-world knowledge, then checked against the data.
KNOWN_STATION_FACTS = [
    {
        "station_id": "4920.13",
        "why": "South St & Broad St -- NYC Financial District, a real office core (NYSE is on Broad St)",
        "expect": "fills_am_drains_pm",
    },
    {
        "station_id": "4962.08",
        "why": "Broad St & Bridge St -- NYC Financial District, a real office core",
        "expect": "fills_am_drains_pm",
    },
    {
        "station_id": "5854.10",
        "why": "Stuyvesant Walk & 1 Ave Loop -- inside Stuyvesant Town, a large purely-residential complex, no offices",
        "expect": "drains_am_fills_pm",
    },
]

# A real major US holiday inside the full-year window (Jul 2025-Jun 2026)
# with a low-friction comparison: two flanking Thursdays, same day-of-week,
# same season, no other confound.
HOLIDAY_CHECK = {
    "holiday_date": "2025-11-27",  # Thanksgiving 2025
    "comparison_dates": ["2025-11-20", "2025-12-04"],
    "min_dip_fraction": 0.3,  # real-world expectation: a big holiday dip, not a marginal one
}

# Real-world fact: NYC cycling drops sharply in winter cold. Checked against
# the same busy Financial District station used above.
SEASONAL_AMPLITUDE_CHECK = {
    "station_id": "4962.08",
    "min_summer_to_winter_ratio": 1.5,
}


def am_pm_net(curve: list[float], am_hours: tuple[int, ...] = AM_HOURS, pm_hours: tuple[int, ...] = PM_HOURS) -> tuple[float, float]:
    """Sum of net flow over the AM window and the PM window of a 24-value weekday curve."""
    am_net = sum(curve[h] for h in am_hours)
    pm_net = sum(curve[h] for h in pm_hours)
    return am_net, pm_net


def check_station_direction(stations: dict, station_id: str, why: str, expect: str) -> SpotCheckResult:
    """Check one known station's real weekday AM/PM net-flow direction against
    a real-world expectation stated independently of this pipeline's own
    typology labels.
    """
    name = f"station direction: {station_id} ({why})"
    if station_id not in stations:
        return SpotCheckResult(name, False, "station not found in flows.json -- roster may have changed")

    am_net, pm_net = am_pm_net(stations[station_id]["weekday"])
    if expect == "fills_am_drains_pm":
        passed = am_net > 0 and pm_net < 0
    elif expect == "drains_am_fills_pm":
        passed = am_net < 0 and pm_net > 0
    else:
        raise ValueError(f"unknown expectation: {expect}")

    detail = f"AM(7-9) net={am_net:+.3f}, PM(17-19) net={pm_net:+.3f}, expected {expect}"
    return SpotCheckResult(name, passed, detail)


def check_holiday_dip(daily: pd.DataFrame, holiday_date: str, comparison_dates: list[str], min_dip_fraction: float) -> SpotCheckResult:
    """Real system-wide ridership on a known holiday must be meaningfully
    lower than the same day-of-week on flanking non-holiday weeks.
    """
    name = f"holiday dip: {holiday_date} vs. {comparison_dates}"
    daily_totals = daily.groupby("date")["departures_count"].sum()

    holiday_ts = pd.Timestamp(holiday_date)
    if holiday_ts not in daily_totals.index:
        return SpotCheckResult(name, False, f"{holiday_date} not present in daily_net_flow.parquet")

    comparison_ts = [pd.Timestamp(d) for d in comparison_dates]
    missing = [d for d, ts in zip(comparison_dates, comparison_ts) if ts not in daily_totals.index]
    if missing:
        return SpotCheckResult(name, False, f"comparison date(s) missing from data: {missing}")

    holiday_total = daily_totals[holiday_ts]
    comparison_mean = sum(daily_totals[ts] for ts in comparison_ts) / len(comparison_ts)
    dip_fraction = 1 - (holiday_total / comparison_mean)
    passed = dip_fraction >= min_dip_fraction

    detail = f"{holiday_date}={holiday_total:,} vs. flanking mean={comparison_mean:,.0f} -> dip={dip_fraction:.1%} (need >={min_dip_fraction:.0%})"
    return SpotCheckResult(name, passed, detail)


def check_seasonal_amplitude(stations: dict, station_id: str, min_ratio: float) -> SpotCheckResult:
    """Real summer ridership amplitude at a station must be meaningfully
    higher than real winter amplitude -- fewer people bike in NYC winters.
    """
    name = f"seasonal amplitude: {station_id} summer vs. winter"
    if station_id not in stations:
        return SpotCheckResult(name, False, "station not found in flows.json -- roster may have changed")

    seasons = stations[station_id].get("seasons", {})
    if "summer" not in seasons or "winter" not in seasons:
        return SpotCheckResult(name, False, "station is missing summer/winter season data")

    summer_amp = sum(abs(x) for x in seasons["summer"]["weekday"]) / 24
    winter_amp = sum(abs(x) for x in seasons["winter"]["weekday"]) / 24
    ratio = summer_amp / winter_amp if winter_amp else float("inf")
    passed = ratio >= min_ratio

    detail = f"summer mean|net|={summer_amp:.3f}, winter mean|net|={winter_amp:.3f} -> ratio={ratio:.2f}x (need >={min_ratio}x)"
    return SpotCheckResult(name, passed, detail)


def run_all_checks(stations: dict, daily: pd.DataFrame) -> list[SpotCheckResult]:
    """Run every spot check and return all results, passing or failing."""
    results = []
    for fact in KNOWN_STATION_FACTS:
        results.append(check_station_direction(stations, fact["station_id"], fact["why"], fact["expect"]))
    results.append(
        check_holiday_dip(daily, HOLIDAY_CHECK["holiday_date"], HOLIDAY_CHECK["comparison_dates"], HOLIDAY_CHECK["min_dip_fraction"])
    )
    results.append(
        check_seasonal_amplitude(
            stations, SEASONAL_AMPLITUDE_CHECK["station_id"], SEASONAL_AMPLITUDE_CHECK["min_summer_to_winter_ratio"]
        )
    )
    return results


if __name__ == "__main__":
    flows_payload = json.loads(FLOWS_PATH.read_text())
    daily_panel = pd.read_parquet(DAILY_NET_FLOW_PATH)

    results = run_all_checks(flows_payload["stations"], daily_panel)

    print(f"{'PASS' if all(r.passed for r in results) else 'FAIL'} -- {sum(r.passed for r in results)}/{len(results)} checks passed\n")
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"[{status}] {r.name}")
        print(f"       {r.detail}")

    if not all(r.passed for r in results):
        raise SystemExit(1)
