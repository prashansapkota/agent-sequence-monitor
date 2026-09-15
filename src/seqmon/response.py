"""Response handling for detected sequence violations.

Three severities, matching the toolkit's existing vocabulary:

``warn``
    Record and continue. The agent is unaffected.
``pause``
    Require human approval before the session proceeds. Surfaced to the
    caller as ``approval_required``; the integration decides how to ask.
``break``
    Terminate the session.

This module decides *what* should happen and records it. It does not kill
processes -- the embedding runtime owns that, and keeping the decision
separate from the mechanism is what lets the benchmark harness replay
adversarial traces and observe outcomes without side effects.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .evaluator import Violation
from .spec import Response

logger = logging.getLogger("seqmon")


@dataclass
class Decision:
    """The aggregate outcome of one observed event."""

    violations: list[Violation] = field(default_factory=list)
    terminate: bool = False
    approval_required: bool = False

    @property
    def clean(self) -> bool:
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


class ResponseHandler:
    """Turns violations into a ``Decision`` and emits the audit record.

    Args:
        log: Whether to emit violations to the ``seqmon`` logger.
    """

    def __init__(self, log: bool = True) -> None:
        self.log = log
        self.history: list[Violation] = []

    def handle(self, violations: list[Violation]) -> Decision:
        decision = Decision(violations=list(violations))

        for violation in violations:
            self.history.append(violation)
            if self.log:
                level = {
                    Response.WARN: logging.WARNING,
                    Response.PAUSE: logging.ERROR,
                    Response.BREAK: logging.CRITICAL,
                }[violation.response]
                logger.log(level, violation.describe())

            if violation.response is Response.BREAK:
                decision.terminate = True
            elif violation.response is Response.PAUSE:
                decision.approval_required = True

        return decision
