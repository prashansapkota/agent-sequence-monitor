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

Timestamp contract (enforced in :meth:`IncrementalEvaluator.on_action`):
timestamps are supplied by the interceptor, not the agent, and are treated
as monotonic per session. A late event (timestamp below the session's
latest) is clamped up to that latest timestamp, so it can neither sit
behind newer events in a window nor produce a negative ordering gap. If a
``clock`` is given, a timestamp more than ``max_clock_skew`` seconds ahead
of it is clamped down to ``clock() + max_clock_skew``, so one far-future
stamp cannot empty every window. Without a clock there is no forward bound,
so the interceptor's timestamps must be trusted. Clamped events are counted
in ``SessionState.clamped_events``.

Idle-session expiry has two parts. Expiry *on return* is per session: a
session whose next event is at least ``idle_ttl`` after its previous one
starts fresh, which cannot change a result because every window is already
empty. The *sweep*, which drops sessions that never return, needs a notion
of "now" shared by all sessions, and the event's own timestamp is not one:
another session's far-future stamp, or a session on a different time base,
would drop live state (REVIEW.md R2-M1). So the sweep never reads event
timestamps unless the caller says they share one time base:

- with a ``clock``: the sweep uses ``clock() - max_clock_skew`` as now.
  Results are unchanged as long as no event is stamped more than
  ``max_clock_skew`` behind the clock;
- with ``shared_time_base=True`` and no clock: the sweep uses the event's
  timestamp, as before. Only correct when every session's stamps come
  from one trusted time base (e.g. replaying one recorded trace); one bad
  stamp then affects every session;
- otherwise (the default): no sweep. Results never depend on other
  sessions, but memory across sessions grows with the number of sessions
  that never return.

The public entry point is :meth:`IncrementalEvaluator.on_action`.
``SequenceEvaluator`` and ``observe`` are the original names, kept as
aliases so existing callers (bench/, experiments/, tests/) keep working.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import NoReturn

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
    """A sequence constraint that has been breached.

    Attributes:
        rule: Name of the violated rule.
        response: The rule's response (``Response.WARN``/``PAUSE``/``BREAK``,
            a.k.a. ``LOG``/``ESCALATE``/``TERMINATE``).
        message: The rule's message.
        session_id: Session the violation belongs to.
        observed: The value that breached the threshold (for ordering
            rules: seconds since the ``before`` action).
        threshold: The rule's threshold (0 for ordering rules).
        events_elapsed: Events seen in the session when detected -- the
            detection-latency measure.
        event: The event that tipped it over (with its timestamp as
            clamped by the evaluator, see the module docstring).
        kind: Constraint type: ``cumulative``, ``rate``, ``ordering`` or
            ``scope``.
    """

    rule: str
    response: Response
    message: str
    session_id: str
    observed: float
    threshold: float
    events_elapsed: int
    event: ToolCallEvent
    kind: str = ""

    def describe(self) -> str:
        """One-line human-readable summary for logs."""
        detail = self.message or f"sequence constraint {self.rule!r} violated"
        if self.kind == "ordering":
            measure = f"forbidden action {self.observed:g}s after the enabling one"
        else:
            measure = f"observed {self.observed:g} > threshold {self.threshold:g}"
        return f"[{self.response.value}] {detail} ({measure}, after {self.events_elapsed} events)"


def _check_cumulative(
    rule: CumulativeConstraint, state: SessionState, event: ToolCallEvent
) -> tuple[float, bool]:
    win = state.record(rule.name, rule.window, event, rule.attribute)
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
    """Fire when more than ``max_outside`` escapes fall in one window.

    Only out-of-scope accesses enter the window, so in-scope traffic costs
    nothing and cannot dilute the count.
    """
    if event.resource is None or event.resource in rule.allowed:
        return 0.0, False
    count = state.record(rule.name, rule.window, event).count()
    return float(count), count > rule.max_outside


def _unknown_constraint(rule: NoReturn) -> NoReturn:
    """Type-checked exhaustiveness guard: adding a fifth constraint class to
    ``Constraint`` without a branch makes mypy reject the call site."""
    raise TypeError(f"unsupported constraint type {type(rule).__name__}")


def _threshold_of(rule: Constraint) -> float:
    if isinstance(rule, CumulativeConstraint):
        return rule.threshold
    if isinstance(rule, RateConstraint):
        return float(rule.max_calls)
    if isinstance(rule, ScopeConstraint):
        return float(rule.max_outside)
    if isinstance(rule, OrderingConstraint):
        return 0.0
    _unknown_constraint(rule)


def _kind_of(rule: Constraint) -> str:
    if isinstance(rule, CumulativeConstraint):
        return "cumulative"
    if isinstance(rule, RateConstraint):
        return "rate"
    if isinstance(rule, OrderingConstraint):
        return "ordering"
    if isinstance(rule, ScopeConstraint):
        return "scope"
    _unknown_constraint(rule)


