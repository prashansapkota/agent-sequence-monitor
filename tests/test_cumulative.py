"""Cumulative rules: the threshold edge, window eviction, the exact window
boundary, session isolation, and floating-point accumulation."""

from __future__ import annotations

import pytest

from seqmon import IncrementalEvaluator, ToolCallEvent, parse_policy


def policy(threshold=100, window="10s", aggregate="sum", **extra):
    rule = {"name": "c", "type": "cumulative", "threshold": threshold,
            "window": window, "aggregate": aggregate, "response": "terminate"}
    rule.update(extra)
    return parse_policy({"rules": [rule]})


def ev(t, m=0.0, *, session="s1", action="q", resource=None, attrs=None):
    return ToolCallEvent(action=action, agent_id="a", session_id=session,
                         timestamp=float(t), magnitude=m, resource=resource,
                         attributes=attrs or {})


def fired(evaluator, events):
    """Indices of the events that produced a violation."""
    return [i for i, e in enumerate(events) if evaluator.on_action(e)]


# --- exactly at the limit vs one past ----------------------------------------

def test_sum_exactly_at_threshold_does_not_fire():
    assert fired(IncrementalEvaluator(policy(100)), [ev(0, 60), ev(1, 40)]) == []


def test_sum_one_past_threshold_fires_on_that_event():
    assert fired(IncrementalEvaluator(policy(100)), [ev(0, 60), ev(1, 40), ev(2, 1)]) == [2]


def test_count_exactly_at_threshold_vs_one_past():
    e = IncrementalEvaluator(policy(3, aggregate="count"))
    assert fired(e, [ev(0), ev(1), ev(2)]) == []
    v = e.on_action(ev(3))
    assert v and v[0].observed == 4 and v[0].threshold == 3


def test_distinct_exactly_at_threshold_vs_one_past():
    e = IncrementalEvaluator(policy(2, aggregate="distinct"))
    events = [ev(0, resource="a"), ev(1, resource="b"), ev(2, resource="a"),
              ev(3, resource=None), ev(4, resource="c")]
    assert fired(e, events) == [4]


def test_threshold_zero_fires_on_first_positive_value():
    assert fired(IncrementalEvaluator(policy(0)), [ev(0, 0), ev(1, 0.001)]) == [1]


def test_attribute_sum_ignores_magnitude_and_missing_keys_count_zero():
    p = policy(10, attribute="rows")
    events = [ev(0, 999, attrs={"rows": 6}), ev(1, 999), ev(2, 0, attrs={"rows": 4}),
              ev(3, 0, attrs={"rows": 1})]
    assert fired(IncrementalEvaluator(p), events) == [3]


def test_negative_values_offset_the_sum_as_documented():
    # docs: negatives allowed (refunds) and offset a sum.
    assert fired(IncrementalEvaluator(policy(100)), [ev(0, 90), ev(1, -50), ev(2, 50)]) == []


def test_violation_reports_observed_and_events_elapsed():
    e = IncrementalEvaluator(policy(100))
    [e.on_action(x) for x in (ev(0, 50), ev(1, 50))]
    [v] = e.on_action(ev(2, 7))
    assert (v.observed, v.threshold, v.events_elapsed, v.kind) == (107, 100, 3, "cumulative")


# --- window eviction -----------------------------------------------------------

def test_old_events_are_evicted_and_stop_counting():
    # 10 s window: 60 at t=0 is gone by t=10, so 60 + 60 never coexist.
    assert fired(IncrementalEvaluator(policy(100)), [ev(0, 60), ev(10, 60), ev(20, 60)]) == []


def test_eviction_is_partial():
    events = [ev(0, 50), ev(5, 30), ev(12, 30), ev(13, 41)]
    # at t=12: window (2,12] holds 30 + 30 = 60; at t=13: (3,13] holds 30+30+41 = 101
    assert fired(IncrementalEvaluator(policy(100)), events) == [3]


def test_long_gap_empties_window_completely():
    e = IncrementalEvaluator(policy(100), repeat_alerts=True)
    fired(e, [ev(i, 10) for i in range(9)])
    assert e.store.peek("s1").memory_footprint() == 9
    e.on_action(ev(1000, 1))
    assert e.store.peek("s1").memory_footprint() == 1


# --- exactly on the boundary ------------------------------------------------------

@pytest.mark.parametrize("dt, expect_fire", [
    (9.0, True), (9.999, True), (10.0, False), (10.001, False),
])
def test_window_boundary_is_half_open(dt, expect_fire):
    """Window is (now - 10, now]: an event exactly 10 s old is out."""
    e = IncrementalEvaluator(policy(100))
    assert not e.on_action(ev(0, 60))
    assert bool(e.on_action(ev(dt, 60))) is expect_fire


def test_equal_timestamps_all_count():
    assert fired(IncrementalEvaluator(policy(100)), [ev(5, 34)] * 3) == [2]


# --- sessions are isolated -------------------------------------------------------

def test_two_sessions_do_not_share_sums():
    e = IncrementalEvaluator(policy(100))
    events = [ev(0, 60, session="A"), ev(1, 60, session="B"),
              ev(2, 30, session="A"), ev(3, 30, session="B")]
    assert fired(e, events) == []
    assert e.on_action(ev(4, 11, session="A"))
    assert e.store.peek("B").window("c", 10).total == 90


def test_many_interleaved_sessions_fire_independently():
    e = IncrementalEvaluator(policy(100))
    sessions = [f"s{i}" for i in range(50)]
    hits = set()
    for step in range(3):
        for sid in sessions:
            for v in e.on_action(ev(step, 40, session=sid)):
                hits.add((v.session_id, v.events_elapsed))
    assert hits == {(sid, 3) for sid in sessions}


def test_session_reset_only_affects_that_session():
    e = IncrementalEvaluator(policy(100))
    for sid in ("A", "B"):
        e.on_action(ev(0, 90, session=sid))
    e.reset("A")
    assert not e.on_action(ev(1, 20, session="A"))
    assert e.on_action(ev(1, 20, session="B"))


def test_same_rule_fires_once_per_session_by_default():
    e = IncrementalEvaluator(policy(100))
    assert fired(e, [ev(i, 60) for i in range(5)]) == [1]


# --- floating point (BUGS.md) ---------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="BUG-001: running float sum overshoots; 500 x $0.10 "
                                       "against a $50 threshold fires (observed 50.00000000000044)")
def test_decimal_charges_summing_exactly_to_threshold_do_not_fire():
    p = policy(50.0, window="1d", attribute="cost_usd")
    events = [ev(i, attrs={"cost_usd": 0.10}) for i in range(500)]
    assert fired(IncrementalEvaluator(p), events) == []


@pytest.mark.xfail(strict=True, reason="BUG-001: 0.1 + 0.1 + 0.1 > 0.3 in the running sum")
def test_three_tenths_at_threshold_point_three_do_not_fire():
    assert fired(IncrementalEvaluator(policy(0.3)), [ev(i, 0.1) for i in range(3)]) == []


@pytest.mark.xfail(strict=True, reason="BUG-002: eviction leaves a float residue (1.1e-16) when "
                                       "the remaining in-window events sum to exactly 0")
def test_residue_after_eviction_does_not_fire_a_zero_threshold():
    e = IncrementalEvaluator(policy(0), repeat_alerts=True)
    for i, m in enumerate([0.1, 0.2, 0.3]):
        e.on_action(ev(i, m))
    for t in (10.5, 11.5):
        e.on_action(ev(t, 0.0))
    # Window (2.5, 12.5] holds three 0.0 events: the true sum is 0.
    assert e.on_action(ev(12.5, 0.0)) == []
