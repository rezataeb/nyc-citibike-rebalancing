"""Build the project report PDF from report/report.html with headless Chrome.

Method: report.html is a plain HTML page styled for print by report.css.
Every number in it is a {{placeholder}}; the facts functions below read
each one from the committed data files (data/*.json), so the report can't
quote a figure the data doesn't contain, and a rebuild after the data
changes updates every number at once. fill_template() refuses to build if
any placeholder has no value. The two charts are drawn with matplotlib
from the same files. Chrome's own print-to-PDF engine then renders the
filled page, so the PDF is exactly what Chrome's print preview shows -- no
LaTeX, same approach as the earlier docs V2 doc set.

Usage:
    python3 -m report.build_report
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

REPORT_DIR = Path(__file__).resolve().parent
DATA_DIR = REPORT_DIR.parent / "data"
HTML_PATH = REPORT_DIR / "report.html"
PDF_PATH = REPORT_DIR / "Citi_Bike_Rebalancing_Report.pdf"
FIGURES_DIR = REPORT_DIR / "figures"

# Checked in order; the first one that exists is used.
CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "chromium",
    "chromium-browser",
]

PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")

# Chart colors: the first three categorical slots of the dataviz reference
# palette (validated as a set on white: CVD and normal-vision separation
# pass; aqua is under 3:1 contrast, so every line is also direct-labeled
# and the same numbers appear in a table). Text stays in ink colors.
SERIES_COLORS = {"naive": "#2a78d6", "guarded": "#eb6834", "gam": "#1baf7a"}
ACCENT = "#0b5d63"  # report.css --accent
INK, INK_SOFT, RULE = "#1a1a18", "#454440", "#d8d7d3"


# ---------------------------------------------------------------- formatting

def fmt_int(n: float) -> str:
    """12345 -> '12,345'."""
    return f"{int(round(n)):,}"


def fmt_pct(rate: float, digits: int = 1) -> str:
    """0.1222 -> '12.2%'. Takes a 0-1 rate."""
    return f"{rate * 100:.{digits}f}%"


def fmt_month(yyyy_mm: str) -> str:
    """'2025-09' -> 'Sep 2025'."""
    return datetime.strptime(yyyy_mm, "%Y-%m").strftime("%b %Y")


def fmt_day(iso: str) -> str:
    """'2026-07-13T18:22:42+00:00' -> 'Jul 13, 2026' (UTC date)."""
    d = datetime.fromisoformat(iso)
    return f"{d.strftime('%b')} {d.day}, {d.year}"


# ---------------------------------------------------------------- facts
# Each function takes one already-loaded data file and returns
# {placeholder_name: display string}. Pure: no file access.

def coverage_facts(manifest: dict) -> dict[str, str]:
    """Trip-data window and station counts, from data_manifest.json."""
    months = manifest["months"]
    return {
        "trip_first_month": fmt_month(months[0]),
        "trip_last_month": fmt_month(months[-1]),
        "trip_n_months": str(len(months)),
        "n_stations": fmt_int(manifest["n_stations_any_coverage"]),
        "n_stations_full": fmt_int(manifest["n_stations_full_coverage"]),
    }


def typology_facts(flows: dict) -> dict[str, str]:
    """Station-typology clustering result, from flows.json's typology block."""
    t = flows["typology"]
    by_name = {c["name"].split(" (")[0]: c["n_stations"] for c in t["clusters"]}
    return {
        "typology_k": str(t["k"]),
        "typology_silhouette": f"{t['silhouette']:.2f}",
        "n_commuter_core": fmt_int(by_name["Commuter core"]),
        "n_residential_feeder": fmt_int(by_name["Residential feeder"]),
        "n_low_signal": fmt_int(t["n_excluded_low_signal"]),
    }


def equity_facts(flows: dict) -> dict[str, str]:
    """Equity layers (source sizes, thresholds) and per-station flag counts.

    Counts stations whose context has near_nycha / near_school /
    transit_gap set -- the same flags the dashboard's detail panel reads.
    """
    layers = flows["equity_join"]["layers"]
    stations = flows["stations"].values()

    def count(flag: str) -> str:
        return fmt_int(sum(1 for s in stations if (s.get("context") or {}).get(flag)))

    return {
        "nycha_records": fmt_int(layers["nycha"]["n_records"]),
        "school_records": fmt_int(layers["school"]["n_records"]),
        "subway_records": fmt_int(layers["subway"]["n_records"]),
        "nycha_threshold_m": str(layers["nycha"]["threshold_m"]),
        "school_threshold_m": str(layers["school"]["threshold_m"]),
        "subway_threshold_m": str(layers["subway"]["threshold_m"]),
        "n_near_nycha": count("near_nycha"),
        "n_near_school": count("near_school"),
        "n_transit_gap": count("transit_gap"),
    }


