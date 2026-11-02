"""Guardrails for AI-written text: every number must come from the facts (Feature 1, step 2).

The model may phrase things, never produce numbers. check_briefing() returns
a list of plain-language violations for one briefing; an empty list means it
passes. Reused later by the change memo and the eval harness.

Method, stated plainly:
  - extract_numbers() pulls every number-like token out of a text and puts
    it in one normal form: plain numbers ("2,507" -> 2507.0, "13.0%" -> 13.0),
    clock times ("8 AM", "8:00 a.m.", "noon" -> "8 AM" / "12 PM"), ordinals
    ("12th" -> 12.0) and spelled-out numbers two..twenty, second..tenth.
    "one" and "first" are NOT read as numbers: they are too often plain words
    ("this one", "first-come"). The prompt asks for digits instead.
  - allowed_values() runs the same extractor over every value in the facts
    record, strings included (station names, window labels, hour labels), so
    both sides are normalized identically.
  - Kinds must match: a percentage only matches a *_pct fact, a distance in
    meters only a *_m fact, a clock time only an hour label.
  - A text number passes if it equals an allowed number, or equals an allowed
    number rounded to the text's own precision ("1" for 1.2, "13" for 13.0).
    Rounding the other way, or any number not derived from the facts, fails.
  - Day-type scope: a sentence that names a time of day must also say
    weekday(s) or weekend(s).
  - Typology hedge: when facts say typology_matches_peak is False, a sentence
    naming the type must say "grouped with".
  - Causal words ("because", "caused", "due to", ...) fail: the facts hold no
    causes. Length: at most MAX_SENTENCES sentences and MAX_WORDS words.
"""

from __future__ import annotations

import re

MAX_SENTENCES = 3
MAX_WORDS = 90

SPELLED = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
    "eighth": 8, "ninth": 9, "tenth": 10,
}
CAUSAL_WORDS = ("because", "caused", "causes", "due to", "as a result", "leads to", "results in")
TIME_OF_DAY_WORDS = ("morning", "afternoon", "evening", "night", "midday", "rush hour")
DAY_TYPE_WORDS = ("weekday", "weekend")
TYPOLOGY_SHORT_NAMES = ("commuter core", "residential feeder")

_TIME = re.compile(r"\b(\d{1,2})(?::00)?\s*([ap])\.?\s*m\b\.?", re.IGNORECASE)
_NOON_MIDNIGHT = re.compile(r"\b(noon|midnight)\b", re.IGNORECASE)
_ORDINAL = re.compile(r"#?\b(\d+)(?:st|nd|rd|th)\b", re.IGNORECASE)
_PERCENT = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:%|percent\b)", re.IGNORECASE)
_METERS = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)\s*(?:m|meters|metres)\b", re.IGNORECASE)
_NUMBER = re.compile(r"(?<![\w.])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])-?\d+(?:\.\d+)?")
_WORD = re.compile(r"[a-z]+", re.IGNORECASE)


class Percent(float):
    """A number written as a percentage: may only match a *_pct fact."""


class Meters(float):
    """A number written as a distance in meters: may only match a *_m fact."""


def _time_label(hour: int, meridiem: str) -> str:
    return f"{hour} {meridiem.upper()}M"


def extract_numbers(text: str) -> list[str | float]:
    """Every number-like token in `text`, normalized: clock times as '8 AM', the rest as floats.

    Percentages come back as Percent and distances as Meters, so a number
    can't pass by matching a fact of a different kind ("14.0%" must not pass
    because a subway is called "14 St"). Times, percentages, distances and
    ordinals are removed before plain numbers are read, so "8 AM" is one
    token, not a time plus a stray 8.
    """
    tokens: list[str | float] = []
    remaining = text
    for match in _TIME.finditer(remaining):
        tokens.append(_time_label(int(match.group(1)), match.group(2)))
    remaining = _TIME.sub(" ", remaining)
    for match in _NOON_MIDNIGHT.finditer(remaining):
        tokens.append("12 PM" if match.group(1).lower() == "noon" else "12 AM")
    remaining = _NOON_MIDNIGHT.sub(" ", remaining)
    for match in _PERCENT.finditer(remaining):
        tokens.append(Percent(match.group(1)))
    remaining = _PERCENT.sub(" ", remaining)
    for match in _METERS.finditer(remaining):
        tokens.append(Meters(match.group(1).replace(",", "")))
    remaining = _METERS.sub(" ", remaining)
    for match in _ORDINAL.finditer(remaining):
        tokens.append(float(match.group(1)))
    remaining = _ORDINAL.sub(" ", remaining)
    for match in _NUMBER.finditer(remaining):
        tokens.append(float(match.group(0).replace(",", "")))
    for word in _WORD.findall(remaining):
        if word.lower() in SPELLED:
            tokens.append(float(SPELLED[word.lower()]))
    return tokens


