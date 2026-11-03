"""Prompt, request settings and template fallback for AI station briefings (Feature 1, step 3).

Method, stated plainly:
  - Model: Claude Haiku 5.5. Thinking is OFF (allowed on Haiku 5.5 at its
    default effort): a 2-3 sentence rewrite of a small facts record needs
    no reasoning, and thinking tokens bill as output.
  - The model sees ONE station's facts record (built by briefings.py) and a
    fixed system prompt with rules and three worked examples. The system
    prompt is marked for prompt caching: it is identical for all ~2,500
    requests, so after the first one it is read from cache at ~10% of the
    input price.
  - Every model output is checked by guardrails.check_briefing(). The run
    step (step 4) retries once with the problems listed, then falls back to
    template_fallback(), which is built so it always passes the same checks
    (a test runs it over every real station).
  - dry_run() costs $0: it builds the real requests for a sample and counts
    their tokens with the free count_tokens endpoint.

Run the $0 dry run:
    python3 -m pipeline.briefing_writer --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pipeline.briefings import TYPOLOGY_PEAK_SIDES

MODEL = "claude-haiku-5-5"
MAX_TOKENS = 400
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

# Anthropic list prices, USD per million tokens (Haiku 5.5, prompts <= 100K tokens).
# Checked against the claude-api skill's model table (cached 2026-10-06) -- re-check
# https://platform.claude.com/docs/en/about-claude/pricing before a paid run.
PRICE_INPUT = 0.10
PRICE_OUTPUT = 0.50
CACHE_READ_FACTOR = 0.10
CACHE_WRITE_FACTOR = 1.25
BATCH_FACTOR = 0.50
ASSUMED_OUTPUT_TOKENS = 120  # illustrative assumption until the pilot measures it

TYPE_SHORT_NAMES = {name: name.split(" (")[0].lower() for name in TYPOLOGY_PEAK_SIDES}

SYSTEM_PROMPT = """You write short station briefings for a public dashboard about Citi Bike station imbalance in New York City. Readers are NYC DOT planners, council-member staff and community boards. Many will never read a chart, so the briefing must make one station readable in five seconds.

You receive ONE station's facts record as JSON. Write EXACTLY 3 sentences, at most 70 words in total, in plain English, in this fixed shape:
- Sentence 1 (flow): the station name, its type, and its weekday fill and drain peaks; add the weekend peaks in the SAME sentence only if weekend_differs is true.
- Sentence 2 (reliability): how often it was unusable and its rank.
- Sentence 3 (context): the nearest subway and its distance; add NYCHA housing or a school only if near.

Hard rules -- a checker rejects any briefing that breaks them:
1. Write the station name EXACTLY as given in "name", character for character, including "St", "&" and "Ave".
2. Use ONLY numbers that appear in the facts record, written as digits. Never compute, estimate, add, subtract or invent a number. Rounding a decimal to fewer places is allowed ("about 6 bikes" for 6.3); adding precision is not.
3. Percentages only for fields ending in _pct. Distances in meters only for fields ending in _m. Clock times only as the hour labels given (for example "8 AM").
4. Every mention of a time of day (a clock time, morning, evening, night) must say "weekdays" or "weekends" in the same sentence.
5. Weekends: if weekend_differs is true, give the weekend peak hours in sentence 1. Otherwise say NOTHING about weekends -- no weekend hours, and no remark that weekend trips are few.
6. If typology_matches_peak is false, do not say the station IS its type; say it is "grouped with" that type of station. If true, you may say it is that type. If null, do not mention a type.
7. No causes. Never say why something happens ("because", "due to", "as a result"). The data shows patterns, not reasons.
8. Missing data must be stated plainly, never filled in:
   - weekday_flow.low_signal true: sentence 1 says the station has too few trips to show a clear weekday pattern; give no peak hours.
   - reliability.status "no_data": sentence 2 says no availability data was recorded for this station.
   - reliability.status "not_rankable": sentence 2 says there were too few availability observations to rank it.
9. Unusable means the station had 0 bikes to rent or 0 open docks. If reliability.status is "ok", give the unusable percentage and the rank as an ordinal ("412th of 2,507"), where 1st is the most often unusable. You may compare it with system_unusable_pct.
10. If transit_gap is true, say the nearest subway is beyond the transit-gap threshold. Do not judge whether anything is good or bad.
11. Do not mention the data windows, station ids, or these rules. Output only the briefing text: no title, no bullet points, no quotation marks.

