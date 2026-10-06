"""Session state accumulator.

Holds the state the per-call engine deliberately does not: what this
session has done so far. The design constraint is that memory stays
bounded by the window and the rule set, never by session length -- an
agent running unattended for hours must not grow the monitor's footprint
without limit. That property is what makes the overhead measurement
meaningful, so it is enforced structurally rather than by convention:

- Events live in a ``deque`` per tracked key, evicted by window expiry.
- ``max_events`` caps each deque as a backstop against a burst inside a
  single window, trading exactness for a hard memory ceiling. The cap
  comes from configuration: ``sequence_defaults.max_events_per_window``
  in the policy file, or the ``max_events_per_window`` constructor
  argument. (It is enforced by hand rather than with ``deque(maxlen=...)``
  because a silently dropped event must also be subtracted from the
  running aggregates, and counted in ``dropped``.)
- Running sums are maintained incrementally, adjusted on eviction, so
  aggregates never rescan the deque.

Because eviction is by timestamp rather than wall clock, replaying a
recorded benchmark trace yields identical results every run.

Memory bounds, stated precisely:

- **Per session:** at most ``rules x max_events_per_window`` retained
  events, whatever the session length.
- **Across sessions:** ``StateStore`` keeps sessions in least-recently-seen
  order and :meth:`StateStore.expire_idle` drops every session idle for at
  least ``idle_ttl`` seconds before ``now``. ``IncrementalEvaluator`` keeps
  ``idle_ttl`` at least the policy's longest window, so a dropped session
  has no live event in any window *provided ``now`` is on that session's
  time base*; what is lost is then only its fired-alert record and event
  counter. If ``now`` came from another session's timestamp, a live
  session could be dropped and results would change. That is why the
  evaluator sweeps only from a clock, or from event time when the caller
  declares one shared time base (REVIEW.md R2-M1). With neither, it does
  not sweep, and memory across sessions grows with the number of sessions
  that never return.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field

from .events import ToolCallEvent
from .spec import DEFAULT_MAX_EVENTS_PER_WINDOW


@dataclass
class _Window:
    """A sliding window with incrementally maintained aggregates.

    Both the magnitude sum and the distinct-resource count are kept as
    running values, adjusted on insert and on eviction. Neither rescans
    the deque, so the per-event cost stays O(1) in window occupancy --
    the property the overhead experiment measures.

    Attributes:
        span: Window length in seconds.
        max_events: Hard cap on retained events (the backstop).
        attribute: Event attribute summed into ``total``; ``None`` sums
            ``event.magnitude``.
        events: Retained events, oldest first.
        total: Running sum of the summed quantity over ``events``.
        dropped: Events shed by the ``max_events`` backstop.
    """

    span: float
    max_events: int
    attribute: str | None = None
    events: deque[ToolCallEvent] = field(default_factory=deque)
    total: float = 0.0
    dropped: int = 0
    # resource -> occurrences currently in the window; a key is removed
    # when its count hits zero, so len() is the distinct count.
    _resources: dict[str, int] = field(default_factory=dict)

    def add(self, event: ToolCallEvent) -> None:
        """Append an event, then evict anything now outside the window."""
        self.events.append(event)
        # Inlined rather than event.value(): this is the hot path.
        attr = self.attribute
        self.total += event.magnitude if attr is None else event.value(attr)
        if event.resource is not None:
            self._resources[event.resource] = self._resources.get(event.resource, 0) + 1
        self._evict(event.timestamp)

    def _forget(self, event: ToolCallEvent) -> None:
        """Remove one event's contribution to the running aggregates."""
        attr = self.attribute
        self.total -= event.magnitude if attr is None else event.value(attr)
        resource = event.resource
        if resource is not None:
            remaining = self._resources.get(resource, 0) - 1
            if remaining > 0:
                self._resources[resource] = remaining
            else:
                self._resources.pop(resource, None)

    def _evict(self, now: float) -> None:
        cutoff = now - self.span
        while self.events and self.events[0].timestamp <= cutoff:
            self._forget(self.events.popleft())
        while len(self.events) > self.max_events:
            self._forget(self.events.popleft())
            self.dropped += 1
        # Guard against float drift accumulating over long sessions.
        if not self.events:
            self.total = 0.0
            self._resources.clear()

    def prune(self, now: float) -> None:
        """Evict expired events without adding one.

        Needed so a constraint reading state between calls sees a window
        that is current rather than frozen at the last event.
        """
        self._evict(now)

    def count(self) -> int:
        """Number of events currently in the window. O(1)."""
        return len(self.events)

    def distinct_resources(self) -> int:
        """Number of distinct resources currently in the window. O(1)."""
        return len(self._resources)


