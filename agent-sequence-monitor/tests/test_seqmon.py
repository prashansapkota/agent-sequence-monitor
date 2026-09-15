"""Tests for the sequence monitoring layer."""

from __future__ import annotations

import pytest

from seqmon import (
    Response,
    SequenceMonitor,
    SpecError,
    ToolCallEvent,
    load_policy,
)
from seqmon.spec import parse_duration

POLICY = "policies/example.yaml"


def ev(action, t, *, magnitude=0.0, resource=None, session="s1"):
    return ToolCallEvent(
        action=action,
        agent_id="agent-1",
        session_id=session,
        timestamp=float(t),
        magnitude=magnitude,
        resource=resource,
    )


# --- spec -----------------------------------------------------------------

def test_parse_duration_units():
    assert parse_duration("30s") == 30
    assert parse_duration("5m") == 300
    assert parse_duration("2h") == 7200
    assert parse_duration("1d") == 86400
    assert parse_duration(45) == 45


@pytest.mark.parametrize("bad", ["", "5x", "-1m", "0s", "abc"])
def test_parse_duration_rejects_bad(bad):
    with pytest.raises(SpecError):
        parse_duration(bad)


def test_example_policy_loads_all_four_types():
    policy = load_policy(POLICY)
    kinds = {type(r).__name__ for r in policy.rules}
    assert kinds == {
        "CumulativeConstraint",
        "RateConstraint",
        "OrderingConstraint",
        "ScopeConstraint",
    }


def test_unknown_type_rejected():
    with pytest.raises(SpecError, match="unknown type"):
        load_policy("rules: [{name: r, type: nonsense, window: 1m}]")


def test_duplicate_rule_name_rejected():
    doc = """
rules:
  - {name: dup, type: rate, max_calls: 1, window: 1m}
  - {name: dup, type: rate, max_calls: 2, window: 1m}
"""
    with pytest.raises(SpecError, match="duplicate"):
        load_policy(doc)


# --- cumulative -----------------------------------------------------------

def test_cumulative_sum_fires_only_past_threshold():
    mon = SequenceMonitor.from_file(POLICY, log=False)
    # 4 x 1000 rows = 4000, under the 5000 threshold.
    for i in range(4):
        d = mon.observe(ev("customer_db.query", i, magnitude=1000,
                           resource="customer_db.orders"))
        assert d.clean
    # 5th tips it to 5000... still not > 5000.
    assert mon.observe(
        ev("customer_db.query", 4, magnitude=1000, resource="customer_db.orders")
    ).clean
    d = mon.observe(
        ev("customer_db.query", 5, magnitude=1, resource="customer_db.orders")
    )
    assert d.terminate
    assert d.violations[0].rule == "bulk-customer-read"


def test_cumulative_window_expiry_releases_pressure():
    mon = SequenceMonitor.from_file(POLICY, log=False)
    mon.observe(ev("customer_db.query", 0, magnitude=4999,
                   resource="customer_db.orders"))
    # Two hours later the earlier read has left the 1h window.
    d = mon.observe(ev("customer_db.query", 7200, magnitude=4999,
                       resource="customer_db.orders"))
    assert d.clean


def test_distinct_resource_aggregate():
    mon = SequenceMonitor.from_file(POLICY, log=False)
    decision = None
    for i in range(9):
        decision = mon.observe(
            ev("customer_db.query", i, magnitude=1, resource=f"table_{i}")
        )
    assert decision.approval_required
    assert any(v.rule == "distinct-table-sprawl" for v in decision.violations)


def test_budget_drain_accumulates_small_charges():
    mon = SequenceMonitor.from_file(POLICY, log=False)
    decision = None
    for i in range(101):
        decision = mon.observe(ev("billing.charge", i, magnitude=0.50))
    assert decision.approval_required


# --- rate -----------------------------------------------------------------

def test_rate_limit_fires():
    mon = SequenceMonitor.from_file(POLICY, log=False)
    decision = None
    for i in range(31):
        decision = mon.observe(ev("messaging.send", i))
    assert decision.terminate
    assert decision.violations[0].rule == "message-flood"


def test_rate_spread_out_is_clean():
    mon = SequenceMonitor.from_file(POLICY, log=False)
    # One call every 60s never fills a 5m window beyond 5.
    for i in range(50):
        assert mon.observe(ev("messaging.send", i * 60)).clean


# --- ordering -------------------------------------------------------------

def test_ordering_violation_detected():
    mon = SequenceMonitor.from_file(POLICY, log=False)
    assert mon.observe(ev("customer_db.query", 0, magnitude=1,
                          resource="customer_db.orders")).clean
    d = mon.observe(ev("messaging.send_external", 60))
    assert d.terminate
    assert d.violations[0].rule == "read-then-exfiltrate"


def test_ordering_reverse_order_is_clean():
    mon = SequenceMonitor.from_file(POLICY, log=False)
    assert mon.observe(ev("messaging.send_external", 0)).clean
    assert mon.observe(ev("customer_db.query", 60, magnitude=1,
                          resource="customer_db.orders")).clean


def test_ordering_outside_window_is_clean():
    mon = SequenceMonitor.from_file(POLICY, log=False)
    mon.observe(ev("customer_db.query", 0, magnitude=1,
                   resource="customer_db.orders"))
    # 20 minutes later, outside the 10m correlation window.
    assert mon.observe(ev("messaging.send_external", 1200)).clean


# --- scope ----------------------------------------------------------------

def test_scope_drift_tolerates_then_fires():
    mon = SequenceMonitor.from_file(POLICY, log=False)
    for i in range(3):
        assert mon.observe(
            ev("files.read", i, resource=f"secrets/key_{i}")
        ).clean
    d = mon.observe(ev("files.read", 3, resource="secrets/key_3"))
    assert d.approval_required
    assert d.violations[0].rule == "resource-scope-drift"


