"""Tests for invalid-policy handling and response severity/logging.

These cover paths the scenario tests never reach: every malformed
document must surface as ``SpecError`` (not a stray ``TypeError`` or
``ValueError``), and ``Decision.severity`` / the audit logger must report
the most severe response among simultaneous violations.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from seqmon import (
    Response,
    ResponseHandler,
    SequenceMonitor,
    SpecError,
    ToolCallEvent,
    load_policy,
)
from seqmon.response import Decision

POLICY = Path(__file__).resolve().parents[1] / "policies" / "example.yaml"


def ev(action, t, *, magnitude=0.0, resource=None, session="s1"):
    return ToolCallEvent(
        action=action,
        agent_id="agent-1",
        session_id=session,
        timestamp=float(t),
        magnitude=magnitude,
        resource=resource,
    )


# --- invalid policies -----------------------------------------------------

@pytest.mark.parametrize(
    "doc, match",
    [
        ("- just\n- a list\n", "must be a YAML mapping"),
        ("rules: {not: a list}", "'rules' must be a list"),
        ("rules: [42]", "index 0 must be a mapping"),
        ("rules: [{name: r, window: 1m}]", "missing required field 'type'"),
        ("rules: [{name: r, type: rate, max_calls: 1}]",
         "missing required field 'window'"),
        ("rules: [{name: r, type: rate, window: 1m}]",
         "missing required field 'max_calls'"),
        ("rules: [{name: r, type: cumulative, window: 1m}]",
         "missing required field 'threshold'"),
        ("rules: [{name: r, type: ordering, before: a, window: 1m}]",
         "missing required field 'after'"),
        ("rules: [{name: r, type: scope, window: 1m}]",
         "missing required field 'allowed'"),
        ("rules: [{name: r, type: rate, max_calls: 1, window: 1m, response: kill}]",
         "unknown response 'kill'"),
        ("rules: [{name: r, type: cumulative, threshold: 1, window: 1m, "
         "aggregate: median}]", "unknown aggregate 'median'"),
        ("rules: [{name: r, type: rate, max_calls: 1, window: soon}]",
         "bad duration"),
    ],
)
def test_malformed_policy_raises_spec_error(doc, match):
    with pytest.raises(SpecError, match=match):
        load_policy(doc)


@pytest.mark.parametrize(
    "doc, match",
    [
        # Before the fix these escaped as ValueError / TypeError.
        ("rules: [{name: r, type: rate, max_calls: lots, window: 1m}]",
         "'max_calls' must be a number"),
        ("rules: [{name: r, type: cumulative, threshold: [1], window: 1m}]",
         "'threshold' must be a number"),
        ("rules: [{name: r, type: scope, allowed: [a], max_outside: x, "
         "window: 1m}]", "'max_outside' must be a number"),
        ("rules: [{name: r, type: scope, allowed: 5, window: 1m}]",
         "'allowed' must be a string or a list"),
        ("rules: [{name: r, type: rate, max_calls: 1, window: 1m, actions: 7}]",
         "'actions' must be a string or a list"),
        # bool is an int subclass; str() would turn null into "None".
        ("rules: [{name: r, type: rate, max_calls: true, window: 1m}]",
         "'max_calls' must be a number"),
        ("rules: [{name: r, type: scope, allowed: [a, null], window: 1m}]",
         "'allowed' must contain only strings"),
        ("rules: [{name: r, type: rate, max_calls: 1, window: 1m, actions: [3]}]",
         "'actions' must contain only strings"),
    ],
)
def test_bad_field_types_raise_spec_error_not_bare_errors(doc, match):
    with pytest.raises(SpecError, match=match):
        load_policy(doc)


def test_missing_policy_file_is_reported_as_missing():
    with pytest.raises(SpecError, match="not found"):
        load_policy("policies/does-not-exist.yaml")


def test_scalar_actions_and_allowed_are_accepted():
    policy = load_policy(
        "rules:\n"
        "  - {name: s, type: scope, actions: files.read, allowed: files/ok, "
        "window: 1m}\n"
        "  - {name: r, type: rate, action: messaging.send, max_calls: 2, "
        "window: 1m}\n"
    )
    scope, rate = policy.rules
    assert scope.actions == ("files.read",)
    assert scope.allowed == ("files/ok",)
    assert scope.max_outside == 0
    assert rate.actions == ("messaging.send",)


def test_unnamed_rule_gets_positional_name_and_default_warn():
    policy = load_policy("rules: [{type: rate, max_calls: 1, window: 1m}]")
    assert policy.rules[0].name == "rule_0"
    assert policy.rules[0].response is Response.WARN


# --- Decision.severity ----------------------------------------------------

def test_clean_decision_has_no_severity():
    decision = Decision()
    assert decision.clean
    assert decision.severity is None


WARN_POLICY = """
rules:
  - {name: w, type: rate, actions: [a], max_calls: 1, window: 1m, response: warn}
  - {name: p, type: rate, actions: [a], max_calls: 2, window: 1m, response: pause}
  - {name: b, type: rate, actions: [a], max_calls: 3, window: 1m, response: break}
"""


def test_severity_escalates_warn_pause_break():
    mon = SequenceMonitor(load_policy(WARN_POLICY), log=False)
    severities = [mon.observe(ev("a", i)).severity for i in range(4)]
    assert severities == [None, Response.WARN, Response.PAUSE, Response.BREAK]


def test_severity_reports_most_severe_of_simultaneous_violations():
    doc = """
rules:
  - {name: w, type: rate, actions: [a], max_calls: 1, window: 1m, response: warn}
  - {name: b, type: rate, actions: [a], max_calls: 1, window: 1m, response: break}
"""
    mon = SequenceMonitor(load_policy(doc), log=False)
    mon.observe(ev("a", 0))
    decision = mon.observe(ev("a", 1))
    assert {v.rule for v in decision.violations} == {"w", "b"}
    assert decision.terminate
    assert decision.severity is Response.BREAK


# --- audit logging --------------------------------------------------------

def test_handler_logs_each_violation_at_its_severity(caplog):
    mon = SequenceMonitor(load_policy(WARN_POLICY), log=True)
    with caplog.at_level(logging.DEBUG, logger="seqmon"):
        for i in range(4):
            mon.observe(ev("a", i))
    levels = [r.levelno for r in caplog.records]
    assert levels == [logging.WARNING, logging.ERROR, logging.CRITICAL]
    assert "after 2 events" in caplog.records[0].getMessage()
    assert len(mon.responder.history) == 3


def test_handler_with_logging_disabled_is_silent(caplog):
    mon = SequenceMonitor(load_policy(WARN_POLICY), log=False)
    with caplog.at_level(logging.DEBUG, logger="seqmon"):
        for i in range(4):
            mon.observe(ev("a", i))
    assert caplog.records == []
    # History is the audit record and is kept regardless of logging.
    assert len(mon.responder.history) == 3


def test_handler_empty_violation_list_is_clean():
    decision = ResponseHandler(log=True).handle([])
    assert decision.clean and not decision.terminate


def test_long_multiline_policy_string_is_parsed_not_probed_as_path():
    # Regression: Path(doc).exists() raised OSError (name too long) for
    # YAML strings past the OS filename limit.
    doc = "rules:\n" + "".join(
        f"  - {{name: r{i}, type: rate, max_calls: {i + 1}, window: 1m}}\n"
        for i in range(20)
    )
    assert len(doc) > 255
    assert len(load_policy(doc).rules) == 20


def test_policy_path_object_is_loaded():
    assert len(load_policy(Path(POLICY)).rules) == 6