def _walk(value, key: str = "") -> list[tuple[str, object]]:
    """Every (key, leaf value) pair of a nested facts record."""
    if isinstance(value, dict):
        return [pair for k, item in value.items() for pair in _walk(item, k)]
    if isinstance(value, list):
        return [pair for item in value for pair in _walk(item, key)]
    return [(key, value)]


def allowed_values(facts: dict) -> dict[str, set]:
    """What a briefing for this station may use, by kind.

    'number': every number in the facts (plain use); 'percent': only *_pct
    fields; 'meters': only *_m fields; 'time': clock-time labels.
    """
    allowed: dict[str, set] = {"number": set(), "percent": set(), "meters": set(), "time": set()}
    for key, leaf in _walk(facts):
        if isinstance(leaf, bool) or leaf is None:
            continue
        if isinstance(leaf, (int, float)):
            allowed["number"].add(float(leaf))
            if key.endswith("_pct"):
                allowed["percent"].add(float(leaf))
            if key.endswith("_m"):
                allowed["meters"].add(float(leaf))
        elif isinstance(leaf, str):
            for token in extract_numbers(leaf):
                allowed["time" if isinstance(token, str) else "number"].add(token)
    return allowed


def number_is_allowed(value: float, allowed: set[float]) -> bool:
    """Equal to an allowed number, or to one rounded to the text's own precision."""
    places = 0 if value == int(value) else len(repr(float(value)).split(".")[1])
    return any(value == a or round(a, places) == value for a in allowed)


def check_numbers(text: str, facts: dict) -> list[str]:
    """One violation per number or time in `text` that is not in `facts` as the same kind."""
    allowed = allowed_values(facts)
    violations = []
    for token in extract_numbers(text):
        if isinstance(token, str):
            if token not in allowed["time"]:
                violations.append(f"time not in facts: {token}")
        elif isinstance(token, Percent):
            if not number_is_allowed(token, allowed["percent"]):
                violations.append(f"percentage not in facts: {token:g}%")
        elif isinstance(token, Meters):
            if not number_is_allowed(token, allowed["meters"]):
                violations.append(f"distance not in facts: {token:g} m")
        elif not number_is_allowed(token, allowed["number"]):
            violations.append(f"number not in facts: {token:g}")
    return violations


def split_sentences(text: str) -> list[str]:
    """Split on sentence-ending punctuation, keeping 'a.m.' / '13.0' intact."""
    parts = re.split(r"(?<![ap]\.m\.)(?<=[.!?])\s+(?=[A-Z])", text.strip())
    return [p for p in parts if p]


def check_day_type_scope(text: str) -> list[str]:
    """A sentence that names a time of day must also say weekday(s) or weekend(s)."""
    violations = []
    for sentence in split_sentences(text):
        lower = sentence.lower()
        names_time = bool(_TIME.search(sentence) or _NOON_MIDNIGHT.search(sentence)) or any(
            w in lower for w in TIME_OF_DAY_WORDS
        )
        if names_time and not any(w in lower for w in DAY_TYPE_WORDS):
            violations.append(f"time claim without weekday/weekend: {sentence!r}")
    return violations


def check_typology_hedge(text: str, facts: dict) -> list[str]:
    """When the station's own peaks contradict its type name, it may only be 'grouped with' it."""
    if facts.get("typology_matches_peak") is not False:
        return []
    violations = []
    for sentence in split_sentences(text):
        lower = sentence.lower().replace("-", " ")
        if any(name in lower for name in TYPOLOGY_SHORT_NAMES) and "grouped with" not in lower:
            violations.append(f"type named without 'grouped with': {sentence!r}")
    return violations


def check_style(text: str) -> list[str]:
    """No causal claims; at most MAX_SENTENCES sentences and MAX_WORDS words."""
    lower = text.lower()
    violations = [f"causal wording: {w!r}" for w in CAUSAL_WORDS if re.search(rf"\b{w}\b", lower)]
    if len(split_sentences(text)) > MAX_SENTENCES:
        violations.append(f"more than {MAX_SENTENCES} sentences")
    if len(text.split()) > MAX_WORDS:
        violations.append(f"more than {MAX_WORDS} words")
    return violations


def check_briefing(text: str, facts: dict) -> list[str]:
    """All violations for one briefing; an empty list means it passes."""
    return check_numbers(text, facts) + check_day_type_scope(text) + check_typology_hedge(text, facts) + check_style(text)
