"""Rate rules: bursts, steady rates just under the limit, per-tool vs total."""

from __future__ import annotations

from pathlib import Path

import pytest

from seqmon import (
    IncrementalEvaluator,
    SequenceMonitor,
    SpecError,
    ToolCallEvent,
    load_policy,
    parse_policy,
)

ROOT = Path(__file__).resolve().parents[1]


def rate(max_calls=5, window="10s", actions=None, name="r", response="terminate"):
    rule = {"name": name, "type": "rate", "max_calls": max_calls, "window": window,
            "response": response}
    if actions is not None:
        rule["actions"] = actions
    return rule


def ev(t, action="send", session="s1"):
    return ToolCallEvent(action=action, agent_id="a", session_id=session, timestamp=float(t))


def fire_points(evaluator, events):
    return [(i, v.rule) for i, e in enumerate(events) for v in evaluator.on_action(e)]


# --- bursts ---------------------------------------------------------------------

def test_burst_at_one_instant_fires_on_call_max_plus_one():
    e = IncrementalEvaluator(parse_policy({"rules": [rate(5)]}))
    assert fire_points(e, [ev(100)] * 20) == [(5, "r")]


def test_burst_after_quiet_period_fires():
    e = IncrementalEvaluator(parse_policy({"rules": [rate(5)]}))
    events = [ev(t) for t in range(0, 100, 20)] + [ev(200 + 0.01 * i) for i in range(6)]
    assert fire_points(e, events) == [(len(events) - 1, "r")]


def test_burst_straddling_window_boundary_is_split():
    # 5 calls at t=0, 5 at t=10: the window (0,10] never holds 6.
    e = IncrementalEvaluator(parse_policy({"rules": [rate(5)]}))
    assert fire_points(e, [ev(0)] * 5 + [ev(10)] * 5) == []


def test_burst_inside_window_but_not_at_one_instant():
    e = IncrementalEvaluator(parse_policy({"rules": [rate(5)]}))
    assert fire_points(e, [ev(0)] * 5 + [ev(9.999)]) == [(5, "r")]


def test_max_calls_zero_fires_on_first_call():
    e = IncrementalEvaluator(parse_policy({"rules": [rate(0)]}))
    assert fire_points(e, [ev(0)]) == [(0, "r")]


# --- steady rates ---------------------------------------------------------------------

def test_steady_rate_exactly_at_the_limit_never_fires():
    # 5 calls per 10 s, evenly spaced every 2 s, for an hour.
    e = IncrementalEvaluator(parse_policy({"rules": [rate(5)]}))
    assert fire_points(e, [ev(2 * i) for i in range(1800)]) == []


def test_steady_rate_just_over_the_limit_fires():
    # Every 1.99 s: 6 calls fall inside some 10 s window.
    e = IncrementalEvaluator(parse_policy({"rules": [rate(5)]}))
    hits = fire_points(e, [ev(1.99 * i) for i in range(100)])
    assert hits and hits[0][0] == 5


def test_steady_rate_just_under_the_limit_for_a_long_session():
    e = IncrementalEvaluator(parse_policy({"rules": [rate(30, "5m")]}))
    assert fire_points(e, [ev(10.01 * i) for i in range(5000)]) == []


# --- per-tool vs total ----------------------------------------------------------------------

def both():
    return parse_policy({"rules": [
        rate(3, actions=["send"], name="per-tool"),
        rate(5, name="total"),
    ]})


def test_per_tool_limit_ignores_other_tools():
    e = IncrementalEvaluator(both())
    events = [ev(0, "read"), ev(0, "read"), ev(0, "send"), ev(0, "send"), ev(0, "send")]
    assert fire_points(e, events) == []


def test_total_limit_counts_every_tool():
    e = IncrementalEvaluator(both())
    events = [ev(0, a) for a in ("read", "write", "send", "list", "read", "write")]
    assert fire_points(e, events) == [(5, "total")]


def test_per_tool_fires_before_total_when_it_is_tighter():
    e = IncrementalEvaluator(both())
    events = [ev(0, "send")] * 6
    assert fire_points(e, events) == [(3, "per-tool"), (5, "total")]


def test_per_tool_limit_covers_a_list_of_tools_jointly():
    p = parse_policy({"rules": [rate(2, actions=["a", "b"])]})
    assert fire_points(IncrementalEvaluator(p), [ev(0, "a"), ev(0, "b"), ev(0, "a")]) == [(2, "r")]


def test_shipped_rate_example_per_tool_and_total():
    mon = SequenceMonitor.from_file(ROOT / "policies" / "examples" / "rate.yaml", log=False)
    # 30 messages in 5 min is allowed; the 31st terminates.
    decisions = [mon.on_action(ev(i, "messaging.send")) for i in range(31)]
    assert not any(d.violations for d in decisions[:30])
    assert decisions[30].terminate and decisions[30].violations[0].rule == "message-flood"
    # 601 mixed calls within 10 minutes escalate on the total rule.
    mon2 = SequenceMonitor.from_file(ROOT / "policies" / "examples" / "rate.yaml", log=False)
    hits = [i for i in range(601)
            if mon2.on_action(ev(i * 0.5, "files.read" if i % 2 else "llm.completion")).violations]
    assert hits == [600]


def test_rate_limits_are_per_session():
    e = IncrementalEvaluator(parse_policy({"rules": [rate(2)]}))
    events = [ev(0, session=s) for s in ("A", "B", "A", "B", "C")]
    assert fire_points(e, events) == []


@pytest.mark.parametrize("cap", [3, 4])
def test_backstop_cap_below_max_calls_drops_and_counts(cap):
    """If the per-window cap is below max_calls + 1 the window can never hold
    enough events, so the rule cannot fire (see BUG-006). The excess events
    are at least dropped and counted, never silently retained."""
    p = parse_policy({"rules": [rate(5)]})
    mon = SequenceMonitor(p, log=False, max_events_per_window=cap)
    assert not any(mon.on_action(ev(0)).violations for _ in range(100))
    win = mon.store.peek("s1").window("r", 10)
    assert win.count() == cap and win.dropped == 100 - cap


@pytest.mark.xfail(strict=True, reason="BUG-006: a rate rule whose max_calls >= the per-window "
                                       "cap can never fire, and nothing rejects or warns")
def test_rule_that_cannot_fire_under_the_cap_is_rejected():
    text = ("sequence_defaults: {max_events_per_window: 100}\n"
            "sequence_rules:\n  - {name: r, type: rate, max_calls: 150, window: 1h}\n")
    with pytest.raises((SpecError, ValueError)):
        SequenceMonitor(load_policy(text), log=False)


def test_rule_that_cannot_fire_under_the_cap_is_silently_dead_today():
    """Pins the BUG-006 behaviour so a fix is noticed: 1,000 calls at one
    instant against max_calls 150 and a cap of 100 produce no violation."""
    text = ("sequence_defaults: {max_events_per_window: 100}\n"
            "sequence_rules:\n  - {name: r, type: rate, max_calls: 150, window: 1h}\n")
    mon = SequenceMonitor(load_policy(text), log=False)
    assert not any(mon.on_action(ev(0)).violations for _ in range(1000))


def test_monotonic_alias_observe_is_on_action():
    e = IncrementalEvaluator(load_policy(ROOT / "policies" / "examples" / "rate.yaml"))
    assert e.observe == e.on_action