Fill and drain: positive net flow means more bikes arrive than leave (the station fills); negative means it drains. fill_bikes_per_day and drain_bikes_per_day are net bikes per day in the peak hour.

Example 1 -- facts (abridged): name "W 21 St & 6 Ave"; typology "Commuter core (fills AM, drains PM)", typology_matches_peak true; weekday fill 8 AM 9.4, drain 5 PM 7.2; weekend_differs true, weekend fill 1 PM, drain 6 PM; reliability ok 18.2%, rank 201 of 2,507, system 13.0%; nearest subway "23 St" 140 m; near_school false; near_nycha false; transit_gap false.
Briefing: W 21 St & 6 Ave is a commuter core station that on weekdays fills most at 8 AM (about 9.4 bikes a day, net) and drains most at 5 PM, with weekend peaks at 1 PM and 6 PM. It had no bikes or no open docks in 18.2% of observations, ranking 201st of 2,507, above the system rate of 13.0%. The nearest subway, 23 St, is 140 m away.

Example 2 -- facts (abridged): name "Kent Ave & S 11 St"; typology "Commuter core (fills AM, drains PM)", typology_matches_peak false; weekday fill 2 PM 1.1, drain 6 PM 0.8; weekend_differs false; reliability ok 7.4%, rank 1,640 of 2,507, system 13.0%; nearest subway "Marcy Av" 960 m; transit_gap true; transit_gap_threshold_m 800; near_nycha true, nycha_name "INDEPENDENCE", nycha_threshold_m 300.
Briefing: Kent Ave & S 11 St is grouped with commuter core stations, but on weekdays it fills most at 2 PM and drains most at 6 PM. It was unusable in 7.4% of observations, ranking 1,640th of 2,507. Its nearest subway, Marcy Av, is 960 m away, beyond the 800 m transit-gap threshold, and it is within 300 m of INDEPENDENCE public housing.

Example 3 -- facts (abridged): name "Beach 98 St & Shore Front Pkwy"; typology "Low signal (excluded from clustering)", typology_matches_peak null; weekday_flow low_signal true; weekend_differs null; reliability no_data; nearest subway "Beach 98 St" 210 m; transit_gap false.
Briefing: Beach 98 St & Shore Front Pkwy has too few trips to show a clear weekday pattern. No availability data was recorded for this station. The nearest subway, Beach 98 St, is 210 m away."""


def prompt_facts(facts: dict) -> dict:
    """The facts record as the model sees it: without the windows and the station id (rule 9)."""
    return {k: v for k, v in facts.items() if k not in ("station_id", "trip_window", "reliability_window")}


def build_params(facts: dict) -> dict:
    """Messages API parameters for one station's briefing (same shape for Batch and count_tokens)."""
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "thinking": {"type": "disabled"},
        "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": json.dumps(prompt_facts(facts), ensure_ascii=False, indent=1)}],
    }


def retry_messages(facts: dict, first_text: str, problems: list[str]) -> list[dict]:
    """Second attempt: the model sees its first briefing and exactly what the checker rejected."""
    feedback = "The checker rejected that briefing:\n- " + "\n- ".join(problems) + "\nRewrite it so it follows every rule."
    return build_params(facts)["messages"] + [
        {"role": "assistant", "content": first_text},
        {"role": "user", "content": feedback},
    ]


# --- template fallback --------------------------------------------------------