def fold_mae(perf: dict) -> dict[str, list]:
    """Per-fold MAE for the three compared tiers, in fold order.

    Returns {"months": [...], "naive": [...], "guarded": [...], "gam": [...]}.
    """
    out: dict[str, list] = {"months": [], "naive": [], "guarded": [], "gam": []}
    for fold in perf["folds"]:
        out["months"].append(fold["test_month"])
        for tier in ("naive", "guarded", "gam"):
            out[tier].append(fold["tiers"][tier]["overall"]["mae"])
    return out


def model_facts(perf: dict) -> dict[str, str]:
    """Walk-forward model comparison, from model_performance.json.

    Also counts the folds where the guarded model beat naive -- the
    aggregate alone hides that it does win some months.
    """
    agg, sig = perf["aggregate"], perf["significance"]
    folds = fold_mae(perf)
    guarded_wins = [
        m for m, n, g in zip(folds["months"], folds["naive"], folds["guarded"]) if g < n
    ]
    return {
        "n_folds": str(len(folds["months"])),
        "mae_naive": f"{agg['naive_mean_mae']:.2f}",
        "mae_guarded": f"{agg['guarded_mean_mae']:.2f}",
        "mae_gam": f"{agg['gam_mean_mae']:.2f}",
        "p_guarded_vs_naive": f"{sig['guarded_vs_naive']['p_value']:.3f}",
        "p_gam_vs_naive": f"{sig['gam_vs_naive']['p_value']:.3f}",
        "n_naive_wins": str(len(folds["months"]) - len(guarded_wins)),
        "guarded_win_months": ", ".join(fmt_month(m) for m in guarded_wins) or "none",
    }


def reliability_facts(rel: dict) -> dict[str, str]:
    """GBFS-log reliability window and rates, from reliability.json."""
    w, s, p = rel["window"], rel["system"], rel["per_station_summary"]
    return {
        "rel_start": fmt_day(w["start"]),
        "rel_end": fmt_day(w["end"]),
        "rel_days": str(w["days"]),
        "rel_snapshots": fmt_int(w["n_snapshots"]),
        "rel_median_gap_min": fmt_int(w["median_gap_minutes"]),
        "rel_max_gap_days": f"{w['max_gap_minutes'] / (60 * 24):.1f}",
        "rel_gap_start": fmt_day(w["max_gap_start"]),
        "rel_gap_end": fmt_day(w["max_gap_end"]),
        "rel_days_with_data": fmt_int(w["n_days_with_snapshots"]),
        "rel_unusable_rate": fmt_pct(s["unusable_rate"]),
        "rel_n_obs": fmt_int(s["n_observations"]),
        "rel_n_unusable": fmt_int(s["n_unusable"]),
        "rel_n_empty": fmt_int(s["n_empty"]),
        "rel_n_full": fmt_int(s["n_full"]),
        "rel_full_to_empty": f"{s['n_full'] / s['n_empty']:.1f}",
        "rel_n_offline": fmt_int(s["n_offline_excluded"]),
        "rel_n_stations": fmt_int(p["n_stations"]),
        "rel_n_rankable": fmt_int(p["n_rankable"]),
        "rel_min_obs": str(p["min_observations"]),
        "rel_median_station_rate": fmt_pct(p["median_rate"]),
        "rel_max_station_rate": fmt_pct(p["max_rate"]),
    }


def capacity_facts(cap: dict) -> dict[str, str]:
    """Capacity-vs-reliability regression, from reliability_capacity.json.

    The coefficient is reported as a magnitude plus a direction word
    ("lower"/"higher"), so the sentence stays true if a re-run flips its sign.
    """
    coef = cap["capacity_coefficient_per_dock"]
    facts = {
        "cap_n_stations": fmt_int(cap["n_joined"]),
        "cap_coef_points_per_dock": f"{abs(coef) * 100:.2f}",
        "cap_direction": "lower" if coef < 0 else "higher",
        "cap_se_points_per_dock": f"{cap['capacity_coefficient_se'] * 100:.2f}",
        "cap_gap_docks": str(cap["capacity_gap_docks"]),
        "cap_gap_points": f"{cap['reliability_gap_points']:.1f}",
    }
    for i, band in enumerate(cap["band_summary"], start=1):
        facts[f"cap_band{i}_label"] = band["label"]
        facts[f"cap_band{i}_n"] = fmt_int(band["n_stations"])
        facts[f"cap_band{i}_rate"] = fmt_pct(band["mean_unusable_rate"])
    return facts


