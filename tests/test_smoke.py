"""Smoke tests: the demo runs, the example policies parse, and the
checkpoint-2 interfaces (AGT layout, severity names, hooks, config cap,
field paths in errors) are wired up. Not a full suite."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from seqmon import (
    IncrementalEvaluator,
    LoggingHooks,
    Response,
    SequenceMonitor,
    SpecError,
    ToolCallEvent,
    load_policy,
)
from seqmon.adapters import CallFeed, FakeAGTInterceptor

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "policies" / "examples").glob("*.yaml"))
DEMO = ROOT / "scripts" / "demo_trace.py"


def test_there_are_example_policies():
    assert len(EXAMPLES) >= 4


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_policy_parses(path):
    policy = load_policy(path)
    assert policy.layout == "agt"
    assert policy.rules


def test_demo_script_runs_and_reports_the_step():
    out = subprocess.run(
        [sys.executable, str(DEMO)], capture_output=True, text=True, check=True, cwd="/"
    ).stdout
    assert "Sequence violation fired at step 17: bulk-customer-read [TERMINATE]" in out


def test_demo_every_evaluated_call_passes_per_call_policy():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import demo_trace
    finally:
        sys.path.remove(str(ROOT / "scripts"))
    outcomes = demo_trace.run(verbose=False)
    assert all(o.per_call is None or o.per_call.allowed for o in outcomes)
    fired = [i for i, o in enumerate(outcomes, 1) if o.decision and o.decision.terminate]
    assert fired == [17]
    assert not outcomes[-1].executed  # the external send never runs


def test_fake_interceptor_is_a_call_feed_and_denies_per_call():
    feed = FakeAGTInterceptor.from_policy_file(EXAMPLES[0], log=False)
    assert isinstance(feed, CallFeed)
    big = ToolCallEvent("customer_db.query", "a", "s", 1.0, attributes={"records": 5000})
    outcome = feed.submit(big)
    assert outcome.per_call is not None and not outcome.per_call.allowed
    assert outcome.decision is None  # denied calls never reach the monitor


def test_severity_names_alias_the_agt_verbs():
    assert Response.LOG is Response.WARN
    assert Response("escalate") is Response.PAUSE
    assert Response("terminate").severity == "TERMINATE"


def test_errors_carry_the_field_path():
    doc = (
        "sequence_rules:\n"
        "  - {name: a, type: rate, max_calls: 1, window: 1m}\n"
        "  - {name: b, type: rate, max_calls: 1, window: 1m}\n"
        "  - {name: c, type: rate, max_calls: 1, window: soon}\n"
    )
    with pytest.raises(SpecError, match=r"^sequence_rules\[2\]\.window: "):
        load_policy(doc)


def test_agt_rule_in_standalone_layout_gets_a_hint():
    doc = "rules:\n  - {name: x, condition: {field: f, operator: eq, value: 1}, action: deny}\n"
    with pytest.raises(SpecError, match="sequence_rules"):
        load_policy(doc)


def test_window_cap_comes_from_policy_unless_overridden():
    budget = ROOT / "policies" / "examples" / "budget.yaml"
    assert SequenceMonitor.from_file(budget, log=False).store.max_events_per_window == 20000
    mon = SequenceMonitor.from_file(budget, log=False, max_events_per_window=5)
    assert mon.store.max_events_per_window == 5


def test_on_action_and_hooks():
    policy = load_policy(
        "sequence_rules:\n"
        "  - {name: r, type: rate, max_calls: 1, window: 1m, response: escalate}\n"
    )
    assert IncrementalEvaluator(policy).on_action(ToolCallEvent("t", "a", "s", 0.0)) == []
    hooks = LoggingHooks()
    mon = SequenceMonitor(policy, log=False, escalation_hook=hooks, termination_hook=hooks)
    mon.on_action(ToolCallEvent("t", "a", "s", 0.0))
    assert mon.on_action(ToolCallEvent("t", "a", "s", 1.0)).approval_required
    assert hooks.escalated == ["s"] and hooks.terminated == []


def test_sum_over_named_attribute():
    policy = load_policy(
        "sequence_rules:\n"
        "  - {name: spend, type: cumulative, attribute: cost_usd, threshold: 1.0,"
        " window: 1h, response: log}\n"
    )
    mon = SequenceMonitor(policy, log=False)
    for t in range(3):
        d = mon.on_action(ToolCallEvent("llm", "a", "s", float(t), attributes={"cost_usd": 0.4}))
    assert [v.rule for v in d.violations] == ["spend"]
