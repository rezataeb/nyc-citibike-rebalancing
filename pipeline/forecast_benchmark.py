"""Forecast benchmark: is the average-profile baseline still the best predictor?

The question (user, 2026-11-03): compare the naive baseline against real
time-series approaches, fairly, and say which to use.

Why a new benchmark rather than demand_model.py's backtest:
  - demand_model.py validates leave-one-month-out, so each held-out month
    learns from months AFTER it. Fine for "a typical day", not for a forecast.
  - It scores only station-hours that had at least one trip (the daily
    table stores no zero rows), which flatters methods that overpredict
    activity. Here every hour of every full-coverage station counts; hours
    with no trips are 0.

Method, stated plainly:
  - Stations: the ones with trips in every month of the window (empty
    months before a station opened would otherwise look like real zeros).
  - Forward-only weekly folds: the forecast for test week W is made at the
    start of W using only data before it, for all 7 days of W (1-7 days
    ahead). Test weeks start after 3 months of history.
  - Five methods, all predicting net flow (arrivals - departures) per
    station-hour:
      profile      mean of all earlier days of the same day type (weekday/
                   weekend), per station and hour -- the dashboard's method
      last_week    the same hour 7 days earlier
      four_weeks   mean of the same weekday-hour over the 4 earlier weeks
      holt_winters additive Holt-Winters with a weekly season, per
                   station-hour series (no trend; alpha/gamma picked on the
                   tuning months before the first test week)
      gbm          gradient boosting on recent history (lag 7, lag 14,
                   4-week mean), calendar, station level and the day's actual
                   weather (i.e. assuming a perfect weather forecast), retrained
                   monthly on the 3 months before
  - Scored at three levels: per station-hour; morning peak (sum of hours
    6-9, weekdays only) per station-day; daily total per station-day. MAE in
    bikes. Skill = 1 - MAE / MAE(profile). A paired Wilcoxon signed-rank test
    over test weeks says whether a method's weekly MAE differs from the
    profile's.
Output: data/forecast_benchmark.json.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import HistGradientBoostingRegressor

from pipeline.build_full_year import DAILY_NET_FLOW_PATH
from pipeline.window import TARGET_MONTHS, WEATHER_END, WEATHER_START

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUT_PATH = DATA_DIR / "forecast_benchmark.json"

HISTORY_MONTHS = 3          # months of history before the first test week
AM_HOURS = [6, 7, 8, 9]     # morning peak, 6:00-9:59
HW_ALPHAS = (0.02, 0.05, 0.1, 0.2)
HW_GAMMAS = (0.02, 0.05, 0.1, 0.2)
GBM_TRAIN_MONTHS = 3
GBM_MAX_ROWS = 1_500_000
METHODS = ["profile", "last_week", "four_weeks", "holt_winters", "gbm"]


# ---------------------------------------------------------------------------
# Data: a dense (station, day, hour) array with zeros for hours without trips
# ---------------------------------------------------------------------------

def dense_cube(daily: pd.DataFrame, months: list[str]) -> tuple[np.ndarray, list[str], pd.DatetimeIndex]:
    """(Y[station, day, hour] float32 net flow, station ids, days), full-coverage stations only."""
    months_seen = daily.assign(m=daily["date"].dt.strftime("%Y-%m")).groupby("station_id")["m"].nunique()
    stations = sorted(months_seen[months_seen == len(months)].index)
    first = pd.Timestamp(f"{months[0]}-01")
    last = pd.Timestamp(f"{months[-1]}-01") + pd.offsets.MonthEnd(0)
    days = pd.date_range(first, last, freq="D")
    s_index = {s: i for i, s in enumerate(stations)}
    rows = daily[daily["station_id"].isin(s_index)]
    cube = np.zeros((len(stations), len(days), 24), dtype=np.float32)
    d = ((rows["date"] - first).dt.days).to_numpy()
    cube[rows["station_id"].map(s_index).to_numpy(), d, rows["hour"].to_numpy()] = rows["net"].to_numpy()
    return cube, stations, days


def weekly_origins(days: pd.DatetimeIndex, months: list[str], history_months: int = HISTORY_MONTHS) -> list[int]:
    """Day indexes of each test week's Monday, once `history_months` of history exist, full weeks only."""
    start = pd.Timestamp(f"{months[history_months]}-01")
    mondays = [i for i, d in enumerate(days) if d.dayofweek == 0 and d >= start and i + 7 <= len(days)]
    return mondays