def test_in_scope_resources_never_fire():
    mon = SequenceMonitor.from_file(POLICY, log=False)
    for i in range(50):
        assert mon.observe(ev("files.read", i, resource="files/reports")).clean


# --- structural properties ------------------------------------------------

def test_memory_stays_bounded_over_long_session():
    """The core overhead claim: footprint plateaus, not grows."""
    mon = SequenceMonitor.from_file(POLICY, log=False, repeat_alerts=True)
    # One benign in-scope call per minute for 24 simulated hours.
    for i in range(1440):
        mon.observe(ev("files.read", i * 60, resource="files/reports"))
    footprint = mon.footprint("s1")
    assert mon.store.get("s1").total_events == 1440
    # Windows top out at 1h, so far fewer than 1440 events are retained.
    assert footprint < 200, f"footprint grew to {footprint}"


def test_sessions_are_isolated():
    mon = SequenceMonitor.from_file(POLICY, log=False)
    for i in range(20):
        mon.observe(ev("messaging.send", i, session="a"))
    for i in range(20):
        assert mon.observe(ev("messaging.send", i, session="b")).clean


def test_alert_fires_once_per_session_by_default():
    mon = SequenceMonitor.from_file(POLICY, log=False)
    fired = 0
    for i in range(60):
        if mon.observe(ev("messaging.send", i)).violations:
            fired += 1
    assert fired == 1


def test_violation_records_detection_latency():
    mon = SequenceMonitor.from_file(POLICY, log=False)
    decision = None
    for i in range(31):
        decision = mon.observe(ev("messaging.send", i))
    # 31 events observed before the rate rule could possibly know.
    assert decision.violations[0].events_elapsed == 31


def test_replay_stops_at_termination():
    mon = SequenceMonitor.from_file(POLICY, log=False)
    events = [ev("messaging.send", i) for i in range(100)]
    decisions = mon.replay(events)
    assert len(decisions) == 31
    assert decisions[-1].terminate


def test_distinct_count_is_incremental_and_correct():
    """distinct_resources must track evictions, not just insertions."""
    from seqmon.state import _Window

    win = _Window(span=100.0, max_events=1000)
    win.add(ev("q", 0, resource="a"))
    win.add(ev("q", 1, resource="b"))
    win.add(ev("q", 2, resource="a"))   # duplicate, still 2 distinct
    assert win.distinct_resources() == 2

    # At t=101 the cutoff is 1: the t=0 "a" and the t=1 "b" both expire.
    # The "a" at t=2 survives, so its key must stay -- this is the case a
    # naive "delete on evict" would get wrong, dropping "a" entirely.
    win.add(ev("q", 101, resource="c"))
    assert win.distinct_resources() == 2   # a (t=2) and c

    # At t=103 the cutoff is 3: both "a" and "b" are gone, "c" remains.
    win.add(ev("q", 103, resource="c"))
    assert win.distinct_resources() == 1


def test_per_event_cost_does_not_grow_with_session_length():
    """Marginal cost must stay flat -- no aggregate may rescan the window."""
    import time

    mon = SequenceMonitor.from_file(POLICY, log=False, repeat_alerts=True)

    def marginal_us(count: int, start: int) -> float:
        t0 = time.perf_counter()
        for i in range(start, start + count):
            mon.observe(ev("customer_db.query", i, magnitude=1.0,
                           resource=f"t{i % 50}"))
        return (time.perf_counter() - t0) / count * 1e6

    marginal_us(5000, 0)               # warm past the window fill
    early = marginal_us(5000, 5000)
    late = marginal_us(5000, 40000)
    # Generous bound: flags O(n) regressions without being flaky on CI.
    assert late < early * 3, f"cost grew {early:.1f}us -> {late:.1f}us"


# --- scenario traces ------------------------------------------------------
# These pin the benign/adversarial pair together. A policy change that
# silences the attack, or that starts alerting on ordinary work, breaks here.

def _load_scenario(module_path: str):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / module_path
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_adversarial_trace_is_detected_and_terminated():
    scenario = _load_scenario("bench/adversarial/slow_exfiltration.py")
    mon = SequenceMonitor.from_file(POLICY, log=False)

    fired, terminated_at = [], None
    for i, (event, verdict) in enumerate(scenario.build_trace(), start=1):
        assert verdict == "allow", "every call must pass the per-call engine"
        decision = mon.observe(event)
        fired.extend(v.rule for v in decision.violations)
        if decision.terminate:
            terminated_at = i
            break

    assert "resource-scope-drift" in fired
    assert "bulk-customer-read" in fired
    assert terminated_at is not None, "attack must be stopped"
    # Detection is not instant, but must not be arbitrarily late either.
    assert 10 <= terminated_at <= 25


def test_benign_trace_produces_no_alerts():
    scenario = _load_scenario("bench/benign/legitimate_reporting.py")
    mon = SequenceMonitor.from_file(POLICY, log=False)

    alerts = [v for e in scenario.build_trace() for v in mon.observe(e).violations]
    assert alerts == [], f"false positives: {[a.rule for a in alerts]}"


def test_benign_trace_is_the_harder_case():
    """The control must not be trivially benign -- it should out-volume
    the attack, so a no-alert result is meaningful rather than vacuous."""
    adversarial = _load_scenario("bench/adversarial/slow_exfiltration.py")
    benign = _load_scenario("bench/benign/legitimate_reporting.py")

    attack_rows = sum(
        e.magnitude for e, _ in adversarial.build_trace()
        if e.action == "customer_db.query"
    )
    benign_rows = sum(
        e.magnitude for e in benign.build_trace()
        if e.action == "customer_db.query"
    )
    assert benign_rows > attack_rows
