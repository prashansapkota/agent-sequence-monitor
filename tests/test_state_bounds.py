"""Memory bounds under long sessions (100k+ events) and many sessions.

Checks every growth path in the state: per-rule deques, the distinct-resource
dict, the fired set, the per-session window dict, the session store, and the
response history. Bounds asserted are the ones documented in
``src/seqmon/state.py``.
"""

from __future__ import annotations

import pytest

from seqmon import IncrementalEvaluator, SequenceMonitor, ToolCallEvent, parse_policy

N = 100_000

ALL_FOUR = {"rules": [
    {"name": "sum", "type": "cumulative", "threshold": 1e12, "window": "1m"},
    {"name": "distinct", "type": "cumulative", "aggregate": "distinct",
     "threshold": 1e12, "window": "1m"},
    {"name": "rate", "type": "rate", "max_calls": 10**9, "window": "1m"},
    {"name": "order", "type": "ordering", "before": "read", "after": "send", "window": "1m"},
    {"name": "scope", "type": "scope", "allowed": ["ok"], "max_outside": 10**9,
     "window": "1m"},
]}


def ev(i, *, session="s1", action="read", resource=None, t=None):
    return ToolCallEvent(action=action, agent_id="a", session_id=session,
                         timestamp=float(i if t is None else t),
                         magnitude=1.0, resource=resource)


def windows(evaluator, sid="s1"):
    return evaluator.store.peek(sid)._windows


def test_100k_events_window_bounded_by_time_span():
    """1 event/s against 60 s windows: each deque holds at most 60 events,
    and the distinct dict at most 60 keys, however long the session."""
    e = IncrementalEvaluator(parse_policy(ALL_FOUR), repeat_alerts=True)
    peak = 0
    for i in range(N):
        e.on_action(ev(i, resource=f"res-{i}"))   # every resource unique
        if i % 9973 == 0:
            peak = max(peak, max(len(w.events) for w in windows(e).values()))
    st = e.store.peek("s1")
    assert st.total_events == N
    for name, w in windows(e).items():
        assert len(w.events) <= 60, name
        assert len(w._resources) <= 60, name
    assert peak <= 60
    assert len(windows(e)) <= len(ALL_FOUR["rules"])
    assert st.memory_footprint() <= 60 * len(ALL_FOUR["rules"])


def test_100k_events_in_one_window_bounded_by_cap():
    """All events at the same instant: time eviction cannot help, so the
    backstop cap must hold every deque and dict at <= cap."""
    cap = 500
    mon = SequenceMonitor(parse_policy(ALL_FOUR), log=False, max_events_per_window=cap)
    for i in range(N):
        mon.on_action(ev(0, resource=f"res-{i}", action="read" if i % 2 else "write"))
    st = mon.store.peek("s1")
    for name, w in st._windows.items():
        assert len(w.events) <= cap, name
        assert len(w._resources) <= cap, name
    assert mon.footprint("s1") <= cap * len(ALL_FOUR["rules"])
    assert sum(w.dropped for w in st._windows.values()) > 0


def test_ordering_window_is_bounded_by_cap_even_with_only_before_events():
    """REVIEW MINOR 4: ordering keeps every ``before`` event; bounded only by the cap."""
    cap = 1000
    mon = SequenceMonitor(parse_policy(ALL_FOUR), log=False, max_events_per_window=cap)
    for i in range(N):
        mon.on_action(ev(i * 1e-4, action="read"))   # 100k reads inside 10 s
    assert len(mon.store.peek("s1")._windows["order"].events) == cap


def test_fired_set_and_history_bounded_with_repeat_alerts():
    p = parse_policy({"rules": [
        {"name": f"r{k}", "type": "rate", "max_calls": 0, "window": "1s"} for k in range(5)
    ]})
    mon = SequenceMonitor(p, log=False, repeat_alerts=True)
    for i in range(N):
        assert len(mon.on_action(ev(i)).violations) == 5
    assert len(mon.store.peek("s1").fired) == 5
    assert len(mon.responder.history) == 1_000


def test_many_sessions_bounded_with_a_clock():
    """With a clock, sessions idle for longer than the longest window are
    swept, so 100k one-shot sessions do not accumulate."""
    now = [0.0]
    p = parse_policy({"rules": [{"name": "r", "type": "rate", "max_calls": 5,
                                 "window": "10s"}]})
    e = IncrementalEvaluator(p, clock=lambda: now[0], max_clock_skew=1.0)
    peak = 0
    for i in range(N):
        now[0] = i * 0.01
        e.on_action(ev(0, session=f"s{i}", t=now[0]))
        peak = max(peak, len(e.store))
    # One session per 0.01 s, ttl 10 s, sweep every ttl/16, plus the skew backoff.
    assert peak <= (10 + 10 / 16 + 1) / 0.01 + 2
    assert e.store.expired > N - peak - 1


def test_many_sessions_bounded_with_shared_time_base():
    p = parse_policy({"rules": [{"name": "r", "type": "rate", "max_calls": 5,
                                 "window": "10s"}]})
    e = IncrementalEvaluator(p, shared_time_base=True)
    for i in range(N):
        e.on_action(ev(0, session=f"s{i}", t=i * 0.01))
    assert len(e.store) <= (10 + 10 / 16) / 0.01 + 2


def test_returning_session_after_idle_gap_does_not_keep_old_state():
    p = parse_policy(ALL_FOUR)
    e = IncrementalEvaluator(p)
    for i in range(1000):
        e.on_action(ev(i))
    e.on_action(ev(10_000))
    assert e.store.peek("s1").total_events == 1


@pytest.mark.xfail(strict=True, reason="BUG-009 (documented limitation): with the default "
                                       "configuration (no clock, no shared_time_base) sessions "
                                       "that never return are never dropped")
def test_many_sessions_bounded_in_default_configuration():
    p = parse_policy({"rules": [{"name": "r", "type": "rate", "max_calls": 5,
                                 "window": "10s"}]})
    mon = SequenceMonitor(p, log=False)
    for i in range(N):
        mon.on_action(ev(0, session=f"s{i}", t=i * 0.01))
    assert len(mon.store) <= 2_000