# ---------------------------------------------------------------------------
# Methods: each returns predictions for days [w0, w0+7) using only days < w0
# ---------------------------------------------------------------------------

def predict_profile(cube: np.ndarray, is_weekend: np.ndarray, w0: int) -> np.ndarray:
    """Mean over all earlier days of the same day type, per station and hour."""
    out = np.empty((cube.shape[0], 7, 24), dtype=np.float32)
    for weekend in (False, True):
        past = np.where(is_weekend[:w0] == weekend)[0]
        mean = cube[:, past, :].mean(axis=1)
        for k in range(7):
            if is_weekend[w0 + k] == weekend:
                out[:, k, :] = mean
    return out


def predict_lag(cube: np.ndarray, w0: int, lags: tuple[int, ...]) -> np.ndarray:
    """Mean of the same hour `lags` days earlier, for each day of the week. Every lag >= 7, so all inputs are before w0."""
    assert min(lags) >= 7
    return np.stack([np.mean([cube[:, w0 + k - lag, :] for lag in lags], axis=0) for k in range(7)], axis=1)


def holt_winters_states(series: np.ndarray, alpha: float, gamma: float, season: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """Additive Holt-Winters (no trend) over axis 1 of `series` [n_series, n_days].

    Returns (level[n, days], seasonal[n, days]): the state AFTER each day.
    Initialised from the first `season` days (level = their mean, seasonal =
    each day's deviation). One vectorized pass for all series at once.
    """
    n, d = series.shape
    level = np.empty((n, d), dtype=np.float64)
    seas = np.empty((n, d + season), dtype=np.float64)  # seas[:, t + season] = seasonal index of day t after its update
    init = series[:, :season].astype(np.float64)
    lvl = init.mean(axis=1)
    seas[:, :season] = init - lvl[:, None]
    for t in range(d):
        s_prev = seas[:, t]  # seasonal index from one season ago
        y = series[:, t]
        new_lvl = alpha * (y - s_prev) + (1 - alpha) * lvl
        seas[:, t + season] = gamma * (y - new_lvl) + (1 - gamma) * s_prev
        lvl = new_lvl
        level[:, t] = lvl
    return level, seas[:, season:]


def hw_forecast(level: np.ndarray, seasonal: np.ndarray, w0: int, season: int = 7) -> np.ndarray:
    """Forecast days w0..w0+6 from the state after day w0-1: level + the seasonal index of the same weekday."""
    return np.stack([level[:, w0 - 1] + seasonal[:, w0 - season + k] for k in range(7)], axis=1)


def tune_holt_winters(series: np.ndarray, tune_end: int) -> tuple[float, float]:
    """(alpha, gamma) with the lowest 1-7-day-ahead MAE on weekly forecasts before `tune_end`."""
    best, best_mae = None, np.inf
    origins = list(range(28, tune_end - 6, 7))
    for a in HW_ALPHAS:
        for g in HW_GAMMAS:
            level, seasonal = holt_winters_states(series[:, :tune_end], a, g)
            err = np.mean([np.abs(hw_forecast(level, seasonal, w) - series[:, w:w + 7]).mean() for w in origins])
            if err < best_mae:
                best, best_mae = (a, g), err
    return best


def gbm_features(cube: np.ndarray, days: pd.DatetimeIndex, day_idx: np.ndarray, weather: np.ndarray,
                 station_level: np.ndarray) -> np.ndarray:
    """Feature rows for every (station, day in day_idx, hour); history features only use days <= d - 7."""
    S, _, H = cube.shape
    lag7 = cube[:, day_idx - 7, :]
    lag14 = cube[:, day_idx - 14, :]
    four = np.mean([cube[:, day_idx - 7 * k, :] for k in range(1, 5)], axis=0)
    hour = np.broadcast_to(np.arange(H), (S, len(day_idx), H))
    dow = np.broadcast_to(days.dayofweek.to_numpy()[day_idx][None, :, None], (S, len(day_idx), H))
    temp = np.broadcast_to(weather[:, day_idx, 0][:, :, None], (S, len(day_idx), H))
    precip = np.broadcast_to(weather[:, day_idx, 1][:, :, None], (S, len(day_idx), H))
    level = np.broadcast_to(station_level[:, None, :], (S, len(day_idx), H))
    cols = [lag7, lag14, four, hour, dow, temp, precip, level]
    return np.stack([c.reshape(-1) for c in cols], axis=1).astype(np.float32)


def station_hour_level(cube: np.ndarray, end: int) -> np.ndarray:
    """Each station-hour's mean net over days < end (the station's own level and shape)."""
    return cube[:, :end, :].mean(axis=1)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def level_errors(pred: np.ndarray, actual: np.ndarray, weekday: np.ndarray) -> dict[str, float]:
    """MAE per station-hour, weekday morning peak (hours 6-9 summed) and daily total, for one test week."""
    am = np.abs(pred[:, weekday][:, :, AM_HOURS].sum(axis=2) - actual[:, weekday][:, :, AM_HOURS].sum(axis=2))
    return {
        "hourly": float(np.abs(pred - actual).mean()),
        "am_peak": float(am.mean()) if am.size else float("nan"),
        "daily": float(np.abs(pred.sum(axis=2) - actual.sum(axis=2)).mean()),
    }


def summarize(per_week: dict[str, list[dict]]) -> dict:
    """Mean MAE, skill vs profile, weeks won and a paired Wilcoxon p-value, per method and level."""
    out = {}
    for level in ("hourly", "am_peak", "daily"):
        base = np.array([w[level] for w in per_week["profile"]])
        out[level] = {}
        for m, weeks in per_week.items():
            vals = np.array([w[level] for w in weeks])
            row = {"mae": round(float(vals.mean()), 4), "skill_vs_profile": round(float(1 - vals.mean() / base.mean()), 4),
                   "weeks_better_than_profile": int((vals < base).sum()), "n_weeks": int(len(vals))}
            if m != "profile":
                row["wilcoxon_p"] = round(float(stats.wilcoxon(vals, base).pvalue), 6)
            out[level][m] = row
    return out


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def load_weather(stations: list[str], daily: pd.DataFrame, days: pd.DatetimeIndex) -> np.ndarray:
    """weather[station, day, (temp_c, precip_mm)] from the same per-zone Open-Meteo pull demand_model uses."""
    from pipeline.demand_model import station_coords_from_daily
    from pipeline.weather import compute_weather_zones, fetch_weather_at_points

    coords = station_coords_from_daily(daily[daily["station_id"].isin(stations)])
    zone_by_station, centroids = compute_weather_zones(coords)
    by_zone = fetch_weather_at_points(centroids, WEATHER_START, WEATHER_END)
    out = np.zeros((len(stations), len(days), 2), dtype=np.float32)
    for z, frame in enumerate(by_zone):
        frame = frame.set_index(pd.to_datetime(frame["date"])).reindex(days)
        values = frame[["temp_mean_c", "precip_mm"]].interpolate(limit_direction="both").to_numpy()
        for i, s in enumerate(stations):
            if zone_by_station.get(s) == z:
                out[i] = values
    return out


def run(months: list[str] = TARGET_MONTHS, seed: int = 0) -> dict:
    daily = pd.read_parquet(DAILY_NET_FLOW_PATH)
    daily["date"] = pd.to_datetime(daily["date"])
    cube, stations, days = dense_cube(daily, months)
    is_weekend = np.asarray(days.dayofweek >= 5)
    weeks = weekly_origins(days, months)
    print(f"{len(stations):,} full-coverage stations, {len(days)} days, {len(weeks)} test weeks from {days[weeks[0]].date()}")

    # Holt-Winters: tuned once on the history before the first test week, on the station-hour series.
    S = cube.shape[0]
    series = cube.transpose(0, 2, 1).reshape(S * 24, -1)  # [station*hour, day]
    alpha, gamma = tune_holt_winters(series, weeks[0])
    print(f"holt-winters alpha={alpha} gamma={gamma}")
    level, seasonal = holt_winters_states(series, alpha, gamma)

    weather = load_weather(stations, daily, days)
    rng = np.random.default_rng(seed)
    per_week = {m: [] for m in METHODS}
    gbm_model, gbm_month = None, None
    for w0 in weeks:
        month = days[w0].strftime("%Y-%m")
        if month != gbm_month:  # retrain monthly on the 3 months before this month's first test week
            start = max(28, w0 - 91)
            train_days = np.arange(start, w0)
            lvl = station_hour_level(cube, start)
            X = gbm_features(cube, days, train_days, weather, lvl)
            y = cube[:, train_days, :].reshape(-1)
            keep = rng.choice(len(y), size=min(GBM_MAX_ROWS, len(y)), replace=False)
            gbm_model = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.1, max_depth=6, random_state=seed)
            gbm_model.fit(X[keep], y[keep])
            gbm_month, gbm_level = month, station_hour_level(cube, w0)
        test_days = np.arange(w0, w0 + 7)
        actual = cube[:, test_days, :]
        weekday = ~is_weekend[test_days]
        preds = {
            "profile": predict_profile(cube, is_weekend, w0),
            "last_week": predict_lag(cube, w0, (7,)),
            "four_weeks": predict_lag(cube, w0, (7, 14, 21, 28)),
            "holt_winters": hw_forecast(level, seasonal, w0).reshape(S, 24, 7).transpose(0, 2, 1),
            "gbm": gbm_model.predict(gbm_features(cube, days, test_days, weather, gbm_level)).reshape(S, 7, 24),
        }
        for m in METHODS:
            per_week[m].append(level_errors(preds[m], actual, weekday))
        print(f"  week of {days[w0].date()}: " + ", ".join(f"{m} {per_week[m][-1]['hourly']:.3f}" for m in METHODS))

    result = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "months": months,
        "n_stations": len(stations),
        "first_test_week": str(days[weeks[0]].date()),
        "n_test_weeks": len(weeks),
        "holt_winters": {"alpha": alpha, "gamma": gamma},
        "levels": {"hourly": "per station-hour", "am_peak": "weekday hours 6-9 summed, per station-day",
                   "daily": "daily total, per station-day"},
        "results": summarize(per_week),
        "per_week": {m: [{k: round(v, 4) for k, v in w.items()} for w in weeks_] for m, weeks_ in per_week.items()},
        "method": __doc__.split("Method, stated plainly:")[1].split("Output:")[0].strip(),
    }
    OUT_PATH.write_text(json.dumps(result, indent=2))
    print(f"wrote {OUT_PATH}")
    return result


if __name__ == "__main__":
    out = run()
    for level, rows in out["results"].items():
        print(f"\n{level}")
        for m, r in rows.items():
            print(f"  {m:13s} MAE {r['mae']:.3f}  skill {r['skill_vs_profile']:+.1%}  better in {r['weeks_better_than_profile']}/{r['n_weeks']} weeks"
                  + (f"  p={r['wilcoxon_p']:.4f}" if 'wilcoxon_p' in r else ""))
