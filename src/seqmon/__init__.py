"""seqmon -- cumulative policy violation detection for autonomous agents.

A stateful sequence-monitoring layer that complements, rather than
replaces, a stateless per-call policy engine. The per-call engine keeps
deciding individual invocations; this layer observes the same stream of
permitted calls and enforces constraints over their sequence.

Entry points: :class:`SequenceMonitor` (evaluator + responses),
:class:`IncrementalEvaluator` (``on_action(event) -> list[Violation]``),
:func:`load_policy`. Adapters for feeding calls in live in
:mod:`seqmon.adapters`.
"""

from .evaluator import IncrementalEvaluator, SequenceEvaluator, Violation
from .events import ToolCallEvent
from .monitor import SequenceMonitor
from .response import (
    Decision,
    EscalationHook,
    LoggingHooks,
    NoOpHooks,
    ResponseHandler,
    TerminationHook,
)
from .spec import (
    DEFAULT_MAX_EVENTS_PER_WINDOW,
    Aggregate,
    Constraint,
    CumulativeConstraint,
    OrderingConstraint,
    RateConstraint,
    Response,
    ScopeConstraint,
    SequencePolicy,
    SpecError,
    load_policy,
    parse_policy,
)
from .state import SessionState, StateStore

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_MAX_EVENTS_PER_WINDOW",
    "Aggregate",
    "Constraint",
    "CumulativeConstraint",
    "Decision",
    "EscalationHook",
    "IncrementalEvaluator",
    "LoggingHooks",
    "NoOpHooks",
    "OrderingConstraint",
    "RateConstraint",
    "Response",
    "ResponseHandler",
    "ScopeConstraint",
    "SequenceEvaluator",
    "SequenceMonitor",
    "SequencePolicy",
    "SessionState",
    "SpecError",
    "StateStore",
    "TerminationHook",
    "ToolCallEvent",
    "Violation",
    "load_policy",
    "parse_policy",
]
