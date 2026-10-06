"""Interfaces for feeding intercepted tool calls into the sequence layer.

The data flow this models::

    agent --tool call--> interceptor --> per-call engine --allow/deny-->
                              |                                   |
                              |  (only if allowed)                v
                              +--> SequenceMonitor.on_action --> Decision

The per-call engine is AGT's job and stays stateless. The interceptor is
whatever sits on the tool-call path; it asks the per-call engine first and
forwards only permitted calls to the monitor, so the monitor never sees a
denied call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..events import ToolCallEvent
from ..response import Decision


@dataclass(frozen=True)
class PerCallVerdict:
    """The per-call engine's decision on one call.

    Field names follow AGT's ``PolicyDecision``
    (``agent_os/policies/evaluator.py``): ``allowed``, ``matched_rule``,
    ``action``, ``reason``.
    """

    allowed: bool
    action: str = "allow"
    matched_rule: str | None = None
    reason: str = ""


@runtime_checkable
class PerCallEngine(Protocol):
    """A stateless per-call policy check: one call in, one verdict out."""

    def check(self, event: ToolCallEvent) -> PerCallVerdict:
        """Decide whether this single call may run, ignoring history."""
        ...


@dataclass(frozen=True)
class InterceptOutcome:
    """What happened to one intercepted call.

    Attributes:
        event: The call.
        per_call: The per-call engine's verdict. ``None`` if the call was
            refused before reaching it because its session had already
            been terminated by the sequence monitor.
        decision: The sequence monitor's decision, or ``None`` if the call
            never reached the monitor (denied per-call, or session ended).
        executed: Whether the call is allowed to run.
        reason: Short explanation when ``executed`` is False.
    """

    event: ToolCallEvent
    per_call: PerCallVerdict | None
    decision: Decision | None
    executed: bool
    reason: str = ""


@runtime_checkable
class CallFeed(Protocol):
    """Anything that feeds intercepted calls into the sequence layer.

    A real AGT adapter would implement this on the tool-call path; the
    demo and tests use :class:`seqmon.adapters.FakeAGTInterceptor`.
    """

    def submit(self, event: ToolCallEvent) -> InterceptOutcome:
        """Run one call through per-call policy, then the sequence monitor."""
        ...
