"""Public entry point: ``SequenceMonitor``.

Wires the accumulator, evaluator, and response handler into the one object
an integration needs. Typical use, alongside an existing per-call engine::

    monitor = SequenceMonitor.from_file("policies/examples/exfiltration.yaml")

    # ... inside the tool-call interceptor, after the per-call engine allows:
    decision = monitor.on_action(event)
    if decision.terminate:
        raise SessionTerminated(decision.violations[0].describe())
    if decision.approval_required:
        if await request_human_approval(decision):
            monitor.approve(event.session_id, decision.violations)

The monitor never vetoes an individual call on its own authority -- by
the time it sees one, the per-call engine has already permitted it. It
acts on the session.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .evaluator import IncrementalEvaluator, Violation
from .events import ToolCallEvent
from .response import Decision, EscalationHook, ResponseHandler, TerminationHook
from .spec import DEFAULT_MAX_EVENTS_PER_WINDOW, SequencePolicy, load_policy
from .state import StateStore


class SequenceMonitor:
    """Observes a permitted tool-call stream and enforces sequence policy.

    Args:
        policy: The parsed sequence policy.
        log: Whether to log each violation to the ``seqmon`` logger.
        repeat_alerts: Whether a rule may alert more than once per session.
        max_events_per_window: Per-rule window cap. ``None`` (default) uses
            the policy file's ``sequence_defaults.max_events_per_window``,
            falling back to 10,000.
        escalation_hook: Called on ``ESCALATE`` (``pause``) decisions.
        termination_hook: Called on ``TERMINATE`` (``break``) decisions.
        idle_ttl: Idle-session expiry in seconds; ``None`` uses the
            policy's longest window (see ``IncrementalEvaluator``).
        clock: Optional time source bounding future timestamps and driving
            the idle-session sweep.
        max_clock_skew: Seconds a timestamp may run ahead of ``clock()``.
        shared_time_base: Without a clock, let event timestamps drive the
            idle-session sweep. Only safe when every session's timestamps
            come from one trusted time base (see ``IncrementalEvaluator``).
        history_limit: Violations kept in ``responder.history``; ``None``
            keeps all of them.
    """

    def __init__(
        self,
        policy: SequencePolicy,
        log: bool = True,
        repeat_alerts: bool = False,
        max_events_per_window: int | None = None,
        escalation_hook: EscalationHook | None = None,
        termination_hook: TerminationHook | None = None,
        idle_ttl: float | None = None,
        clock: Callable[[], float] | None = None,
        max_clock_skew: float = 60.0,
        shared_time_base: bool = False,
        history_limit: int | None = 1_000,
    ) -> None:
        self.policy = policy
        if max_events_per_window is None:
            max_events_per_window = (
                policy.max_events_per_window or DEFAULT_MAX_EVENTS_PER_WINDOW
            )
        self.store = StateStore(max_events_per_window=max_events_per_window)
        self.evaluator = IncrementalEvaluator(
            policy,
            store=self.store,
            repeat_alerts=repeat_alerts,
            idle_ttl=idle_ttl,
            clock=clock,
            max_clock_skew=max_clock_skew,
            shared_time_base=shared_time_base,
        )
        self.responder = ResponseHandler(
            log=log,
            escalation_hook=escalation_hook,
            termination_hook=termination_hook,
            history_limit=history_limit,
        )

    @classmethod
    def from_file(cls, path: str | Path, **kwargs: Any) -> SequenceMonitor:
        """Build a monitor from a YAML policy file (either layout).

        Keyword arguments are passed to the constructor.
        """
        return cls(load_policy(path), **kwargs)

    def on_action(self, event: ToolCallEvent) -> Decision:
        """Process one permitted tool call and decide what should happen."""
        return self.responder.handle(self.evaluator.on_action(event))

    observe = on_action
    """Original name of :meth:`on_action` (same function object)."""

    def replay(self, events: list[ToolCallEvent]) -> list[Decision]:
        """Feed a recorded trace through the monitor.

        Stops at the first terminating decision, matching what would
        happen in a live session.
        """
        decisions: list[Decision] = []
        for event in events:
            decision = self.on_action(event)
            decisions.append(decision)
            if decision.terminate:
                break
        return decisions

    def reset(self, session_id: str) -> None:
        """Forget one session's accumulated state."""
        self.evaluator.reset(session_id)

    def approve(self, session_id: str, violations: Iterable[Violation]) -> None:
        """Record that a human approved an escalation.

        Re-arms the escalating rules, so the next breaching event in the
        session escalates again instead of the rule staying silent.
        """
        self.evaluator.rearm(session_id, (v.rule for v in violations))

    def footprint(self, session_id: str) -> int:
        """Events currently retained for a session (bounded-memory check).

        Does not create state for an unknown session; returns 0 instead.
        """
        state = self.store.peek(session_id)
        return 0 if state is None else state.memory_footprint()
