"""Severity -> Response mapping, Decision aggregation, and hook invocation."""

from __future__ import annotations

import logging

import pytest

from seqmon import (
    Decision,
    EscalationHook,
    LoggingHooks,
    NoOpHooks,
    Response,
    ResponseHandler,
    SequenceMonitor,
    TerminationHook,
    ToolCallEvent,
    parse_policy,
)


class Recorder:
    def __init__(self):
        self.escalated = []
        self.terminated = []

    def escalate(self, session_id, violations):
        self.escalated.append((session_id, [v.rule for v in violations]))

    def terminate(self, session_id, violations):
        self.terminated.append((session_id, [v.rule for v in violations]))


def rule(name, response, max_calls=0):
    return {"name": name, "type": "rate", "max_calls": max_calls, "window": "1m",
            "response": response}


def ev(t=0, session="s1"):
    return ToolCallEvent(action="x", agent_id="a", session_id=session, timestamp=float(t))


def monitor(*rules, **kw):
    rec = Recorder()
    mon = SequenceMonitor(parse_policy({"rules": list(rules)}), log=False,
                          escalation_hook=rec, termination_hook=rec, **kw)
    return mon, rec


# --- mapping ----------------------------------------------------------------------

@pytest.mark.parametrize("member, value, severity", [
    (Response.LOG, "warn", "LOG"),
    (Response.ESCALATE, "pause", "ESCALATE"),
    (Response.TERMINATE, "break", "TERMINATE"),
])
def test_severity_names_alias_verbs(member, value, severity):
    assert member.value == value
    assert member.severity == severity
    assert Response(value) is member
    assert Response(severity.lower()) is member


def test_alias_identity():
    assert Response.LOG is Response.WARN
    assert Response.ESCALATE is Response.PAUSE
    assert Response.TERMINATE is Response.BREAK
    assert len(list(Response)) == 3


@pytest.mark.parametrize("bad", ["block", "deny", "quarantine", "", 1, None])
def test_unknown_response_values(bad):
    with pytest.raises(ValueError):
        Response(bad)


@pytest.mark.parametrize("responses, severity, terminate, approval", [
    ([], None, False, False),
    (["log"], Response.WARN, False, False),
    (["escalate"], Response.PAUSE, False, True),
    (["terminate"], Response.BREAK, True, False),
    (["log", "escalate"], Response.PAUSE, False, True),
    (["escalate", "terminate"], Response.BREAK, True, True),
    (["log", "escalate", "terminate"], Response.BREAK, True, True),
])
def test_decision_is_the_most_severe(responses, severity, terminate, approval):
    mon, _ = monitor(*[rule(f"r{i}", r) for i, r in enumerate(responses)])
    d = mon.on_action(ev())
    assert isinstance(d, Decision)
    assert d.severity is severity
    assert d.terminate is terminate
    assert d.approval_required is approval
    assert d.clean is (not responses)
    assert [v.response.severity for v in d.violations] == [r.upper() for r in responses]


# --- hooks ------------------------------------------------------------------------

def test_log_invokes_no_hook():
    mon, rec = monitor(rule("r", "log"))
    mon.on_action(ev())
    assert rec.escalated == rec.terminated == []


def test_escalate_invokes_escalation_hook_once_with_its_violations():
    mon, rec = monitor(rule("a", "escalate"), rule("b", "escalate"), rule("c", "log"))
    mon.on_action(ev(session="S"))
    assert rec.escalated == [("S", ["a", "b"])]
    assert rec.terminated == []


def test_terminate_invokes_termination_hook_and_suppresses_escalation():
    mon, rec = monitor(rule("a", "escalate"), rule("b", "terminate"))
    mon.on_action(ev(session="S"))
    assert rec.terminated == [("S", ["b"])]
    assert rec.escalated == []


def test_hooks_fire_once_per_rule_per_session_by_default():
    mon, rec = monitor(rule("a", "escalate"))
    for t in range(5):
        mon.on_action(ev(t))
    assert rec.escalated == [("s1", ["a"])]


def test_approve_rearms_so_the_next_breach_escalates_again():
    mon, rec = monitor(rule("a", "escalate"))
    d = mon.on_action(ev(0))
    mon.approve("s1", d.violations)
    mon.on_action(ev(1))
    mon.on_action(ev(2))   # not approved again: stays muted
    assert rec.escalated == [("s1", ["a"]), ("s1", ["a"])]


def test_hooks_are_per_session():
    mon, rec = monitor(rule("a", "terminate"))
    for s in ("A", "B", "A"):
        mon.on_action(ev(0, session=s))
    assert rec.terminated == [("A", ["a"]), ("B", ["a"])]


def test_replay_stops_at_first_terminate():
    mon, rec = monitor(rule("a", "terminate", max_calls=2))
    decisions = mon.replay([ev(t) for t in range(10)])
    assert len(decisions) == 3 and decisions[-1].terminate
    assert rec.terminated == [("s1", ["a"])]


def test_hook_exception_propagates_to_the_caller():
    """A failing hook is not swallowed (fail-loud), so the runtime sees it."""
    class Boom:
        def escalate(self, session_id, violations):
            raise RuntimeError("approval service down")

        def terminate(self, session_id, violations):
            raise RuntimeError("cannot kill")

    mon = SequenceMonitor(parse_policy({"rules": [rule("a", "terminate")]}), log=False,
                          termination_hook=Boom())
    with pytest.raises(RuntimeError, match="cannot kill"):
        mon.on_action(ev())


def test_protocols_and_builtin_hooks():
    for hooks in (NoOpHooks(), LoggingHooks(), Recorder()):
        assert isinstance(hooks, EscalationHook)
        assert isinstance(hooks, TerminationHook)


def test_logging_hooks_record_and_log(caplog):
    hooks = LoggingHooks()
    mon = SequenceMonitor(parse_policy({"rules": [rule("e", "escalate"), rule("t", "terminate")]}),
                          log=True, escalation_hook=hooks, termination_hook=hooks)
    with caplog.at_level(logging.WARNING, logger="seqmon"):
        mon.on_action(ev(session="X"))
    assert hooks.terminated == ["X"] and hooks.escalated == []
    levels = {r.levelno for r in caplog.records}
    assert logging.ERROR in levels and logging.CRITICAL in levels


@pytest.mark.parametrize("response, level", [
    ("log", logging.WARNING), ("escalate", logging.ERROR), ("terminate", logging.CRITICAL),
])
def test_audit_log_level_per_severity(caplog, response, level):
    handler = ResponseHandler(log=True)
    mon = SequenceMonitor(parse_policy({"rules": [rule("r", response)]}), log=False)
    violations = mon.evaluator.on_action(ev())
    with caplog.at_level(logging.DEBUG, logger="seqmon"):
        handler.handle(violations)
    assert [r.levelno for r in caplog.records] == [level]


def test_history_limit_none_keeps_everything():
    handler = ResponseHandler(log=False, history_limit=None)
    mon = SequenceMonitor(parse_policy({"rules": [rule("r", "log")]}), log=False,
                          repeat_alerts=True)
    for t in range(1500):
        handler.handle(mon.evaluator.on_action(ev(t)))
    assert len(handler.history) == 1500
