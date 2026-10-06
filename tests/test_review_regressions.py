"""One regression test (or more) per BLOCKER / MAJOR finding in REVIEW.md.

``tests/test_review_fixes.py`` (written with the fixes) already covers most
of these against ``bench/naive.py``. These tests are independent: they use
the tests-only reference in ``naive_reference.py`` and probe each finding as
REVIEW.md describes it. Round 3 MINORs that are still open are marked
xfail(strict=True) and logged in BUGS.md.

Not testable as code: M5 (work uncommitted) is a process item; R2-M2 is
covered only to the extent that the report must state where its code is.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from naive_reference import NaiveReference

from seqmon import (
    IncrementalEvaluator,
    SequenceMonitor,
    SpecError,
    ToolCallEvent,
    load_policy,
    parse_policy,
)
from seqmon.adapters import FakeAGTInterceptor, FakePerCallEngine

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "progress_report_1.md"

RATE = parse_policy({"rules": [
    {"name": "r", "type": "rate", "max_calls": 2, "window": "10s"}]})
CUM = parse_policy({"rules": [
    {"name": "c", "type": "cumulative", "threshold": 100, "window": "1h",
     "response": "terminate"}]})
ORDER = parse_policy({"rules": [
    {"name": "o", "type": "ordering", "before": "a", "after": "b", "window": "1h"}]})
SCOPE_ESC = parse_policy({"rules": [
    {"name": "s", "type": "scope", "allowed": ["ok"], "max_outside": 1, "window": "1h",
     "response": "escalate"}]})


def ev(t, m=0.0, *, s="A", action="x", resource=None):
    return ToolCallEvent(action=action, agent_id="a", session_id=s, timestamp=float(t),
                         magnitude=m, resource=resource)


def rules_fired(evaluator, events):
    return [(i, v.rule) for i, e in enumerate(events) for v in evaluator.on_action(e)]


def ref_fired(policy, events):
    return [(a.index, a.rule) for a in NaiveReference(policy).run(events)]


# --- B1: the progress report exists -------------------------------------------------

def test_B1_progress_report_exists_and_is_substantive():
    text = REPORT.read_text(encoding="utf-8")
    assert len(text.splitlines()) > 100
    assert "Progress Report" in text


def test_R2_M2_report_states_where_its_code_is():
    head = "\n".join(REPORT.read_text(encoding="utf-8").splitlines()[:15])
    assert "progress-report" in head and "agent-sequence-monitor" in head


# --- M1: out-of-order and future timestamps -------------------------------------------

def test_M1_late_event_does_not_produce_a_stale_window_false_positive():
    """REVIEW probe: rate 2/10s, events at t=100, 0, 105. With clamping the
    t=0 event is treated as t=100, so 3 events lie in (95, 105]; incremental
    and reference must agree."""
    events = [ev(100), ev(0), ev(105)]
    assert rules_fired(IncrementalEvaluator(RATE), events) == ref_fired(RATE, events)


def test_M1_ordering_gap_is_never_negative():
    e = IncrementalEvaluator(ORDER)
    e.on_action(ev(100, action="a"))
    [v] = e.on_action(ev(50, action="b"))
    assert v.observed == 0.0


def test_M1_far_future_stamp_is_clamped_with_a_clock():
    e = IncrementalEvaluator(CUM, clock=lambda: 10.0, max_clock_skew=60)
    e.on_action(ev(0, 90))
    e.on_action(ev(1e12, 0))        # clamped to 70
    assert e.on_action(ev(2, 90))   # 180 > 100 still detected
    assert e.store.peek("A").clamped_events >= 1


# --- M2: memory across sessions -----------------------------------------------------------

def test_M2_footprint_query_does_not_create_session():
    mon = SequenceMonitor(CUM, log=False)
    assert mon.footprint("ghost") == 0 and len(mon.store) == 0


def test_M2_terminated_session_state_is_dropped_by_interceptor():
    mon = SequenceMonitor(CUM, log=False)
    icpt = FakeAGTInterceptor(FakePerCallEngine.from_mapping({"rules": []}), mon)
    for t in range(3):
        icpt.submit(ev(t, 60, s="S0"))
    assert "S0" in icpt.terminated
    assert mon.store.peek("S0") is None
    assert not icpt.submit(ev(10, 1, s="S0")).executed


def test_M2_history_is_capped():
    mon = SequenceMonitor(RATE, log=False, repeat_alerts=True)
    for t in range(5000):
        mon.on_action(ev(t * 0.001))
    assert len(mon.responder.history) == 1000


# --- M3: approved escalation re-arms --------------------------------------------------------

def test_M3_every_breach_after_approval_asks_again():
    asks = []
    mon = SequenceMonitor(SCOPE_ESC, log=False)
    icpt = FakeAGTInterceptor(FakePerCallEngine.from_mapping({"rules": []}), mon,
                              approver=lambda sid, vs: asks.append(sid) or True)
    for t in range(50):
        icpt.submit(ev(t, resource=f"other-{t}"))
    # max_outside 1: calls 2..50 each breach, and each is asked about.
    assert len(asks) == 49


def test_M3_refused_escalation_ends_session():
    mon = SequenceMonitor(SCOPE_ESC, log=False)
    icpt = FakeAGTInterceptor(FakePerCallEngine.from_mapping({"rules": []}), mon,
                              approver=lambda sid, vs: False)
    outcomes = [icpt.submit(ev(t, resource=f"o{t}")) for t in range(5)]
    assert [o.executed for o in outcomes] == [True, True, False, False, False]


# --- M4: AGT `action:` verdict key / unknown keys -----------------------------------------------

@pytest.mark.parametrize("verdict", ["deny", "allow", "block", "audit"])
def test_M4_agt_verdict_in_sequence_rule_is_rejected(verdict):
    with pytest.raises(SpecError, match=r"sequence_rules\[0\]\.action: .*response"):
        parse_policy({"sequence_rules": [
            {"name": "r", "type": "rate", "max_calls": 1, "window": "1m", "action": verdict}]})


@pytest.mark.parametrize("typo", ["attrbute", "max_outsde", "priority", "foo"])
def test_M4_unknown_rule_keys_are_rejected(typo):
    with pytest.raises(SpecError, match=rf"rules\[0\]\.{typo}:"):
        parse_policy({"rules": [{"name": "r", "type": "scope", "allowed": ["a"],
                                 "window": "1m", typo: 1}]})


# --- R2-M1: cross-session coupling via the idle sweep ------------------------------------------

@pytest.mark.parametrize("kw", [{}, {"clock": lambda: 5.0}])
def test_R2_M1_future_stamp_in_other_session_does_not_change_verdict(kw):
    e = IncrementalEvaluator(CUM, **kw)
    e.on_action(ev(0, 90, s="A"))
    e.on_action(ev(1e12, 0, s="B"))
    assert e.on_action(ev(2, 90, s="A"))


def test_R2_M1_independent_time_bases_do_not_interfere():
    e = IncrementalEvaluator(CUM)
    e.on_action(ev(0, 60, s="A"))
    e.on_action(ev(1.7e9, 0, s="B"))
    assert e.on_action(ev(10, 60, s="A"))


# --- Round 3 MINORs still open ---------------------------------------------

@pytest.mark.xfail(strict=True, reason="BUG-007 (REVIEW R3-m1): with a clock, a stamp lagging "
                                       "the clock by > max_clock_skew lets the sweep drop a "
                                       "live session; breach missed, clamped_events stays 0")
def test_R3_m1_lagging_stamps_do_not_drop_live_session():
    now = [600.0]
    e = IncrementalEvaluator(CUM, clock=lambda: now[0], max_clock_skew=60)
    e.on_action(ev(0, 60, s="A"))
    for i in range(1, 336):              # other sessions keep the sweep running
        now[0] = 600 + 10 * i
        e.on_action(ev(10 * i, 0, s=f"other{i}"))
    now[0] = 600 + 3350
    assert e.on_action(ev(3350, 60, s="A"))   # 60 + 60 within one hour


@pytest.mark.xfail(strict=True, reason="BUG-008 (REVIEW R3-m2): idle_ttl=NaN is accepted and "
                                       "silently disables expiry")
def test_R3_m2_nan_idle_ttl_is_rejected():
    with pytest.raises(ValueError):
        IncrementalEvaluator(CUM, idle_ttl=float("nan"))


def test_example_policy_still_loads():
    assert load_policy(ROOT / "policies" / "example.yaml").rules