def fleet_coverage(fleet: dict) -> dict[str, list]:
    """Deficit stations serviced by fleet size: {"trucks": [...], "serviced": [...]}."""
    sizes = sorted(fleet["scenarios"], key=int)
    return {
        "trucks": [int(k) for k in sizes],
        "serviced": [fleet["scenarios"][k]["n_deficit_serviced"] for k in sizes],
    }


def fleet_facts(fleet: dict) -> dict[str, str]:
    """Fleet-simulator headline numbers, from fleet_scenarios.json."""
    cov = fleet_coverage(fleet)
    largest = fleet["scenarios"][str(cov["trucks"][-1])]
    flagged = largest["n_deficit_flagged"]
    return {
        "fleet_capacity": str(fleet["capacity"]),
        "fleet_max_stops": str(fleet["max_stops"]),
        "fleet_n_deficit_flagged": fmt_int(flagged),
        "fleet_n_surplus_flagged": fmt_int(largest["n_surplus_flagged"]),
        "fleet_one_truck": fmt_int(cov["serviced"][0]),
        "fleet_max_trucks": str(cov["trucks"][-1]),
        "fleet_max_serviced": fmt_int(cov["serviced"][-1]),
        "fleet_max_share": fmt_pct(cov["serviced"][-1] / flagged),
    }


def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def collect_facts(data_dir: Path = DATA_DIR) -> dict[str, str]:
    """Load every data file the report quotes and merge all facts into one dict."""
    flows = load_json(data_dir / "flows.json")
    facts: dict[str, str] = {}
    facts.update(coverage_facts(load_json(data_dir / "data_manifest.json")))
    facts.update(typology_facts(flows))
    facts.update(equity_facts(flows))
    facts.update(model_facts(load_json(data_dir / "model_performance.json")))
    facts.update(reliability_facts(load_json(data_dir / "reliability.json")))
    facts.update(capacity_facts(load_json(data_dir / "reliability_capacity.json")))
    facts.update(fleet_facts(load_json(data_dir / "fleet_scenarios.json")))
    return facts


def fill_template(html: str, facts: dict[str, str]) -> str:
    """Replace every {{name}} in html with facts[name].

    Raises KeyError naming every placeholder that has no fact, so a typo or
    a missing data field stops the build instead of printing '{{...}}'.
    """
    missing = sorted({m for m in PLACEHOLDER.findall(html) if m not in facts})
    if missing:
        raise KeyError(f"No value for placeholder(s): {', '.join(missing)}")
    return PLACEHOLDER.sub(lambda m: facts[m.group(1)], html)


# ---------------------------------------------------------------- figures

def _use_report_font() -> None:
    """Match report.css's font stack (first one installed wins)."""
    import matplotlib

    matplotlib.rcParams["font.family"] = ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]


def _style_axes(ax) -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(RULE)
    ax.tick_params(colors=INK_SOFT, labelsize=8, length=0, pad=4)
    ax.grid(axis="y", color=RULE, linewidth=0.6)
    ax.set_axisbelow(True)


