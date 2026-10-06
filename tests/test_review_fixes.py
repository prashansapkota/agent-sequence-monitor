"""Regression tests for the 2026-10-06 review findings (REVIEW.md).

M1: timestamp contract (late, equal and far-future timestamps).
M2: memory bounded across sessions, not only per session.
M3: an approved escalation re-arms the rule instead of muting it.
M4: unknown rule keys and AGT's ``action:`` verdict key are rejected.
Plus the cheap MINOR items (numeric validation, ordering before == after,
window boundary, describe() for ordering, per-call fail-closed).
"""

from __future__ import annotations

import math
import random
from pathlib import Path

import pytest

from bench.naive import NaiveEvaluator
from seqmon import (
    IncrementalEvaluator,
    SequenceMonitor,
    SpecError,
    ToolCallEvent,
    load_policy,
)
from seqmon.adapters import FakeAGTInterceptor, FakePerCallEngine

ROOT = Path(__file__).resolve().parents[1]
POLICY = load_policy(ROOT / "policies" / "example.yaml")


def ev(action, t, *, resource=None, magnitude=0.0, session="s1", attributes=None):
    return ToolCallEvent(action=action, agent_id="a", session_id=session,
                         timestamp=float(t), resource=resource, magnitude=magnitude,
                         attributes=attributes or {})


RATE = load_policy("rules:\n  - {name: r, type: rate, max_calls: 2, window: 10s}\n")
CUM = load_policy("rules:\n  - {name: c, type: cumulative, threshold: 100, window: 1h}\n")
ORDER = load_policy(
    "rules:\n  - {name: o, type: ordering, before: a, after: b, window: 10m}\n"
)


def alerts(evaluator, events):
    return [(i, v.rule, v.observed) for i, e in enumerate(events)
            for v in evaluator.on_action(e)]


def naive(policy, events):
    n = NaiveEvaluator(policy)
    return [(i, rule, obs) for i, e in enumerate(events) for rule, obs in n.observe(e)]


# --- M1: timestamps -------------------------------------------------------

def test_late_event_is_clamped_and_matches_oracle():
    events = [ev("x", 100), ev("x", 0), ev("x", 105)]
    inc = alerts(IncrementalEvaluator(RATE), events)
    assert inc == naive(RATE, events)
    # The late call is counted at its arrival (t=100), so 3 calls in 5 s.
    assert inc == [(2, "r", 3.0)]


def test_clamping_is_counted_on_the_session():
    e = IncrementalEvaluator(RATE)
    for t in (100, 0, 50, 105):
        e.on_action(ev("x", t))
    state = e.store.peek("s1")
    assert state is not None and state.clamped_events == 2
    assert state.last_timestamp == 105


def test_ordering_gap_is_never_negative():
    out = IncrementalEvaluator(ORDER).on_action(ev("a", 100))
    assert out == []
    v = IncrementalEvaluator(ORDER)
    v.on_action(ev("a", 100))
    (violation,) = v.on_action(ev("b", 50))
    assert violation.observed == 0.0


def test_equal_timestamps_are_not_clamped():
    e = IncrementalEvaluator(RATE)
    fired = [bool(e.on_action(ev("x", 5))) for _ in range(3)]
    assert fired == [False, False, True]
    assert e.store.peek("s1").clamped_events == 0


def test_far_future_stamp_cannot_empty_windows_when_a_clock_is_given():
    now = [0.0]
    e = IncrementalEvaluator(CUM, clock=lambda: now[0], max_clock_skew=60)
    assert e.on_action(ev("x", 0, magnitude=90)) == []
    assert e.on_action(ev("x", 1e12, magnitude=0)) == []   # clamped to 60
    now[0] = 2.0
    (v,) = e.on_action(ev("x", 2, magnitude=90))            # clamped up to 60
    assert v.observed == 180


def test_far_future_stamp_without_clock_is_the_documented_limit():
    e = IncrementalEvaluator(CUM)
    e.on_action(ev("x", 0, magnitude=90))
    e.on_action(ev("x", 1e12, magnitude=0))
    # Without a clock the interceptor's timestamps are trusted; the later
    # event is clamped to 1e12, and the first 90 has left the window.
    assert e.on_action(ev("x", 2, magnitude=90)) == []


