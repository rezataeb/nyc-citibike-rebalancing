"""Tests for pipeline/briefing_writer.py (no API calls)."""

import json

import pytest

from pipeline.briefing_writer import (
    MODEL,
    SYSTEM_PROMPT,
    build_params,
    estimate_cost,
    ordinal,
    pilot_sample,
    prompt_facts,
    retry_messages,
    template_fallback,
)
from pipeline.briefings import FLOWS_PATH, MANIFEST_PATH, RELIABILITY_PATH, build_all_facts
from pipeline.guardrails import check_briefing


@pytest.fixture(scope="module")
def all_facts():
    return build_all_facts(
        json.loads(FLOWS_PATH.read_text()), json.loads(RELIABILITY_PATH.read_text()), json.loads(MANIFEST_PATH.read_text())
    )


def test_template_fallback_passes_the_checker_for_every_real_station(all_facts):
    """The fallback must be safe by construction: zero violations on all stations."""
    failures = {i: check_briefing(template_fallback(f), f) for i, f in all_facts.items()}
    assert {i: p for i, p in failures.items() if p} == {}


@pytest.mark.parametrize("n,label", [(1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"), (12, "12th"),
                                     (13, "13th"), (21, "21st"), (112, "112th"), (1174, "1,174th"), (2507, "2,507th")])
def test_ordinal(n, label):
    assert ordinal(n) == label


def test_params_use_haiku_no_thinking_and_cache_the_system_prompt(all_facts):
    params = build_params(next(iter(all_facts.values())))
    assert params["model"] == MODEL == "claude-haiku-5-5"
    assert params["thinking"] == {"type": "disabled"}
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert params["system"][0]["text"] == SYSTEM_PROMPT


def test_model_does_not_see_windows_or_station_id(all_facts):
    facts = next(iter(all_facts.values()))
    assert set(prompt_facts(facts)) == set(facts) - {"station_id", "trip_window", "reliability_window"}


def test_retry_shows_the_model_its_first_text_and_the_problems(all_facts):
    facts = next(iter(all_facts.values()))
    messages = retry_messages(facts, "first try", ["number not in facts: 9"])
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert "number not in facts: 9" in messages[-1]["content"]


def test_pilot_sample_is_varied_deterministic_and_unique(all_facts):
    sample = pilot_sample(all_facts, 20)
    assert sample == pilot_sample(all_facts, 20)
    assert len(sample) == len(set(sample)) == 20
    picked = [all_facts[i] for i in sample]
    assert any(f["typology_matches_peak"] is False for f in picked)
    assert any(f["weekday_flow"]["low_signal"] for f in picked)
    assert any(f["reliability"]["status"] == "no_data" for f in picked)


def test_cost_estimate_arithmetic():
    est = estimate_cost([2000, 2000], n_stations=1000, system_tokens=1500, output_tokens=100)
    # standard: 1000 * (2000*0.10 + 100*0.50) / 1e6 = 0.25
    assert est["standard"] == 0.25
    assert est["batch"] == 0.125
    assert est["batch_cached"] < est["batch"]