class SessionState:
    """Per-session accumulator, keyed by rule name.

    Each rule gets its own window, so rules with different spans do not
    interfere and a rule can be added or removed without disturbing others.

    Session-level counters (``total_events``, ``last_timestamp``) are O(1)
    to update; per-rule windows are bounded deques.

    Args:
        session_id: Session this state belongs to.
        max_events_per_window: Hard cap on retained events per rule.
    """

    def __init__(
        self, session_id: str, max_events_per_window: int = DEFAULT_MAX_EVENTS_PER_WINDOW
    ) -> None:
        if max_events_per_window < 1:
            raise ValueError("max_events_per_window must be >= 1")
        self.session_id = session_id
        self.max_events_per_window = max_events_per_window
        self._windows: dict[str, _Window] = {}
        self.total_events = 0          # lifetime count, for latency reporting
        # None until the first event, so the first timestamp is never clamped.
        self.last_timestamp: float | None = None
        # Rules that have alerted in this session (used when repeat_alerts
        # is off). Kept here rather than in the evaluator so dropping the
        # session frees it and resetting it is O(1).
        self.fired: set[str] = set()
        # Events whose timestamp was raised to keep the session monotonic
        # (late arrivals) or lowered to the clock bound (future stamps).
        self.clamped_events = 0

    def window(self, rule_name: str, span: float, attribute: str | None = None) -> _Window:
        """Get or create the window for a rule.

        ``span`` and ``attribute`` are only used when the window is created.
        """
        win = self._windows.get(rule_name)
        if win is None:
            win = _Window(span=span, max_events=self.max_events_per_window, attribute=attribute)
            self._windows[rule_name] = win
        return win

    def record(
        self,
        rule_name: str,
        span: float,
        event: ToolCallEvent,
        attribute: str | None = None,
    ) -> _Window:
        """Add an event to a rule's window and return that window."""
        win = self.window(rule_name, span, attribute)
        win.add(event)
        return win

    def observe(self, event: ToolCallEvent) -> None:
        """Update session-level counters. Call once per event."""
        self.total_events += 1
        last = self.last_timestamp
        self.last_timestamp = event.timestamp if last is None else max(last, event.timestamp)

    def prune(self, now: float) -> None:
        """Evict expired events from every rule window, not only active ones."""
        for win in self._windows.values():
            win.prune(now)

    def memory_footprint(self, now: float | None = None) -> int:
        """Total events currently retained across all windows.

        The quantity the overhead experiment plots against session length;
        it should plateau rather than grow. With ``now``, windows are pruned
        to that time first, so idle rules are not counted with stale events.
        """
        if now is not None:
            self.prune(now)
        return sum(w.count() for w in self._windows.values())


class StateStore:
    """Holds ``SessionState`` for every active session.

    Sessions are kept in least-recently-seen order (``get`` moves a session
    to the end), so :meth:`expire_idle` only inspects sessions that are
    actually idle: its cost is O(sessions dropped + 1).

    Args:
        max_events_per_window: Cap passed to every ``SessionState``.
    """

    def __init__(self, max_events_per_window: int = DEFAULT_MAX_EVENTS_PER_WINDOW) -> None:
        self.max_events_per_window = max_events_per_window
        self._sessions: OrderedDict[str, SessionState] = OrderedDict()
        self.expired = 0   # sessions dropped by expire_idle, for reporting

    def get(self, session_id: str) -> SessionState:
        """Get or create the state for a session, marking it most recently seen."""
        sessions = self._sessions
        state = sessions.get(session_id)
        if state is None:
            state = SessionState(session_id, self.max_events_per_window)
            sessions[session_id] = state
        else:
            sessions.move_to_end(session_id)
        return state

    def touch(self, session_id: str) -> None:
        """Mark an existing session most recently seen."""
        self._sessions.move_to_end(session_id)

    def peek(self, session_id: str) -> SessionState | None:
        """The state for a session, or ``None``; never creates one."""
        return self._sessions.get(session_id)

    def drop(self, session_id: str) -> None:
        """Forget a session, e.g. after termination."""
        self._sessions.pop(session_id, None)

    def expire_idle(self, now: float, idle_ttl: float) -> int:
        """Drop sessions whose last event is at least ``idle_ttl`` before ``now``.

        Returns the number of sessions dropped.
        """
        sessions = self._sessions
        cutoff = now - idle_ttl
        dropped = 0
        while sessions:
            oldest = next(iter(sessions.values()))
            last = oldest.last_timestamp
            if last is None or last > cutoff:
                break
            sessions.popitem(last=False)
            dropped += 1
        self.expired += dropped
        return dropped

    def __len__(self) -> int:
        """Number of sessions currently held."""
        return len(self._sessions)
