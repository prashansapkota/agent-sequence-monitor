"""Naive full-history reference evaluator, for tests only.

Written independently of ``bench/naive.py`` (which the property tests must
not import). It keeps every event of every session and, on each event,
recomputes every rule from its definition by scanning the whole history.
O(n) per event -- only usable on short traces, which is the point: it is
too simple to share a bug with the incremental evaluator's running sums.

Semantics it encodes (taken from ``docs/rule_format.md`` and the
``seqmon.evaluator`` module docstring, not from the incremental code):

- A rule only reacts to events its ``actions`` filter matches (empty =
  every action). Ordering rules ignore ``actions`` and use before/after.
- Windows are the half-open interval ``(now - window, now]`` where ``now``
  is the timestamp of the event being evaluated.
- Late events are clamped up to the session's latest timestamp.
- ``cumulative``: sum / count / distinct over matching events in the window;
  fires when the aggregate is strictly greater than ``threshold``.
- ``rate``: matching events in the window; fires when count > ``max_calls``.
- ``ordering``: on an ``after`` event, fires if a ``before`` event lies in
  the window; ``observed`` is the gap to the most recent ``before``.
- ``scope``: matching events whose resource is set and not in ``allowed``,
  counted in the window; fires when count > ``max_outside``.
- ``repeat_alerts=False``: a rule alerts once per session; a session whose
  next event comes at least ``idle_ttl`` (default: longest window) after the
  previous one starts fresh, so it may alert again.

The per-window ``max_events`` backstop is deliberately NOT modelled: traces
fed to this reference must stay under the cap.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from seqmon import (
    Aggregate,
    CumulativeConstraint,
    OrderingConstraint,
    RateConstraint,
    ScopeConstraint,
    SequencePolicy,
    ToolCallEvent,
)


@dataclass
class _Session:
    events: list[ToolCallEvent] = field(default_factory=list)  # clamped
    last: float | None = None
    fired: set[str] = field(default_factory=set)
    total: int = 0


@dataclass(frozen=True)
class RefAlert:
    rule: str
    session_id: str
    index: int  # position of the triggering event in the input trace
    observed: float
    events_elapsed: int


class NaiveReference:
    def __init__(self, policy: SequencePolicy, repeat_alerts: bool = False) -> None:
        self.policy = policy
        self.repeat_alerts = repeat_alerts
        self.ttl = max((r.window for r in policy.rules), default=0.0)
        self.sessions: dict[str, _Session] = {}

    @staticmethod
    def _in_window(e: ToolCallEvent, now: float, span: float) -> bool:
        return now - span < e.timestamp <= now

    def _value(self, rule: CumulativeConstraint, e: ToolCallEvent) -> float:
        if rule.attribute is None:
            return e.magnitude
        return float(e.attributes.get(rule.attribute, 0.0))

    def observe(self, event: ToolCallEvent, index: int = 0) -> list[RefAlert]:
        sess = self.sessions.get(event.session_id)
        ts = event.timestamp
        if sess is not None and sess.last is not None:
            if ts < sess.last:
                ts = sess.last
            elif self.ttl > 0 and ts - sess.last >= self.ttl:
                sess = None  # idle long enough: start fresh
        if sess is None:
            sess = _Session()
            self.sessions[event.session_id] = sess
        sess.last = ts if sess.last is None else max(sess.last, ts)
        sess.total += 1
        now = ts
        cur = ToolCallEvent(
            action=event.action, agent_id=event.agent_id, session_id=event.session_id,
            timestamp=ts, resource=event.resource, magnitude=event.magnitude,
            attributes=dict(event.attributes),
        )
        sess.events.append(cur)
        history = sess.events

        alerts: list[RefAlert] = []
        for rule in self.policy.rules:
            breached = False
            observed = 0.0
            if isinstance(rule, OrderingConstraint):
                if cur.action != rule.after:
                    continue
                befores = [
                    e for e in history[:-1]
                    if e.action == rule.before and self._in_window(e, now, rule.window)
                ]
                if befores:
                    breached = True
                    observed = now - max(e.timestamp for e in befores)
            else:
                if rule.actions and cur.action not in rule.actions:
                    continue
                matching = [
                    e for e in history
                    if (not rule.actions or e.action in rule.actions)
                    and self._in_window(e, now, rule.window)
                ]
                if isinstance(rule, CumulativeConstraint):
                    if rule.aggregate is Aggregate.SUM:
                        observed = sum(self._value(rule, e) for e in matching)
                    elif rule.aggregate is Aggregate.COUNT:
                        observed = float(len(matching))
                    else:
                        observed = float(
                            len({e.resource for e in matching if e.resource is not None})
                        )
                    breached = observed > rule.threshold
                elif isinstance(rule, RateConstraint):
                    observed = float(len(matching))
                    breached = observed > rule.max_calls
                elif isinstance(rule, ScopeConstraint):
                    if cur.resource is None or cur.resource in rule.allowed:
                        continue
                    outside = [
                        e for e in matching
                        if e.resource is not None and e.resource not in rule.allowed
                    ]
                    observed = float(len(outside))
                    breached = observed > rule.max_outside
                else:  # pragma: no cover
                    raise TypeError(rule)
            if not breached:
                continue
            if not self.repeat_alerts and rule.name in sess.fired:
                continue
            sess.fired.add(rule.name)
            alerts.append(RefAlert(rule.name, event.session_id, index, observed, sess.total))
        return alerts

    def run(self, events: list[ToolCallEvent]) -> list[RefAlert]:
        out: list[RefAlert] = []
        for i, e in enumerate(events):
            out.extend(self.observe(e, i))
        return out
