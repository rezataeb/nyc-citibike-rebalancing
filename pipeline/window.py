"""The data window, set in ONE place.

Every pipeline step that needs "which months" or "which dates" reads them
from here, so rolling the project forward is one edit plus one command:

    1. Change WINDOW_END_MONTH (and AVAILABILITY_END) below.
    2. python3 -m pipeline.update_window      # adds/drops trip months
    3. python3 -m pipeline.reproduce_all --skip-download

Method, stated plainly: the trip window is the 12 calendar months ending at
WINDOW_END_MONTH. Twelve months covers every month and season once, so the
dashboard can describe "a typical <month> <weekday> at <hour>" for any date
of the year from this one baseline year -- it does not go stale when the
calendar moves past it. The availability (GBFS) window is a separate,
shorter log; AVAILABILITY_END freezes it so later snapshots on the
data-snapshots branch never silently change published rates.
"""
from __future__ import annotations

from datetime import date, timedelta

# 2026-11-03: trips through Sep 2026 (Oct 2026 not yet published by
# Citi Bike); availability log through Oct 31, 2026.
WINDOW_END_MONTH = "2026-09"
AVAILABILITY_END = "2026-10-31"  # inclusive, UTC date
N_MONTHS = 12


def target_months(end_month: str = WINDOW_END_MONTH, n: int = N_MONTHS) -> list[str]:
    """The n calendar months ending at end_month, oldest first: '2026-09' -> ['2025-10', ..., '2026-09']."""
    year, month = (int(x) for x in end_month.split("-"))
    months = []
    for _ in range(n):
        months.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return months[::-1]


def window_dates(months: list[str]) -> tuple[str, str]:
    """First and last calendar day of a month list: (['2025-10', ..., '2026-09']) -> ('2025-10-01', '2026-09-30')."""
    start = f"{months[0]}-01"
    year, month = (int(x) for x in months[-1].split("-"))
    next_first = date(year + (month == 12), month % 12 + 1, 1)
    return start, (next_first - timedelta(days=1)).isoformat()


TARGET_MONTHS = target_months()
WEATHER_START, WEATHER_END = window_dates(TARGET_MONTHS)
