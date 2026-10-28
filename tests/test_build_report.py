"""Tests for report/build_report.py and the structure of report/report.html.

The structure tests guard the report's required sections (Session 72's
roadmap step 3): Limitations including the Phase 5 closeout, Future steps,
and the AI/LLM section. The facts tests run each reader on a small
hand-written dict, so they don't depend on the real data changing. Two
tests read the real data files: one fills the real template (no
placeholder may be left), and one pins the hand-written reliability-window
paragraph to the reliability.json it describes. The render test is skipped
when Chrome is not installed.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser

import pytest

from report.build_report import (
    DATA_DIR,
    HTML_PATH,
    PLACEHOLDER,
    build_figures,
    capacity_facts,
    chrome_pdf_command,
    collect_facts,
    coverage_facts,
    equity_facts,
    fill_template,
    find_chrome,
    fleet_coverage,
    fleet_facts,
    fmt_day,
    fmt_month,
    fmt_pct,
    model_facts,
    reliability_facts,
    render_pdf,
    typology_facts,
)

REQUIRED_SECTION_IDS = [
    "summary",
    "problem",
    "data-methods",
    "findings",
    "limitations",
    "gbfs-sampling",
    "phase5-closeout",
    "live-mode-caveats",
    "future-steps",
    "ai-llm",
]


class _IdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.section_ids: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "section":
            for name, value in attrs:
                if name == "id":
                    self.section_ids.append(value)


def _section_ids(html: str) -> list[str]:
    parser = _IdCollector()
    parser.feed(html)
    return parser.section_ids


# ---------------------------------------------------------------- structure

def test_report_has_every_required_section():
    ids = _section_ids(HTML_PATH.read_text(encoding="utf-8"))
    missing = [s for s in REQUIRED_SECTION_IDS if s not in ids]
    assert missing == []


def test_phase5_closeout_sits_inside_limitations():
    # The roadmap requires the closeout as a limitation, not a footnote
    # elsewhere: it must come after "limitations" and before the next h2 section.
    ids = _section_ids(HTML_PATH.read_text(encoding="utf-8"))
    assert ids.index("limitations") < ids.index("phase5-closeout") < ids.index("future-steps")


# ---------------------------------------------------------------- formatting

def test_formatters():
    assert fmt_pct(0.12218) == "12.2%"
    assert fmt_month("2025-09") == "Sep 2025"
    assert fmt_day("2026-07-13T18:22:42+00:00") == "Jul 13, 2026"


# ---------------------------------------------------------------- facts

def test_coverage_facts():
    facts = coverage_facts({
        "months": ["2025-09", "2025-10", "2026-08"],
        "n_stations_any_coverage": 2592,
        "n_stations_full_coverage": 1672,
    })
    assert facts["trip_first_month"] == "Sep 2025"
    assert facts["trip_last_month"] == "Aug 2026"
    assert facts["trip_n_months"] == "3"
    assert facts["n_stations"] == "2,592"


def _flows_fixture():
    return {
        "typology": {
            "k": 2,
            "silhouette": 0.3813,
            "n_excluded_low_signal": 1,
            "clusters": [
                {"cluster": 0, "name": "Residential feeder (drains AM, fills PM)", "n_stations": 1169},
                {"cluster": 1, "name": "Commuter core (fills AM, drains PM)", "n_stations": 707},
            ],
        },
        "equity_join": {"layers": {
            "nycha": {"n_records": 216, "threshold_m": 300},
            "school": {"n_records": 1899, "threshold_m": 300},
            "subway": {"n_records": 2120, "threshold_m": 800},
        }},
        "stations": {
            "a": {"context": {"near_nycha": 1, "near_school": 1, "transit_gap": 0}},
            "b": {"context": {"near_nycha": 0, "near_school": 1, "transit_gap": 1}},
            "c": {},  # no equity context at all: counts as not flagged
        },
    }


def test_typology_facts_matches_clusters_by_name_not_position():
    facts = typology_facts(_flows_fixture())
    assert facts["n_commuter_core"] == "707"
    assert facts["n_residential_feeder"] == "1,169"
    assert facts["typology_silhouette"] == "0.38"


def test_equity_facts_counts_flagged_stations():
    facts = equity_facts(_flows_fixture())
    assert (facts["n_near_nycha"], facts["n_near_school"], facts["n_transit_gap"]) == ("1", "2", "1")
    assert facts["subway_threshold_m"] == "800"


def _perf_fixture():
    def fold(month, naive, guarded, gam):
        return {"test_month": month, "tiers": {
            "naive": {"overall": {"mae": naive}},
            "guarded": {"overall": {"mae": guarded}},
            "gam": {"overall": {"mae": gam}},
        }}
    return {
        "folds": [fold("2025-12", 1.8, 1.7, 1.9), fold("2026-04", 1.9, 2.1, 2.2)],
        "aggregate": {"naive_mean_mae": 1.85, "guarded_mean_mae": 1.9, "gam_mean_mae": 2.05},
        "significance": {
            "guarded_vs_naive": {"p_value": 0.0122},
            "gam_vs_naive": {"p_value": 0.00244},
        },
    }


def test_model_facts_names_the_months_guarded_won():
    facts = model_facts(_perf_fixture())
    assert facts["guarded_win_months"] == "Dec 2025"
    assert facts["n_naive_wins"] == "1"
    assert facts["mae_naive"] == "1.85"
    assert facts["p_gam_vs_naive"] == "0.002"


def test_reliability_facts():
    facts = reliability_facts({
        "window": {"start": "2026-07-13T18:22:42+00:00", "end": "2026-09-13T17:52:39+00:00",
                   "days": 61, "n_snapshots": 191, "median_gap_minutes": 86.7,
                   "max_gap_minutes": 70725.2, "max_gap_start": "2026-07-26T15:07:26+00:00",
                   "max_gap_end": "2026-09-13T17:52:39+00:00", "n_days_with_snapshots": 15},
        "system": {"unusable_rate": 0.1222, "n_observations": 451111, "n_unusable": 55120,
                   "n_empty": 13507, "n_full": 42082, "n_offline_excluded": 18962},
        "per_station_summary": {"n_stations": 2480, "n_rankable": 2385, "min_observations": 20,
                                "median_rate": 0.0942, "max_rate": 0.7696},
    })
    assert facts["rel_unusable_rate"] == "12.2%"
    assert facts["rel_max_gap_days"] == "49.1"
    assert facts["rel_gap_start"] == "Jul 26, 2026"
    assert facts["rel_days_with_data"] == "15"
    assert facts["rel_full_to_empty"] == "3.1"
    assert facts["rel_median_gap_min"] == "87"


def _cap_fixture(coef):
    return {
        "n_joined": 2317, "capacity_coefficient_per_dock": coef,
        "capacity_coefficient_se": 0.00021, "capacity_gap_docks": 20,
        "reliability_gap_points": 2.105,
        "band_summary": [{"label": "< 20 docks", "n_stations": 519, "mean_unusable_rate": 0.1314}],
    }


def test_capacity_facts_reports_magnitude_and_direction():
    facts = capacity_facts(_cap_fixture(-0.0010525))
    assert facts["cap_coef_points_per_dock"] == "0.11"
    assert facts["cap_direction"] == "lower"
    assert facts["cap_band1_rate"] == "13.1%"
    # If a re-run flips the sign, the sentence must flip with it.
    assert capacity_facts(_cap_fixture(0.0010525))["cap_direction"] == "higher"


def _fleet_fixture():
    def scen(serviced):
        return {"n_deficit_flagged": 100, "n_surplus_flagged": 50, "n_deficit_serviced": serviced}
    # Keys are strings and deliberately out of order: "10" sorts before "2" as text.
    return {"capacity": 20, "max_stops": 45,
            "scenarios": {"1": scen(5), "10": scen(40), "2": scen(9)}}


def test_fleet_coverage_sorts_fleet_sizes_numerically():
    cov = fleet_coverage(_fleet_fixture())
    assert cov == {"trucks": [1, 2, 10], "serviced": [5, 9, 40]}


def test_fleet_facts():
    facts = fleet_facts(_fleet_fixture())
    assert facts["fleet_max_trucks"] == "10"
    assert facts["fleet_max_share"] == "40.0%"
    assert facts["fleet_one_truck"] == "5"


# ---------------------------------------------------------------- template

def test_fill_template_replaces_known_names():
    assert fill_template("a {{x}} b {{ y }}", {"x": "1", "y": "2"}) == "a 1 b 2"


def test_fill_template_refuses_unknown_names():
    with pytest.raises(KeyError, match="missing_one"):
        fill_template("{{known}} {{missing_one}}", {"known": "1"})


def test_real_report_fills_with_no_placeholder_left():
    filled = fill_template(HTML_PATH.read_text(encoding="utf-8"), collect_facts())
    assert PLACEHOLDER.search(filled) is None
    assert "{{" not in filled


def test_longest_gap_explanation_matches_reliability_json():
    # Section 5.1 explains the longest gap in words (the log hitting GitHub's
    # 100 MB limit, Aug 30 - Sep 13, 2026). The dates are filled from the
    # data, but the reason can't be, so this pins it: if a rebuild finds a
    # different longest gap, this fails and 5.1's explanation must be rewritten.
    window = json.loads((DATA_DIR / "reliability.json").read_text())["window"]
    assert window["max_gap_start"][:10] == "2026-08-30"
    assert window["max_gap_end"][:10] == "2026-09-13"


# ---------------------------------------------------------------- build

def test_build_figures_writes_two_svgs(tmp_path):
    paths = build_figures(DATA_DIR, tmp_path)
    assert [p.name for p in paths] == ["fold_mae.svg", "fleet_coverage.svg"]
    assert all(p.read_text().lstrip().startswith("<?xml") for p in paths)


def test_find_chrome_returns_none_when_nothing_exists(tmp_path):
    assert find_chrome([str(tmp_path / "no-such-chrome"), "no-such-binary-xyz"]) is None


def test_find_chrome_accepts_existing_absolute_path(tmp_path):
    fake = tmp_path / "chrome"
    fake.write_text("")
    assert find_chrome([str(fake)]) == str(fake)


def test_chrome_pdf_command_prints_to_target_without_header_footer(tmp_path):
    html = tmp_path / "r.html"
    pdf = tmp_path / "r.pdf"
    cmd = chrome_pdf_command("chrome", html, pdf)
    assert cmd[0] == "chrome"
    assert f"--print-to-pdf={pdf}" in cmd
    assert "--no-pdf-header-footer" in cmd
    assert cmd[-1].startswith("file://")


@pytest.mark.skipif(find_chrome() is None, reason="Chrome not installed")
def test_render_pdf_writes_a_real_pdf(tmp_path):
    out = render_pdf(HTML_PATH, tmp_path / "report.pdf")
    assert out.read_bytes()[:5] == b"%PDF-"
