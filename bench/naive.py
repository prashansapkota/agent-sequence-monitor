"""Naive baseline evaluator: rescan the whole session history on every event.

This is what a sequence check looks like without incremental state -- the
obvious implementation, and the one ``ExecutionContext.history`` invites.
It exists for two reasons:

1. **Overhead baseline.** Per-event cost grows linearly with session
   length, which is the contrast the overhead experiment draws against
   ``SequenceEvaluator``'s O(rules) per event.
2. **Oracle.** It computes each rule directly from its definition, with no
   deques, running sums or eviction, so agreement with the incremental
   evaluator on random traces is evidence the incremental bookkeeping is
   right (see ``tests/test_bench.py``).

One intentional difference: there is no ``max_events`` backstop. The
incremental evaluator sheds the oldest events once a window holds more than
``max_events_per_window``; the naive one never does. Traces that exceed the
backstop can therefore diverge -- ``bench/suites/evasion.py`` has one that
does so deliberately.
"""

from __future__ import annotations

from seqmon import (
    Aggregate,
    CumulativeConstraint,
    OrderingConstraint,
    RateConstraint,
    ScopeConstraint,
    SequencePolicy,
    ToolCallEvent,
)


class NaiveEvaluator:
    """Evaluates a policy by rescanning full history on every event.

    Fires each rule at most once per session, like the incremental
    evaluator's default.
    """

    def __init__(self, policy: SequencePolicy) -> None:
        self.policy = policy
        self.history: list[ToolCallEvent] = []
        self._fired: set[tuple[str, str]] = set()

    def observe(self, event: ToolCallEvent) -> list[tuple[str, float]]:
        """Return ``(rule name, observed value)`` for each new violation."""
        self.history.append(event)
        now = event.timestamp
        out: list[tuple[str, float]] = []

        for rule in self.policy.rules:
            ordering = isinstance(rule, OrderingConstraint)
            if not ordering and not rule.matches_action(event.action):
                continue
            cutoff = now - rule.window
            # The deliberate O(history) step: every event, every rule.
            live = [
                e for e in self.history
                if e.session_id == event.session_id and e.timestamp > cutoff
            ]

            if isinstance(rule, CumulativeConstraint):
                hits = [e for e in live if rule.matches_action(e.action)]
                if rule.aggregate is Aggregate.SUM:
                    observed = sum(e.magnitude for e in hits)
                elif rule.aggregate is Aggregate.COUNT:
                    observed = float(len(hits))
                else:
                    observed = float(len({e.resource for e in hits if e.resource is not None}))
                breached = observed > rule.threshold
            elif isinstance(rule, RateConstraint):
                observed = float(sum(1 for e in live if rule.matches_action(e.action)))
                breached = observed > rule.max_calls
            elif ordering:
                if event.action != rule.after or event.action == rule.before:
                    continue
                prior = [e for e in live[:-1] if e.action == rule.before]
                if not prior:
                    continue
                observed, breached = now - prior[-1].timestamp, True
            elif isinstance(rule, ScopeConstraint):
                if event.resource is None or event.resource in rule.allowed:
                    continue
                observed = float(sum(
                    1 for e in live
                    if rule.matches_action(e.action)
                    and e.resource is not None
                    and e.resource not in rule.allowed
                ))
                breached = observed > rule.max_outside
            else:  # pragma: no cover
                continue

            key = (event.session_id, rule.name)
            if breached and key not in self._fired:
                self._fired.add(key)
                out.append((rule.name, observed))
        return out
