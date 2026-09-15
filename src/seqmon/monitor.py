"""Public entry point: ``SequenceMonitor``.

Wires the accumulator, evaluator, and response handler into the one object
an integration needs. Typical use, alongside an existing per-call engine::

    monitor = SequenceMonitor.from_file("policies/exfiltration.yaml")

    # ... inside the tool-call interceptor, after the per-call engine allows:
    decision = monitor.observe(event)
    if decision.terminate:
        raise SessionTerminated(decision.violations[0].describe())
    if decision.approval_required:
        await request_human_approval(decision)

The monitor never vetoes an individual call on its own authority -- by
the time it sees one, the per-call engine has already permitted it. It
acts on the session.
"""

from __future__ import annotations

from pathlib import Path

from .evaluator import SequenceEvaluator
from .events import ToolCallEvent
from .response import Decision, ResponseHandler
from .spec import SequencePolicy, load_policy
from .state import StateStore


class SequenceMonitor:
    """Observes a permitted tool-call stream and enforces sequence policy."""

    def __init__(
        self,
        policy: SequencePolicy,
        log: bool = True,
        repeat_alerts: bool = False,
        max_events_per_window: int = 10_000,
    ) -> None:
        self.policy = policy
        self.store = StateStore(max_events_per_window=max_events_per_window)
        self.evaluator = SequenceEvaluator(
            policy, store=self.store, repeat_alerts=repeat_alerts
        )
        self.responder = ResponseHandler(log=log)

    @classmethod
    def from_file(cls, path: str | Path, **kwargs) -> SequenceMonitor:
        """Build a monitor from a YAML policy file."""
        return cls(load_policy(path), **kwargs)

    def observe(self, event: ToolCallEvent) -> Decision:
        """Process one permitted tool call and decide what should happen."""
        return self.responder.handle(self.evaluator.observe(event))

    def replay(self, events: list[ToolCallEvent]) -> list[Decision]:
        """Feed a recorded trace through the monitor.

        Stops at the first terminating decision, matching what would
        happen in a live session.
        """
        decisions: list[Decision] = []
        for event in events:
            decision = self.observe(event)
            decisions.append(decision)
            if decision.terminate:
                break
        return decisions

    def reset(self, session_id: str) -> None:
        """Forget one session's accumulated state."""
        self.evaluator.reset(session_id)

    def footprint(self, session_id: str) -> int:
        """Events currently retained for a session (bounded-memory check)."""
        return self.store.get(session_id).memory_footprint()