def ordinal(n: int) -> str:
    """412 -> '412th', 1 -> '1st', 12 -> '12th', 2,507 -> '2,507th'."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n:,}{suffix}"


def _peaks_clause(flow: dict) -> str:
    parts = []
    if flow.get("fill_peak_hour"):
        parts.append(f"fills most at {flow['fill_peak_hour']}")
    if flow.get("drain_peak_hour"):
        parts.append(f"drains most at {flow['drain_peak_hour']}")
    return " and ".join(parts) if parts else "shows no clear peak"


def _flow_sentence(facts: dict) -> str:
    name, weekday, weekend = facts["name"].strip(), facts["weekday_flow"], facts["weekend_flow"]
    if weekday["low_signal"]:
        return f"{name} has too few trips to show a clear weekday pattern."
    short = TYPE_SHORT_NAMES.get(facts["typology"])
    match = facts["typology_matches_peak"]
    if short and match is True:
        lead = f"{name} is a {short} station: on weekdays it"
    elif short and match is False:
        lead = f"{name} is grouped with {short} stations; on weekdays it"
    else:
        lead = f"On weekdays, {name}"
    sentence = f"{lead} {_peaks_clause(weekday)}"
    if facts["weekend_differs"]:
        hours = [h for h in (weekend.get("fill_peak_hour"), weekend.get("drain_peak_hour")) if h]
        if hours:
            sentence += f", while on weekends its peaks move to {' and '.join(hours)}"
    return sentence + "."


def _reliability_sentence(reliability: dict) -> str:
    status = reliability["status"]
    if status == "no_data":
        return "No availability data was recorded for this station."
    if status == "not_rankable":
        return "There were too few availability observations to rank it."
    return (
        f"It had no bikes or no open docks in {reliability['unusable_pct']:.1f}% of observations "
        f"({ordinal(reliability['rank'])} of {reliability['n_ranked']:,}; system {reliability['system_unusable_pct']:.1f}%)."
    )


def _context_sentence(context: dict) -> str:
    sentence = f"Nearest subway: {context['nearest_subway']}, {context['subway_distance_m']:,} m"
    if context["transit_gap"]:
        sentence += f" (beyond the {context['transit_gap_threshold_m']:,} m transit-gap threshold)"
    near = []
    if context["near_nycha"]:
        near.append(f"{context['nycha_name']} public housing")
    if context["near_school"]:
        near.append(f"{context['school_name']}")
    if near:
        sentence += f"; within {context['nycha_threshold_m']:,} m of {' and '.join(near)}"
    return sentence + "."


def template_fallback(facts: dict) -> str:
    """A plain, rule-based briefing built only from the facts -- the safe fallback."""
    return " ".join([_flow_sentence(facts), _reliability_sentence(facts["reliability"]), _context_sentence(facts["context"])])


# --- $0 dry run -----------------------------------------------------------------

def load_api_key(env_path: Path = ENV_PATH) -> None:
    """Put ANTHROPIC_API_KEY from the gitignored .env into the environment, never printing it."""
    if "ANTHROPIC_API_KEY" in os.environ or not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        if line.startswith("ANTHROPIC_API_KEY="):
            os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()


def pilot_sample(all_facts: dict[str, dict], n: int = 20) -> list[str]:
    """A fixed, varied sample: round-robin across station kinds, sorted ids within each kind.

    Kinds: grouped-with hedge, matches its type, weekday low-signal, no reliability
    data, weekend differs. Deterministic, so the pilot and its review are repeatable.
    """
    kinds = {
        "hedge": lambda f: f["typology_matches_peak"] is False,
        "matches": lambda f: f["typology_matches_peak"] is True,
        "low_signal": lambda f: f["weekday_flow"]["low_signal"],
        "no_data": lambda f: f["reliability"]["status"] == "no_data",
        "weekend_differs": lambda f: f["weekend_differs"] is True,
    }
    pools = {k: sorted(i for i, f in all_facts.items() if test(f)) for k, test in kinds.items()}
    chosen: list[str] = []
    while len(chosen) < n and any(pools.values()):
        for pool in pools.values():
            while pool and pool[0] in chosen:
                pool.pop(0)
            if pool and len(chosen) < n:
                chosen.append(pool.pop(0))
    return chosen


def estimate_cost(input_tokens: list[int], n_stations: int, system_tokens: int, output_tokens: int = ASSUMED_OUTPUT_TOKENS) -> dict:
    """USD estimates for a full run; output tokens are an assumption until the pilot measures them."""
    mean_input = sum(input_tokens) / len(input_tokens)
    uncached = n_stations * (mean_input * PRICE_INPUT + output_tokens * PRICE_OUTPUT) / 1e6
    per_station_cached_input = (mean_input - system_tokens) + system_tokens * CACHE_READ_FACTOR
    cached = (system_tokens * CACHE_WRITE_FACTOR * PRICE_INPUT
              + n_stations * (per_station_cached_input * PRICE_INPUT + output_tokens * PRICE_OUTPUT)) / 1e6
    return {
        "mean_input_tokens": round(mean_input),
        "standard": round(uncached, 3),
        "standard_cached": round(cached, 3),
        "batch": round(uncached * BATCH_FACTOR, 3),
        "batch_cached": round(cached * BATCH_FACTOR, 3),
    }


PILOT_PATH = Path(__file__).resolve().parent.parent.parent / "doc" / "briefings_pilot.json"  # local, outside the repo


def response_text(message) -> str:
    """The briefing text, or '' when the model refused or ran out of tokens."""
    if message.stop_reason not in ("end_turn", "stop_sequence"):
        return ""
    return " ".join(b.text for b in message.content if b.type == "text").strip()


def usage_cost(usage) -> float:
    """USD for one response, from its real token counts (standard API prices)."""
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    return (
        usage.input_tokens * PRICE_INPUT
        + cache_write * PRICE_INPUT * CACHE_WRITE_FACTOR
        + cache_read * PRICE_INPUT * CACHE_READ_FACTOR
        + usage.output_tokens * PRICE_OUTPUT
    ) / 1e6


def write_one(client, facts: dict) -> dict:
    """Model briefing -> check -> one retry with the problems -> template fallback."""
    from pipeline.guardrails import check_briefing

    params = build_params(facts)
    first = client.messages.create(**params)
    text = response_text(first)
    problems = check_briefing(text, facts) if text else ["no text (stop_reason: %s)" % first.stop_reason]
    record = {"first_text": text, "first_problems": problems, "usage": [first.usage.model_dump()]}
    if not problems:
        return {**record, "text": text, "source": "model", "cost": usage_cost(first.usage)}

    second = client.messages.create(**{**params, "messages": retry_messages(facts, text, problems)})
    text2 = response_text(second)
    problems2 = check_briefing(text2, facts) if text2 else ["no text (stop_reason: %s)" % second.stop_reason]
    record.update(retry_text=text2, retry_problems=problems2, usage=record["usage"] + [second.usage.model_dump()])
    cost = usage_cost(first.usage) + usage_cost(second.usage)
    if not problems2:
        return {**record, "text": text2, "source": "model_retry", "cost": cost}
    return {**record, "text": template_fallback(facts), "source": "template", "cost": cost}


def pilot(n: int = 20, out_path: Path = PILOT_PATH, holdout: int = 0) -> None:
    """Paid (~$0.01): write briefings for the fixed pilot sample (plus `holdout` new stations) and save them for review.

    The holdout stations come right after the pilot sample in the same fixed
    order, so they were never looked at while tuning the prompt.
    """
    import anthropic

    from pipeline.briefings import FLOWS_PATH, MANIFEST_PATH, RELIABILITY_PATH, build_all_facts, facts_hash

    all_facts = build_all_facts(
        json.loads(FLOWS_PATH.read_text()), json.loads(RELIABILITY_PATH.read_text()), json.loads(MANIFEST_PATH.read_text())
    )
    load_api_key()
    client = anthropic.Anthropic()
    ordered = pilot_sample(all_facts, n + holdout)
    results = {}
    for position, station_id in enumerate(ordered):
        facts = all_facts[station_id]
        results[station_id] = {
            **write_one(client, facts),
            "set": "pilot" if position < n else "holdout",
            "facts": facts,
            "facts_hash": facts_hash(facts),
        }
        r = results[station_id]
        print(f"{station_id:>12}  {r['source']:<12} ${r['cost']:.5f}")

    summary = {"all": summarize(results), **{s: summarize({k: r for k, r in results.items() if r["set"] == s})
                                            for s in ("pilot", "holdout") if any(r["set"] == s for r in results.values())}}
    out_path.write_text(json.dumps({"summary": summary, "results": results}, indent=1, ensure_ascii=False))
    print(json.dumps(summary, indent=1))
    print(f"wrote {out_path}")


def summarize(results: dict) -> dict:
    """Pass / retry / fallback counts, real token usage and cost for a set of results."""
    sources = [r["source"] for r in results.values()]
    usage = [u for r in results.values() for u in r["usage"]]
    total = sum(r["cost"] for r in results.values())
    return {
        "model": MODEL,
        "n": len(results),
        "model_first_try": sources.count("model"),
        "model_after_retry": sources.count("model_retry"),
        "template_fallback": sources.count("template"),
        "api_calls": len(usage),
        "input_tokens": sum(u["input_tokens"] for u in usage),
        "cache_write_tokens": sum(u.get("cache_creation_input_tokens") or 0 for u in usage),
        "cache_read_tokens": sum(u.get("cache_read_input_tokens") or 0 for u in usage),
        "output_tokens": sum(u["output_tokens"] for u in usage),
        "mean_output_tokens": round(sum(u["output_tokens"] for u in usage) / len(usage), 1),
        "cost_usd": round(total, 5),
    }


def review_page(results_path: Path, out_path: Path) -> None:
    """A local HTML page to review saved pilot results by eye: facts, text, source, checker verdict."""
    import html

    from pipeline.guardrails import check_briefing

    data = json.loads(results_path.read_text())
    rows = []
    for station_id, r in data["results"].items():
        f = r["facts"]
        rel = f["reliability"]
        wd, we = f["weekday_flow"], f["weekend_flow"]
        facts_lines = [
            f"<b>{html.escape(f['name'])}</b> ({station_id})",
            f"type: {html.escape(f['typology'])} · matches peak: {f['typology_matches_peak']}",
            "weekday: low signal" if wd["low_signal"] else f"weekday: fill {wd['fill_peak_hour']} ({wd['fill_bikes_per_day']}), drain {wd['drain_peak_hour']} ({wd['drain_bikes_per_day']})",
            "weekend: low signal" if we["low_signal"] else f"weekend: fill {we['fill_peak_hour']}, drain {we['drain_peak_hour']} · differs: {f['weekend_differs']}",
            f"reliability: {rel['status']}" + (f" · {rel['unusable_pct']}% · rank {rel['rank']:,} of {rel['n_ranked']:,} · system {rel['system_unusable_pct']}%" if rel["status"] == "ok" else ""),
            f"subway: {html.escape(f['context']['nearest_subway'])} {f['context']['subway_distance_m']} m · gap: {f['context']['transit_gap']} · NYCHA: {f['context']['near_nycha']} · school: {f['context']['near_school']}",
        ]
        problems = check_briefing(r["text"], f)
        verdict = "passes" if not problems else "FAILS: " + "; ".join(problems)
        first = "" if r["source"] == "model" else f"<details><summary>first draft rejected</summary>{html.escape(r['first_text'])}<br><i>{html.escape('; '.join(r['first_problems']))}</i></details>"
        rows.append(
            f"<tr><td>{'<br>'.join(facts_lines)}</td><td>{html.escape(r['text'])}{first}</td>"
            f"<td class='{r['source']}'>{r['source']}<br><small>{r.get('set', '')}</small></td><td>{html.escape(verdict)}</td></tr>"
        )
    summary = html.escape(json.dumps(data["summary"].get("all", data["summary"]), indent=1))
    out_path.write_text(f"""<!doctype html><meta charset="utf-8"><title>Briefings pilot review</title>
