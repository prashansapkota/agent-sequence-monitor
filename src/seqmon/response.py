"""Response handling for detected sequence violations.

Three severities. Policy files may use AGT's A2A conversation verbs or the
project's severity names; they map to the same ``Response`` members:

=============  =========  =====================================================
severity       verb       effect
=============  =========  =====================================================
``LOG``        ``warn``   Record and continue. The agent is unaffected.
``ESCALATE``   ``pause``  Require human approval before the session's next
                          action. Surfaced as ``Decision.approval_required``
                          and passed to the :class:`EscalationHook`. The
                          monitor cannot block anything itself: the
                          embedding runtime must hold the session until
                          approval, then call ``SequenceMonitor.approve``
                          to re-arm the rule.
``TERMINATE``  ``break``  End the session. Surfaced as ``Decision.terminate``
                          and passed to the :class:`TerminationHook`.
=============  =========  =====================================================

This module decides *what* should happen and records it. It does not kill
processes or prompt humans itself -- the embedding runtime owns that, via
the two hook protocols. Keeping the decision separate from the mechanism
is what lets the benchmark harness replay adversarial traces and observe
outcomes without side effects; the default hooks do nothing.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .evaluator import Violation
from .spec import Response

logger = logging.getLogger("seqmon")


@dataclass
class Decision:
    """The aggregate outcome of one observed event.

    Attributes:
        violations: Violations the event triggered (often empty).
        terminate: True if any violation's response is ``TERMINATE``.
        approval_required: True if any violation's response is ``ESCALATE``.
    """

    violations: list[Violation] = field(default_factory=list)
    terminate: bool = False
    approval_required: bool = False

    @property
    def clean(self) -> bool:
        """True if the event triggered no violation."""
        return not self.violations

    @property
    def severity(self) -> Response | None:
        """The most severe response among the violations, if any."""
        if self.terminate:
            return Response.BREAK
        if self.approval_required:
            return Response.PAUSE
        if self.violations:
            return Response.WARN
        return None


@runtime_checkable
class EscalationHook(Protocol):
    """Called when a decision requires human approval (``ESCALATE``).

    An implementation might open an approval ticket and block the agent's
    next action until it is answered. It is called at most once per
    decision, with the escalating violations, and not at all when the
    same decision also terminates the session.
    """

    def escalate(self, session_id: str, violations: Sequence[Violation]) -> None:
        """Request human approval for ``session_id``."""
        ...


@runtime_checkable
class TerminationHook(Protocol):
    """Called when a decision terminates the session (``TERMINATE``).

    An implementation might cancel the agent's task or revoke its
    credentials. Called at most once per decision, with the terminating
    violations.
    """

    def terminate(self, session_id: str, violations: Sequence[Violation]) -> None:
        """End ``session_id``."""
        ...


class NoOpHooks:
    """Default hooks: do nothing. The ``Decision`` still reports the outcome."""

    def escalate(self, session_id: str, violations: Sequence[Violation]) -> None:
        """Do nothing."""

    def terminate(self, session_id: str, violations: Sequence[Violation]) -> None:
        """Do nothing."""


class LoggingHooks:
    """Hooks that only log what a real integration would do.

    Args:
        log: Logger to write to; defaults to the ``seqmon`` logger.
    """

    def __init__(self, log: logging.Logger | None = None) -> None:
        self.log = log or logger
        self.escalated: list[str] = []
        self.terminated: list[str] = []

    def escalate(self, session_id: str, violations: Sequence[Violation]) -> None:
        """Log an approval request and remember the session."""
        self.escalated.append(session_id)
        rules = ", ".join(v.rule for v in violations)
        self.log.error("ESCALATE session %s: human approval required (%s)", session_id, rules)

    def terminate(self, session_id: str, violations: Sequence[Violation]) -> None:
        """Log a termination and remember the session."""
        self.terminated.append(session_id)
        rules = ", ".join(v.rule for v in violations)
        self.log.critical("TERMINATE session %s (%s)", session_id, rules)


_LEVELS = {
    Response.WARN: logging.WARNING,
    Response.PAUSE: logging.ERROR,
    Response.BREAK: logging.CRITICAL,
}


class ResponseHandler:
    """Turns violations into a ``Decision``, emits the audit record, runs hooks.

    Args:
        log: Whether to emit each violation to the ``seqmon`` logger.
        escalation_hook: Called on ``ESCALATE`` decisions; default no-op.
        termination_hook: Called on ``TERMINATE`` decisions; default no-op.
        history_limit: Most recent violations kept in ``history`` (the
            in-memory audit trail; the logger is the durable one). ``None``
            keeps every violation, which grows without bound.
    """

    def __init__(
        self,
        log: bool = True,
        escalation_hook: EscalationHook | None = None,
        termination_hook: TerminationHook | None = None,
        history_limit: int | None = 1_000,
    ) -> None:
        self.log = log
        self.history: deque[Violation] = deque(maxlen=history_limit)
        noop = NoOpHooks()
        self.escalation_hook: EscalationHook = escalation_hook or noop
        self.termination_hook: TerminationHook = termination_hook or noop

    def handle(self, violations: list[Violation]) -> Decision:
        """Record ``violations`` and decide the session-level outcome."""
        decision = Decision(violations=list(violations))

        for violation in violations:
            self.history.append(violation)
            if self.log:
                logger.log(_LEVELS[violation.response], violation.describe())
            if violation.response is Response.BREAK:
                decision.terminate = True
            elif violation.response is Response.PAUSE:
                decision.approval_required = True

        if decision.terminate:
            breaking = [v for v in violations if v.response is Response.BREAK]
            self.termination_hook.terminate(breaking[0].session_id, breaking)
        elif decision.approval_required:
            pausing = [v for v in violations if v.response is Response.PAUSE]
            self.escalation_hook.escalate(pausing[0].session_id, pausing)

        return decision
