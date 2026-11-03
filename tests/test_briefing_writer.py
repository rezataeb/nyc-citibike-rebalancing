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


# --- the write -> check -> retry -> fallback loop, with a fake model ($0) ----------

class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Usage:
    def __init__(self):
        self.input_tokens, self.output_tokens = 300, 80
        self.cache_creation_input_tokens, self.cache_read_input_tokens = 0, 1800

    def model_dump(self):
        return dict(vars(self))


class _Message:
    def __init__(self, text, stop_reason="end_turn"):
        self.content, self.stop_reason, self.usage = [_Block(text)], stop_reason, _Usage()


class _FakeClient:
    """Returns the queued texts in order and records every request."""

    def __init__(self, *texts):
        self.queue, self.requests = list(texts), []
        self.messages = self

    def create(self, **params):
        self.requests.append(params)
        item = self.queue.pop(0)
        return item if isinstance(item, _Message) else _Message(item)


@pytest.fixture(scope="module")
def some_facts(all_facts):
    return next(f for f in all_facts.values() if f["reliability"]["status"] == "ok" and not f["weekday_flow"]["low_signal"])


def test_good_first_answer_is_used(some_facts):
    from pipeline.briefing_writer import write_one

    good = template_fallback(some_facts)  # known to pass the checker
    result = write_one(_FakeClient(good), some_facts)
    assert result["source"] == "model" and result["text"] == good


def test_bad_first_answer_gets_one_retry_with_its_problems(some_facts):
    from pipeline.briefing_writer import write_one

    client = _FakeClient("It is unusable 99.9% of the time.", template_fallback(some_facts))
    result = write_one(client, some_facts)
    assert result["source"] == "model_retry"
    assert "99.9" in client.requests[1]["messages"][-1]["content"]


def test_two_bad_answers_fall_back_to_the_template(some_facts):
    from pipeline.briefing_writer import write_one

    result = write_one(_FakeClient("Invented 98,765 docks.", "Still 98,765 docks."), some_facts)
    assert result["source"] == "template" and result["text"] == template_fallback(some_facts)


def test_refusal_or_truncation_counts_as_a_failure(some_facts):
    from pipeline.briefing_writer import write_one

    client = _FakeClient(_Message("cut off mid", stop_reason="max_tokens"), template_fallback(some_facts))
    assert write_one(client, some_facts)["source"] == "model_retry"


def test_usage_cost_prices_cache_reads_at_ten_percent():
    from pipeline.briefing_writer import usage_cost

    # 300*0.10 + 1800*0.10*0.10 + 80*0.50 = 30 + 18 + 40 = 88 per million
    assert usage_cost(_Usage()) == pytest.approx(88 / 1e6)
