"""Tests for pipeline/gbm.py."""

import pandas as pd

from pipeline.gbm import _month_calendar, add_features, predict_gbm, train_gbm


def test_month_calendar_feb_2026_flags_presidents_day():
    calendar = _month_calendar("2026-02")
    assert len(calendar) == 28

    presidents_day = calendar[calendar["date"] == "2026-02-16"]
    assert presidents_day["day_type"].iloc[0] == "weekday"
    assert bool(presidents_day["is_holiday"].iloc[0]) is True
    assert calendar["is_holiday"].sum() == 1


def test_month_calendar_june_2026_flags_juneteenth():
    calendar = _month_calendar("2026-06")
    assert len(calendar) == 30

    juneteenth = calendar[calendar["date"] == "2026-06-19"]
    assert juneteenth["day_type"].iloc[0] == "weekday"
    assert bool(juneteenth["is_holiday"].iloc[0]) is True


def test_month_calendar_april_2026_has_no_holiday():
    calendar = _month_calendar("2026-04")
    assert calendar["is_holiday"].sum() == 0


def test_add_features_merges_weather_and_calendar_onto_flows():
    flows = pd.DataFrame(
        [
            {
                "station_id": "1", "station_name": "A", "month": "2026-02", "day_type": "weekday",
                "hour": 8, "net": -5, "lat": 40.7, "lng": -74.0, "n_days": 20, "net_per_day": -0.25,
            }
        ]
    )
    weather = pd.DataFrame(
        {
            "date": pd.date_range("2026-02-01", "2026-02-28"),
            "temp_mean_c": [0.0] * 28,
            "precip_mm": [1.0] * 28,
        }
    )

    result = add_features(flows, weather)

    assert result["temp_mean_c"].iloc[0] == 0.0
    assert result["precip_mm"].iloc[0] == 1.0
    assert result["holiday_fraction"].iloc[0] > 0  # Presidents Day is a Feb weekday
    assert -1.0 <= result["doy_sin"].iloc[0] <= 1.0
    assert -1.0 <= result["doy_cos"].iloc[0] <= 1.0


def test_train_and_predict_gbm_round_trip():
    train_flows = pd.DataFrame(
        [
            {"station_id": "1", "lat": 40.75, "lng": -73.98, "day_type": "weekday", "hour": 8,
             "net_per_day": 5.0, "n_days": 20, "temp_mean_c": 5.0, "precip_mm": 0.0,
             "holiday_fraction": 0.0, "doy_sin": 0.1, "doy_cos": 0.9},
            {"station_id": "1", "lat": 40.75, "lng": -73.98, "day_type": "weekday", "hour": 18,
             "net_per_day": -5.0, "n_days": 20, "temp_mean_c": 5.0, "precip_mm": 0.0,
             "holiday_fraction": 0.0, "doy_sin": 0.1, "doy_cos": 0.9},
            {"station_id": "2", "lat": 40.68, "lng": -73.99, "day_type": "weekday", "hour": 8,
             "net_per_day": 1.0, "n_days": 20, "temp_mean_c": 5.0, "precip_mm": 0.0,
             "holiday_fraction": 0.0, "doy_sin": 0.1, "doy_cos": 0.9},
        ]
    )
    categories = {"day_type": ["weekday", "weekend"]}

    model = train_gbm(train_flows, categories)
    predicted = predict_gbm(model, train_flows, categories)

    assert set(predicted.columns) == {"station_id", "day_type", "hour", "n_days", "predicted_net_per_day"}
    assert len(predicted) == 3
    assert predicted["predicted_net_per_day"].notna().all()
