"""Incremental constraint evaluation.

Every constraint is re-checked when an event arrives, but none rescans
history: cumulative sums are maintained by the window, counts are deque
lengths, and ordering is a pair of timestamps. The per-event cost is
therefore O(rules), independent of session length -- the claim the
overhead experiment is designed to test.

A violation carries ``events_elapsed``, the number of events observed in
the session at the moment of detection. This is the detection-latency
measure: because a sequence is only recognisable once enough of it has
occurred, some latency is structural, and this records how much.
"""

from __future__ import annotations

from dataclasses import dataclass

from .events import ToolCallEvent
from .spec import (
    Aggregate,
    Constraint,
    CumulativeConstraint,
    OrderingConstraint,
    RateConstraint,
    Response,
    ScopeConstraint,
    SequencePolicy,
)
from .state import SessionState, StateStore


@dataclass(frozen=True)
class Violation:
    """A sequence constraint that has been breached."""

    rule: str
    response: Response
    message: str
    session_id: str
    observed: float        # the value that breached the threshold
    threshold: float
    events_elapsed: int    # events seen in session when detected
    event: ToolCallEvent   # the event that tipped it over

    def describe(self) -> str:
        detail = self.message or f"sequence constraint {self.rule!r} violated"
        return (
            f"[{self.response.value}] {detail} "
            f"(observed {self.observed:g} > threshold {self.threshold:g}, "
            f"after {self.events_elapsed} events)"
        )


def _check_cumulative(
    rule: CumulativeConstraint, state: SessionState, event: ToolCallEvent
) -> tuple[float, bool]:
    win = state.record(rule.name, rule.window, event)
    if rule.aggregate is Aggregate.SUM:
        observed = win.total
    elif rule.aggregate is Aggregate.COUNT:
        observed = float(win.count())
    else:
        observed = float(win.distinct_resources())
    return observed, observed > rule.threshold


def _check_rate(
    rule: RateConstraint, state: SessionState, event: ToolCallEvent
) -> tuple[float, bool]:
    win = state.record(rule.name, rule.window, event)
    observed = float(win.count())
    return observed, observed > rule.max_calls


def _check_ordering(
    rule: OrderingConstraint, state: SessionState, event: ToolCallEvent
) -> tuple[float, bool]:
    """Fire when ``after`` occurs while ``before`` is live in the window."""
    win = state.window(rule.name, rule.window)
    if event.action == rule.before:
        win.add(event)
        return 0.0, False
    if event.action == rule.after:
        win.prune(event.timestamp)
        if win.count() > 0:
            gap = event.timestamp - win.events[-1].timestamp
            return gap, True
    return 0.0, False


def _check_scope(
    rule: ScopeConstraint, state: SessionState, event: ToolCallEvent
) -> tuple[float, bool]:
    if event.resource is None or event.resource in rule.allowed:
        return 0.0, False
    count = state.outside_scope.get(rule.name, 0) + 1
    state.outside_scope[rule.name] = count
    return float(count), count > rule.max_outside


def _threshold_of(rule: Constraint) -> float:
    if isinstance(rule, CumulativeConstraint):
        return rule.threshold
    if isinstance(rule, RateConstraint):
        return float(rule.max_calls)
    if isinstance(rule, ScopeConstraint):
        return float(rule.max_outside)
    return 0.0


class SequenceEvaluator:
    """Evaluates a sequence policy against a live stream of events.

    Args:
        policy: The parsed sequence policy to enforce.
        store: Optional shared state store; one is created if omitted.
        repeat_alerts: When False (default) each rule alerts at most once
            per session, so a sustained breach does not flood the results
            with duplicates and skew the detection-latency statistic.
    """

    def __init__(
        self,
        policy: SequencePolicy,
        store: StateStore | None = None,
        repeat_alerts: bool = False,
    ) -> None:
        self.policy = policy
        # `is None`, not `or`: StateStore defines __len__, so an empty
        # store is falsy and `or` would silently discard a passed-in one.
        self.store = StateStore() if store is None else store
        self.repeat_alerts = repeat_alerts
        self._fired: set[tuple[str, str]] = set()   # (session_id, rule_name)

    def observe(self, event: ToolCallEvent) -> list[Violation]:
        """Process one permitted tool call; return any violations it triggers.

        Returns an empty list in the common case, so the caller's hot path
        stays cheap.
        """
        state = self.store.get(event.session_id)
        state.observe(event)
        violations: list[Violation] = []

        for rule in self.policy.rules:
            if not rule.matches_action(event.action):
                # Ordering rules name their actions in before/after, not
                # in the shared `actions` filter.
                if not isinstance(rule, OrderingConstraint):
                    continue

            if isinstance(rule, CumulativeConstraint):
                observed, breached = _check_cumulative(rule, state, event)
            elif isinstance(rule, RateConstraint):
                observed, breached = _check_rate(rule, state, event)
            elif isinstance(rule, OrderingConstraint):
                observed, breached = _check_ordering(rule, state, event)
            elif isinstance(rule, ScopeConstraint):
                observed, breached = _check_scope(rule, state, event)
            else:  # pragma: no cover - guarded by the spec parser
                continue

            if not breached:
                continue

            key = (event.session_id, rule.name)
            if not self.repeat_alerts and key in self._fired:
                continue
            self._fired.add(key)

            violations.append(
                Violation(
                    rule=rule.name,
                    response=rule.response,
                    message=rule.message,
                    session_id=event.session_id,
                    observed=observed,
                    threshold=_threshold_of(rule),
                    events_elapsed=state.total_events,
                    event=event,
                )
            )

        return violations

    def reset(self, session_id: str) -> None:
        """Forget a session's state and its fired-alert record."""
        self.store.drop(session_id)
        self._fired = {k for k in self._fired if k[0] != session_id}