def plot_fold_mae(folds: dict[str, list], path: Path) -> Path:
    """Line chart: per-fold MAE of naive, guarded and GAM, direct-labeled."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _use_report_font()
    labels = {"naive": "Seasonal naive", "guarded": "Guarded model", "gam": "GAM"}
    markers = {"naive": "o", "guarded": "s", "gam": "^"}
    x = list(range(len(folds["months"])))
    fig, ax = plt.subplots(figsize=(6.6, 2.7))
    for tier in ("naive", "guarded", "gam"):
        ax.plot(x, folds[tier], color=SERIES_COLORS[tier], linewidth=2,
                marker=markers[tier], markersize=4.5, label=labels[tier])
        ax.annotate(labels[tier], (x[-1], folds[tier][-1]), xytext=(8, 0),
                    textcoords="offset points", va="center", fontsize=8, color=INK)
    ax.set_xticks(x)
    ax.set_xticklabels([datetime.strptime(m, "%Y-%m").strftime("%b\n%y") for m in folds["months"]])
    ax.set_ylabel("MAE (bikes/day)", fontsize=8, color=INK_SOFT)
    ax.set_xlim(-0.4, x[-1] + 2.2)
    _style_axes(ax)
    ax.legend(loc="upper center", ncol=3, frameon=False, fontsize=8,
              bbox_to_anchor=(0.45, 1.16), labelcolor=INK)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format="svg")
    plt.close(fig)
    return path


def plot_fleet_coverage(cov: dict[str, list], n_flagged: int, path: Path) -> Path:
    """Bar chart: deficit stations fully rebalanced by fleet size, one series."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _use_report_font()
    fig, ax = plt.subplots(figsize=(6.6, 2.5))
    ax.bar(cov["trucks"], cov["serviced"], width=0.62, color=ACCENT)
    for t, n in ((cov["trucks"][0], cov["serviced"][0]), (cov["trucks"][-1], cov["serviced"][-1])):
        ax.annotate(fmt_int(n), (t, n), xytext=(0, 3), textcoords="offset points",
                    ha="center", fontsize=8, color=INK)
    ax.set_xticks(cov["trucks"])
    ax.set_xlabel("Trucks in the fleet", fontsize=8, color=INK_SOFT)
    ax.set_ylabel(f"Deficit stations fully\nrebalanced (of {fmt_int(n_flagged)})", fontsize=8, color=INK_SOFT)
    _style_axes(ax)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format="svg")
    plt.close(fig)
    return path


def build_figures(data_dir: Path = DATA_DIR, out_dir: Path = FIGURES_DIR) -> list[Path]:
    """Draw both report charts from the data files into out_dir."""
    fleet = load_json(data_dir / "fleet_scenarios.json")
    largest = fleet["scenarios"][max(fleet["scenarios"], key=int)]
    return [
        plot_fold_mae(fold_mae(load_json(data_dir / "model_performance.json")),
                      out_dir / "fold_mae.svg"),
        plot_fleet_coverage(fleet_coverage(fleet), largest["n_deficit_flagged"],
                            out_dir / "fleet_coverage.svg"),
    ]


# ---------------------------------------------------------------- PDF

def find_chrome(candidates: list[str] = CHROME_CANDIDATES) -> str | None:
    """Return the first Chrome executable found, or None.

    An absolute path counts if the file exists; a bare name counts if it is
    on PATH.
    """
    for candidate in candidates:
        if os.path.isabs(candidate):
            if Path(candidate).exists():
                return candidate
        elif shutil.which(candidate):
            return shutil.which(candidate)
    return None


def chrome_pdf_command(chrome: str, html_path: Path, pdf_path: Path) -> list[str]:
    """Build the headless-Chrome command that prints html_path to pdf_path.

    --no-pdf-header-footer drops Chrome's default date/URL/page-number
    header and footer, so the page margins come only from report.css's
    @page rule.
    """
    return [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-pdf-header-footer",
        f"--print-to-pdf={pdf_path}",
        html_path.resolve().as_uri(),
    ]


def render_pdf(html_path: Path = HTML_PATH, pdf_path: Path = PDF_PATH) -> Path:
    """Render html_path to pdf_path and return pdf_path.

    Raises RuntimeError if Chrome is not installed or if it exits without
    writing the PDF.
    """
    chrome = find_chrome()
    if chrome is None:
        raise RuntimeError("Google Chrome / Chromium not found; it is needed to print the PDF.")
    if pdf_path.exists():
        pdf_path.unlink()
    subprocess.run(
        chrome_pdf_command(chrome, html_path, pdf_path),
        check=True,
        capture_output=True,
    )
    if not pdf_path.exists():
        raise RuntimeError(f"Chrome exited but did not write {pdf_path}.")
    return pdf_path


def build(data_dir: Path = DATA_DIR, pdf_path: Path = PDF_PATH) -> Path:
    """Full build: facts -> filled HTML -> figures -> PDF.

    The filled page is written next to report.html (so report.css and
    figures/ resolve by the same relative paths) and removed afterwards.
    """
    filled = fill_template(HTML_PATH.read_text(encoding="utf-8"), collect_facts(data_dir))
    build_figures(data_dir)
    filled_path = REPORT_DIR / "_filled.html"
    filled_path.write_text(filled, encoding="utf-8")
    try:
        return render_pdf(filled_path, pdf_path)
    finally:
        filled_path.unlink()


if __name__ == "__main__":
    out = build()
    print(f"Wrote {out} ({out.stat().st_size:,} bytes)")