def random_unordered_trace(seed, n=300):
    rng = random.Random(seed)
    actions = ["customer_db.query", "files.read", "messaging.send",
               "messaging.send_external", "billing.charge"]
    resources = ["customer_db.orders", "customer_db.customers", "files/secret", None]
    t, out = 0.0, []
    for _ in range(n):
        t += rng.choice([0.0, 1.0, 30.0, 900.0])
        jitter = rng.choice([0.0, 0.0, 0.0, -400.0, -5.0])   # some late arrivals
        rows = rng.choice([0, 50, 400, 1500])
        out.append(ev(rng.choice(actions), t + jitter, resource=rng.choice(resources),
                      magnitude=rng.choice([0, 1, 50]), attributes={"records": rows},
                      session=rng.choice(["s1", "s2"])))
    return out


ATTR_POLICY = load_policy(
    "rules:\n"
    "  - {name: rows, type: cumulative, attribute: records, threshold: 3000, "
    "window: 1h, actions: customer_db.query}\n"
    "  - {name: send-after-read, type: ordering, before: customer_db.query, "
    "after: messaging.send_external, window: 10m}\n"
    "  - {name: burst, type: rate, max_calls: 5, window: 1m}\n"
)


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize("policy", [POLICY, ATTR_POLICY], ids=["example", "attributes"])
def test_incremental_matches_oracle_on_unordered_traces(seed, policy):
    events = random_unordered_trace(seed)
    inc = [(i, r) for i, r, _ in alerts(IncrementalEvaluator(policy), events)]
    assert inc == [(i, r) for i, r, _ in naive(policy, events)]


# --- window boundary (MINOR 6) ----------------------------------------------

@pytest.mark.parametrize("gap,fires", [(9.999, True), (10.0, False), (10.001, False)])
def test_window_is_half_open(gap, fires):
    """Window is (now - span, now]: an event exactly ``span`` old is out."""
    e = IncrementalEvaluator(RATE)
    e.on_action(ev("x", 0))
    e.on_action(ev("x", gap))
    assert bool(e.on_action(ev("x", gap))) is fires


# --- M2: memory across sessions ----------------------------------------------