<style>body{{font:14px/1.45 system-ui,sans-serif;margin:24px;color:#1a1a1a}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #ccc;padding:8px;vertical-align:top}}th{{background:#f3f3f3;text-align:left}}td:nth-child(1){{width:32%;font-size:12px}}
td:nth-child(2){{width:44%}}.model{{color:#176b2c}}.model_retry{{color:#8a5a00}}.template{{color:#a01c1c}}pre{{background:#f6f6f6;padding:8px}}</style>
<h1>Briefings pilot review</h1><p>Read each briefing against its facts on the left. The checker verdict is re-run when this page is built.</p>
<pre>{summary}</pre><table><tr><th>Facts (what the model saw)</th><th>Briefing</th><th>Source</th><th>Checker</th></tr>{''.join(rows)}</table>""")
    print(f"wrote {out_path}")


def one_station(station_id: str, live: bool = False) -> None:
    """Show one station's facts, its template briefing, and (with live=True, ~$0.0002) a model briefing."""
    from pipeline.briefings import FLOWS_PATH, MANIFEST_PATH, RELIABILITY_PATH, build_all_facts
    from pipeline.guardrails import check_briefing

    all_facts = build_all_facts(
        json.loads(FLOWS_PATH.read_text()), json.loads(RELIABILITY_PATH.read_text()), json.loads(MANIFEST_PATH.read_text())
    )
    if station_id not in all_facts:
        print(f"{station_id!r} has no facts record (not in flows.json, or excluded)")
        return
    facts = all_facts[station_id]
    print(json.dumps(prompt_facts(facts), indent=1, ensure_ascii=False))
    template = template_fallback(facts)
    print(f"\nTEMPLATE: {template}\nchecker: {check_briefing(template, facts) or 'passes'}")
    if live:
        import anthropic

        load_api_key()
        result = write_one(anthropic.Anthropic(), facts)
        print(f"\nMODEL ({result['source']}, ${result['cost']:.5f}): {result['text']}")
        print(f"checker: {check_briefing(result['text'], facts) or 'passes'}")


def dry_run(n: int = 20) -> None:
    """$0: build real requests for the pilot sample, count tokens, print estimates and one prompt."""
    import anthropic

    from pipeline.briefings import FLOWS_PATH, MANIFEST_PATH, RELIABILITY_PATH, build_all_facts
    from pipeline.guardrails import check_briefing

    all_facts = build_all_facts(
        json.loads(FLOWS_PATH.read_text()), json.loads(RELIABILITY_PATH.read_text()), json.loads(MANIFEST_PATH.read_text())
    )
    sample = pilot_sample(all_facts, n)
    load_api_key()
    client = anthropic.Anthropic()

    def count(params: dict) -> int:
        return client.messages.count_tokens(
            model=params["model"], system=params["system"], messages=params["messages"], thinking=params["thinking"]
        ).input_tokens

    system_tokens = count({**build_params(all_facts[sample[0]]), "messages": [{"role": "user", "content": "{}"}]})
    counts = [count(build_params(all_facts[i])) for i in sample]
    fallback_failures = sum(1 for f in all_facts.values() if check_briefing(template_fallback(f), f))

    estimate = estimate_cost(counts, len(all_facts), system_tokens)
    pilot = estimate_cost(counts, len(sample), system_tokens)
    print(f"stations with facts: {len(all_facts)}  |  pilot sample: {len(sample)}")
    print(f"system prompt: {system_tokens} tokens (cache minimum on Haiku 5.5: 512)")
    print(f"input tokens per request: min {min(counts)}, mean {estimate['mean_input_tokens']}, max {max(counts)}")
    print(f"output tokens assumed: {ASSUMED_OUTPUT_TOKENS} (illustrative -- the pilot measures the real number)")
    print(f"pilot ({len(sample)} stations, standard API): ~${pilot['standard']:.3f}")
    print(f"full run ({len(all_facts)} stations): standard ${estimate['standard']:.2f} | "
          f"standard+cache ${estimate['standard_cached']:.2f} | batch ${estimate['batch']:.2f} | "
          f"batch+cache ${estimate['batch_cached']:.2f}")
    print(f"template fallback fails the checker on {fallback_failures} of {len(all_facts)} stations")
    print(f"\nsample request user message ({sample[0]}):\n{build_params(all_facts[sample[0]])['messages'][0]['content']}")
    print(f"\nits template fallback:\n{template_fallback(all_facts[sample[0]])}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="$0: count tokens and estimate cost")
    parser.add_argument("--pilot", action="store_true", help="paid (~$0.01): write briefings for the pilot sample")
    parser.add_argument("--review", type=Path, help="build a local HTML review page from saved pilot results")
    parser.add_argument("--station", help="show one station's facts and template briefing")
    parser.add_argument("--live", action="store_true", help="with --station: also ask the model (~$0.0002)")
    parser.add_argument("-n", type=int, default=20, help="pilot sample size")
    parser.add_argument("--holdout", type=int, default=0, help="extra never-reviewed stations after the pilot sample")
    parser.add_argument("--out", type=Path, default=PILOT_PATH, help="where to save pilot results (keep it outside the repo)")
    args = parser.parse_args()
    if args.review:
        review_page(args.review, args.review.with_suffix(".html"))
    elif args.station:
        one_station(args.station, args.live)
    elif args.dry_run:
        dry_run(args.n)
    elif args.pilot:
        pilot(args.n, args.out, args.holdout)
    else:
        parser.print_help()
