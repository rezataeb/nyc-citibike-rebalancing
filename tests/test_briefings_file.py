"""The shipped data/briefings.json must match the current data (skipped until the full run writes it)."""

import json

import pytest

from pipeline.briefing_writer import BRIEFINGS_PATH
from pipeline.briefings import FLOWS_PATH, MANIFEST_PATH, RELIABILITY_PATH, build_all_facts, facts_hash
from pipeline.guardrails import check_briefing

pytestmark = pytest.mark.skipif(not BRIEFINGS_PATH.exists(), reason="data/briefings.json not built yet")


@pytest.fixture(scope="module")
def shipped():
    return json.loads(BRIEFINGS_PATH.read_text())


@pytest.fixture(scope="module")
def all_facts():
    return build_all_facts(
        json.loads(FLOWS_PATH.read_text()), json.loads(RELIABILITY_PATH.read_text()), json.loads(MANIFEST_PATH.read_text())
    )


def test_every_station_has_exactly_one_briefing(shipped, all_facts):
    assert set(shipped["stations"]) == set(all_facts)


def test_no_briefing_is_stale(shipped, all_facts):
    """If the data was rebuilt, rerun `--full`: it rewrites only the changed stations."""
    stale = [i for i, b in shipped["stations"].items() if b["facts_hash"] != facts_hash(all_facts[i])]
    assert stale == []


def test_every_shipped_briefing_passes_the_checker(shipped, all_facts):
    failures = {i: check_briefing(b["text"], all_facts[i]) for i, b in shipped["stations"].items()}
    assert {i: p for i, p in failures.items() if p} == {}
