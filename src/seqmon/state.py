"""Session state accumulator.

Holds the state the per-call engine deliberately does not: what this
session has done so far. The design constraint is that memory stays
bounded by the window and the rule set, never by session length -- an
agent running unattended for hours must not grow the monitor's footprint
without limit. That property is what makes the overhead measurement
meaningful, so it is enforced structurally rather than by convention:

- Events live in a ``deque`` per tracked key, evicted by window expiry.
- ``max_events`` caps each deque as a backstop against a burst inside a
  single window, trading exactness for a hard memory ceiling.
- Running sums are maintained incrementally, adjusted on eviction, so
  aggregates never rescan the deque.

Because eviction is by timestamp rather than wall clock, replaying a
recorded benchmark trace yields identical results every run.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .events import ToolCallEvent


@dataclass
class _Window:
    """A sliding window with incrementally maintained aggregates.

    Both the magnitude sum and the distinct-resource count are kept as
    running values, adjusted on insert and on eviction. Neither rescans
    the deque, so the per-event cost stays O(1) in window occupancy --
    the property the overhead experiment measures.
    """

    span: float
    max_events: int
    events: deque[ToolCallEvent] = field(default_factory=deque)
    total: float = 0.0
    dropped: int = 0  # events shed by the max_events backstop
    # resource -> occurrences currently in the window; a key is removed
    # when its count hits zero, so len() is the distinct count.
    _resources: dict[str, int] = field(default_factory=dict)

    def add(self, event: ToolCallEvent) -> None:
        """Append an event, then evict anything now outside the window."""
        self.events.append(event)
        self.total += event.magnitude
        if event.resource is not None:
            self._resources[event.resource] = self._resources.get(event.resource, 0) + 1
        self._evict(event.timestamp)

    def _forget(self, event: ToolCallEvent) -> None:
        """Remove one event's contribution to the running aggregates."""
        self.total -= event.magnitude
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
        return len(self.events)

    def distinct_resources(self) -> int:
        return len(self._resources)


class SessionState:
    """Per-session accumulator, keyed by rule name.

    Each rule gets its own window, so rules with different spans do not
    interfere and a rule can be added or removed without disturbing others.

    Args:
        session_id: Session this state belongs to.
        max_events_per_window: Hard cap on retained events per rule.
    """

    def __init__(self, session_id: str, max_events_per_window: int = 10_000) -> None:
        self.session_id = session_id
        self.max_events_per_window = max_events_per_window
        self._windows: dict[str, _Window] = {}
        self.total_events = 0          # lifetime count, for latency reporting
        self.outside_scope: dict[str, int] = {}   # rule name -> count
        self.last_timestamp: float = 0.0

    def window(self, rule_name: str, span: float) -> _Window:
        """Get or create the window for a rule."""
        win = self._windows.get(rule_name)
        if win is None:
            win = _Window(span=span, max_events=self.max_events_per_window)
            self._windows[rule_name] = win
        return win

    def record(self, rule_name: str, span: float, event: ToolCallEvent) -> _Window:
        """Add an event to a rule's window and return that window."""
        win = self.window(rule_name, span)
        win.add(event)
        return win

    def observe(self, event: ToolCallEvent) -> None:
        """Update session-level counters. Call once per event."""
        self.total_events += 1
        self.last_timestamp = max(self.last_timestamp, event.timestamp)

    def memory_footprint(self) -> int:
        """Total events currently retained across all windows.

        The quantity the overhead experiment plots against session length;
        it should plateau rather than grow.
        """
        return sum(w.count() for w in self._windows.values())


class StateStore:
    """Holds ``SessionState`` for every active session."""

    def __init__(self, max_events_per_window: int = 10_000) -> None:
        self.max_events_per_window = max_events_per_window
        self._sessions: dict[str, SessionState] = {}

    def get(self, session_id: str) -> SessionState:
        state = self._sessions.get(session_id)
        if state is None:
            state = SessionState(session_id, self.max_events_per_window)
            self._sessions[session_id] = state
        return state

    def drop(self, session_id: str) -> None:
        """Forget a session, e.g. after termination."""
        self._sessions.pop(session_id, None)

    def __len__(self) -> int:
        return len(self._sessions)
