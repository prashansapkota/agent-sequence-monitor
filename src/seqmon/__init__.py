"""seqmon -- cumulative policy violation detection for autonomous agents.

A stateful sequence-monitoring layer that complements, rather than
replaces, a stateless per-call policy engine. The per-call engine keeps
deciding individual invocations; this layer observes the same stream of
permitted calls and enforces constraints over their sequence.
"""

from .events import ToolCallEvent
from .evaluator import SequenceEvaluator, Violation
from .monitor import SequenceMonitor
from .response import Decision, ResponseHandler
from .spec import (
    Aggregate,
    CumulativeConstraint,
    OrderingConstraint,
    RateConstraint,
    Response,
    ScopeConstraint,
    SequencePolicy,
    SpecError,
    load_policy,
)
from .state import SessionState, StateStore

__version__ = "0.1.0"

__all__ = [
    "Aggregate",
    "CumulativeConstraint",
    "Decision",
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
    "ToolCallEvent",
    "Violation",
    "load_policy",
]