def test_idle_sessions_are_expired():
    # One trace, one time base: event-time sweeping is declared safe.
    # (Changed 2026-10-06, R2-M1: the default no longer sweeps on event time.)
    mon = SequenceMonitor(POLICY, log=False, shared_time_base=True)
    day = 86_400
    for i in range(5_000):
        mon.on_action(ev("files.read", i * 30, session=f"s{i}"))
    # Only sessions seen within the longest window (1 day) are kept.
    # Sweeps run every ttl/16 of event time, so idle sessions linger <= 1.0625 x ttl.
    assert len(mon.store) <= (day * 17 // 16) // 30 + 1
    assert mon.store.expired == 5_000 - len(mon.store)


def test_returning_session_after_longest_window_starts_fresh():
    mon = SequenceMonitor(RATE, log=False)
    for t in (0, 1, 2):
        mon.on_action(ev("x", t))
    assert mon.store.peek("s1").fired == {"r"}
    mon.on_action(ev("x", 1_000))
    assert mon.store.peek("s1").fired == set()
    assert mon.store.peek("s1").total_events == 1


def test_footprint_does_not_create_a_session():
    mon = SequenceMonitor(POLICY, log=False)
    assert mon.footprint("never-seen") == 0
    assert len(mon.store) == 0


def test_reset_drops_fired_record_with_the_session():
    mon = SequenceMonitor(RATE, log=False)
    for t in (0, 1, 2):
        mon.on_action(ev("x", t))
    mon.reset("s1")
    assert mon.store.peek("s1") is None
    assert not hasattr(mon.evaluator, "_fired")


def test_response_history_is_bounded():
    mon = SequenceMonitor(RATE, log=False, repeat_alerts=True, history_limit=50)
    for t in range(500):
        mon.on_action(ev("x", t * 0.001))
    assert len(mon.responder.history) == 50


def test_idle_rule_window_is_pruned_by_session_prune():
    pol = load_policy(
        "rules:\n"
        "  - {name: a, type: rate, max_calls: 1000, window: 1m, actions: a}\n"
        "  - {name: b, type: rate, max_calls: 1000, window: 1d, actions: b}\n"
    )
    mon = SequenceMonitor(pol, log=False)
    for t in range(100):
        mon.on_action(ev("a", t * 0.1))
    mon.on_action(ev("b", 10_000))
    state = mon.store.peek("s1")
    assert state.memory_footprint() == 101           # "a" window not touched since
    assert state.memory_footprint(now=10_000) == 1   # pruned on demand


def test_terminated_session_state_is_dropped():
    pol = ROOT / "policies" / "examples" / "agt_combined.yaml"
    icpt = FakeAGTInterceptor.from_policy_file(pol, log=False)
    for i in range(20):
        icpt.submit(ev("customer_db.query", i, resource="customer_db.customers",
                       attributes={"records": 400}))
    assert "s1" in icpt.terminated
    assert icpt.monitor.store.peek("s1") is None


# --- M3: escalation re-arms after approval ----------------------------------

SCOPE = load_policy(
    "rules:\n  - {name: drift, type: scope, allowed: [ok], max_outside: 1, "
    "window: 1h, response: escalate}\n"
)


def _interceptor(approver):
    return FakeAGTInterceptor(FakePerCallEngine([]), SequenceMonitor(SCOPE, log=False),
                              approver=approver)


def test_every_breach_after_approval_asks_again():
    asked = []
    icpt = _interceptor(lambda s, v: asked.append(s) or True)
    for i in range(500):
        icpt.submit(ev("q", i, resource=f"t{i}"))
    assert len(asked) == 499          # every call after the tolerance of 1
    assert all(r.approved and not r.auto for r in icpt.escalations)


def test_refused_escalation_ends_the_session():
    icpt = _interceptor(lambda s, v: False)
    outcomes = [icpt.submit(ev("q", i, resource=f"t{i}")) for i in range(10)]
    assert [o.executed for o in outcomes] == [True, True] + [False] * 8


def test_default_approver_is_recorded_as_automatic():
    icpt = _interceptor(None)
    for i in range(3):
        icpt.submit(ev("q", i, resource=f"t{i}"))
    assert [r.auto for r in icpt.escalations] == [True, True]


def test_monitor_approve_rearms_only_the_named_rules():
    mon = SequenceMonitor(SCOPE, log=False)
    mon.on_action(ev("q", 0, resource="t0"))
    first = mon.on_action(ev("q", 1, resource="t1"))
    assert first.approval_required
    assert mon.on_action(ev("q", 2, resource="t2")).clean     # muted until approved
    mon.approve("s1", first.violations)
    assert mon.on_action(ev("q", 3, resource="t3")).approval_required


# --- M4: rule keys --------------------------------------------------------

@pytest.mark.parametrize("value", ["deny", "allow", "block", "audit"])
def test_agt_action_verdict_is_rejected_with_hint(value):
    with pytest.raises(SpecError, match=r"rules\[0\]\.action: .*AGT per-call verdict"):
        load_policy(f"rules:\n  - {{name: r, type: rate, max_calls: 1, window: 1m, "
                    f"action: {value}}}\n")


def test_singular_action_filter_is_rejected():
    with pytest.raises(SpecError, match="use 'actions:'"):
        load_policy("rules:\n  - {name: r, type: rate, max_calls: 1, window: 1m, "
                    "action: messaging.send}\n")


@pytest.mark.parametrize("rule,bad", [
    ("{name: c, type: cumulative, threshold: 1, window: 1m, attrbute: records}", "attrbute"),
    ("{name: s, type: scope, allowed: [x], window: 1m, max_outsde: 10}", "max_outsde"),
    ("{name: r, type: rate, max_calls: 1, window: 1m, priority: 5}", "priority"),
    ("{name: o, type: ordering, before: a, after: b, window: 1m, threshold: 3}",
     "threshold"),
])
def test_unknown_rule_keys_are_rejected(rule, bad):
    with pytest.raises(SpecError, match=rf"rules\[0\]\.{bad}: .*unknown field"):
        load_policy(f"rules:\n  - {rule}\n")


def test_unknown_keys_rejected_in_agt_layout_too():
    with pytest.raises(SpecError, match=r"sequence_rules\[0\]\.foo"):
        load_policy("rules: []\nsequence_rules:\n"
                    "  - {name: r, type: rate, max_calls: 1, window: 1m, foo: bar}\n")


# --- MINOR 1 / 2 / 3: value validation -----------------------------------------

@pytest.mark.parametrize("field,value", [
    ("max_calls", '"5"'), ("max_calls", "2.9"), ("max_calls", "-1"),
    ("max_calls", ".nan"), ("max_calls", ".inf"),
])
def test_bad_rate_numbers_are_rejected(field, value):
    with pytest.raises(SpecError, match=rf"rules\[0\]\.{field}"):
        load_policy(f"rules:\n  - {{name: r, type: rate, {field}: {value}, window: 1m}}\n")


@pytest.mark.parametrize("value", [".nan", "-5", ".inf", '"100"'])
def test_bad_thresholds_are_rejected(value):
    with pytest.raises(SpecError, match=r"rules\[0\]\.threshold"):
        load_policy(f"rules:\n  - {{name: c, type: cumulative, threshold: {value}, "
                    "window: 1m}\n")


@pytest.mark.parametrize("value", [".nan", ".inf"])
def test_non_finite_windows_are_rejected(value):
    with pytest.raises(SpecError, match=r"rules\[0\]\.window"):
        load_policy(f"rules:\n  - {{name: r, type: rate, max_calls: 1, window: {value}}}\n")


def test_integer_valued_threshold_is_still_accepted():
    (rule,) = load_policy("rules:\n  - {name: c, type: cumulative, threshold: 100, "
                          "window: 1m}\n").rules
    assert rule.threshold == 100.0


@pytest.mark.parametrize("kw", [{"magnitude": math.nan}, {"timestamp": math.inf},
                                {"attributes": {"records": math.nan}}])
def test_non_finite_event_values_are_rejected(kw):
    base = {"action": "x", "agent_id": "a", "session_id": "s", "timestamp": 0.0}
    with pytest.raises(ValueError, match="finite"):
        ToolCallEvent(**{**base, **kw})


def test_ordering_with_same_before_and_after_is_rejected():
    with pytest.raises(SpecError, match=r"rules\[0\]\.after: .*can never fire"):
        load_policy("rules:\n  - {name: o, type: ordering, before: a, after: a, "
                    "window: 1m}\n")


# --- MINOR 7 / 9 ------------------------------------------------------------

def test_ordering_violation_describes_a_gap_not_a_threshold():
    e = IncrementalEvaluator(ORDER)
    e.on_action(ev("a", 0))
    (v,) = e.on_action(ev("b", 3))
    assert v.kind == "ordering"
    assert "3s after the enabling one" in v.describe()
    assert "threshold" not in v.describe()


def test_fake_per_call_engine_fails_closed_on_type_error():
    engine = FakePerCallEngine([{"name": "big", "condition": {
        "field": "records", "operator": "gt", "value": "1000"}, "action": "deny"}])
    verdict = engine.check(ev("q", 0, attributes={"records": 5}))
    assert not verdict.allowed and verdict.action == "deny"
    assert "evaluation error" in verdict.reason


# --- R2-M1: the idle sweep must not couple sessions ---------------------------

def _cross_session_probe(evaluator):
    """A adds 90, B sends one far-future stamp, A adds 90 again (180 > 100)."""
    assert evaluator.on_action(ev("x", 0, magnitude=90, session="A")) == []
    evaluator.on_action(ev("x", 1e12, magnitude=0, session="B"))
    return evaluator.on_action(ev("x", 2, magnitude=90, session="A"))


def test_future_stamp_in_another_session_does_not_change_verdict():
    (v,) = _cross_session_probe(IncrementalEvaluator(CUM))
    assert v.session_id == "A" and v.observed == 180


def test_future_stamp_in_another_session_with_clock_does_not_change_verdict():
    e = IncrementalEvaluator(CUM, clock=lambda: 2.0, max_clock_skew=60)
    (v,) = _cross_session_probe(e)
    assert v.observed == 180


def test_independent_time_bases_do_not_interfere():
    e = IncrementalEvaluator(CUM)
    e.on_action(ev("x", 0, magnitude=60, session="A"))
    e.on_action(ev("x", 1.7e9, session="B"))
    assert [v.session_id for v in e.on_action(ev("x", 10, magnitude=60, session="A"))] == ["A"]


def test_clock_sweep_ignores_other_sessions_stamps():
    """Review probe 4: a stamp clamped to clock+skew must not drop a victim."""
    now = [0.0]
    e = IncrementalEvaluator(CUM, clock=lambda: now[0], max_clock_skew=60)
    e.on_action(ev("x", 0, magnitude=90, session="victim"))
    now[0] = 3_570.0    # victim's event still has 30 s left in its 1 h window
    e.on_action(ev("x", 1e12, session="other"))   # clamped to 3,630
    assert e.store.peek("victim") is not None
    (v,) = e.on_action(ev("x", 3_570, magnitude=90, session="victim"))
    assert v.observed == 180


def test_future_stamp_does_not_disable_clock_driven_expiry():
    now = [0.0]
    e = IncrementalEvaluator(CUM, clock=lambda: now[0], max_clock_skew=60)
    e.on_action(ev("x", 1e12, session="B"))
    for i in range(5_000):
        now[0] = i * 10.0
        e.on_action(ev("x", now[0], session=f"s{i}"))
    # ttl 3,600 + skew 60 + sweep slack 225 s at 10 s per session.
    assert len(e.store) <= (3_600 + 60 + 3_600 // 16) // 10 + 2
    assert e.store.expired >= 5_000 - len(e.store)


def test_default_does_not_sweep_on_event_time():
    e = IncrementalEvaluator(CUM)
    for i in range(100):
        e.on_action(ev("x", i * 10_000, session=f"s{i}"))
    assert len(e.store) == 100 and e.store.expired == 0


def test_shared_time_base_keeps_the_documented_coupling():
    """Opt-in event-time sweeping: one bad stamp affects every session."""
    assert _cross_session_probe(IncrementalEvaluator(CUM, shared_time_base=True)) == []


# --- R2-m1: unsafe knob values are rejected ------------------------------------

@pytest.mark.parametrize("skew", [-5.0, math.nan])
def test_negative_or_nan_clock_skew_is_rejected(skew):
    with pytest.raises(ValueError, match="max_clock_skew"):
        IncrementalEvaluator(CUM, clock=lambda: 0.0, max_clock_skew=skew)


def test_idle_ttl_shorter_than_longest_window_is_rejected():
    with pytest.raises(ValueError, match="idle_ttl"):
        IncrementalEvaluator(CUM, idle_ttl=1)
    with pytest.raises(ValueError, match="idle_ttl"):
        SequenceMonitor(CUM, log=False, idle_ttl=1)


@pytest.mark.parametrize("ttl", [0, -1, 3_600, 7_200])
def test_idle_ttl_zero_or_at_least_longest_window_is_accepted(ttl):
    IncrementalEvaluator(CUM, idle_ttl=ttl)


# --- R2-m2: top-level typos in the standalone layout ---------------------------

def test_unknown_top_level_key_rejected_in_standalone_layout():
    with pytest.raises(SpecError, match="foo: unknown top-level field"):
        load_policy("rules: []\nfoo: 1\n")
    with pytest.raises(SpecError, match="rule: unknown top-level field"):
        load_policy("rule:\n  - {name: r, type: rate, max_calls: 2, window: 10s}\n")


def test_unknown_top_level_key_allowed_in_agt_layout():
    # AGT owns the rest of the file (rules, defaults, ...).
    load_policy("sequence_rules: []\nrules: []\ndefaults: {action: allow}\nfoo: 1\n")