class IncrementalEvaluator:
    """Evaluates a sequence policy against a live stream of events.

    Each call to :meth:`on_action` updates per-rule state for one event and
    checks every rule in O(1) per rule; history is never rescanned.

    Args:
        policy: The parsed sequence policy to enforce.
        store: Optional shared state store; one is created if omitted.
        repeat_alerts: When False (default) each rule alerts at most once
            per session until it is re-armed (:meth:`rearm`, called when an
            escalation is approved), so a sustained breach does not flood
            the results with duplicates and skew the detection-latency
            statistic.
        idle_ttl: Sessions idle this many seconds are dropped (see
            ``StateStore.expire_idle``). ``None`` (default) uses the
            policy's longest rule window, the shortest value that never
            discards a live window event. ``0`` or less disables expiry.
        clock: Optional time source (seconds) used to bound future
            timestamps and to drive the idle sweep; see the module
            docstring.
        max_clock_skew: Seconds a timestamp may run ahead of (or, for the
            sweep to stay result-neutral, behind) ``clock()``. Must be
            non-negative.
        shared_time_base: Without a clock, let event timestamps drive the
            idle sweep. Only safe when all sessions share one trusted time
            base; see the module docstring.

    Raises:
        ValueError: If ``max_clock_skew`` is negative, or ``idle_ttl`` is
            positive but shorter than the longest rule window (that would
            discard live window events and change results).
    """

    def __init__(
        self,
        policy: SequencePolicy,
        store: StateStore | None = None,
        repeat_alerts: bool = False,
        idle_ttl: float | None = None,
        clock: Callable[[], float] | None = None,
        max_clock_skew: float = 60.0,
        shared_time_base: bool = False,
    ) -> None:
        self.policy = policy
        # `is None`, not `or`: StateStore defines __len__, so an empty
        # store is falsy and `or` would silently discard a passed-in one.
        self.store = StateStore() if store is None else store
        self.repeat_alerts = repeat_alerts
        longest = max((r.window for r in policy.rules), default=0.0)
        if idle_ttl is None:
            idle_ttl = longest
        elif 0 < idle_ttl < longest:
            raise ValueError(
                f"idle_ttl={idle_ttl} is shorter than the longest rule window "
                f"({longest} s) and would drop live window events; use >= "
                f"{longest}, or 0 to disable expiry"
            )
        if not max_clock_skew >= 0:   # also rejects NaN
            raise ValueError(f"max_clock_skew must be >= 0, got {max_clock_skew}")
        self.idle_ttl = idle_ttl
        self.clock = clock
        self.max_clock_skew = max_clock_skew
        self.shared_time_base = shared_time_base
        self._last_sweep = -math.inf

    def on_action(self, event: ToolCallEvent) -> list[Violation]:
        """Process one permitted tool call; return any violations it triggers.

        Returns an empty list in the common case, so the caller's hot path
        stays cheap.
        """
        store = self.store
        sid = event.session_id
        ts = event.timestamp
        clock = self.clock
        now: float | None = None
        if clock is not None:
            now = clock()
            ts = min(ts, now + self.max_clock_skew)
        ttl = self.idle_ttl
        state = store.peek(sid)
        if state is not None:
            last = state.last_timestamp
            if last is not None and ts < last:
                ts = last
            elif last is not None and ttl > 0 and ts - last >= ttl:
                # Idle at least as long as the longest window: every
                # window is empty, so the session starts fresh.
                store.drop(sid)
                state = None
        # The sweep's "now" never comes from an untrusted event stamp; see
        # the module docstring. A clock reading is backed off by the skew
        # allowance so an event stamped up to max_clock_skew late still
        # finds its session.
        if now is not None:
            now -= self.max_clock_skew
        elif self.shared_time_base:
            now = ts
        if now is not None and ttl > 0:
            last_sweep = self._last_sweep
            # Amortised: sweeping every ttl/16 keeps idle sessions for at
            # most 1.0625 x ttl. A clock that steps backwards re-arms it.
            if now - last_sweep >= ttl / 16 or now < last_sweep:
                store.expire_idle(now, ttl)
                self._last_sweep = now
                state = store.peek(sid)
        if state is None:
            state = store.get(sid)
        else:
            store.touch(sid)
        if ts != event.timestamp:
            event = replace(event, timestamp=ts)
            state.clamped_events += 1
        state.observe(event)
        fired = state.fired
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
            else:
                _unknown_constraint(rule)

            if not breached:
                continue

            if not self.repeat_alerts and rule.name in fired:
                continue
            fired.add(rule.name)

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
                    kind=_kind_of(rule),
                )
            )

        return violations

    observe = on_action
    """Original name of :meth:`on_action` (same function object)."""

    def reset(self, session_id: str) -> None:
        """Forget a session's state and its fired-alert record. O(1)."""
        self.store.drop(session_id)

    def rearm(self, session_id: str, rules: Iterable[str]) -> None:
        """Let ``rules`` alert again in this session.

        Called after a human approves an escalation, so that one approval
        does not silence the rule for the rest of the session: the next
        breaching event escalates again.
        """
        state = self.store.peek(session_id)
        if state is not None:
            state.fired.difference_update(rules)


SequenceEvaluator = IncrementalEvaluator
"""Original name of :class:`IncrementalEvaluator`."""
