"""Event model: the stream of intercepted tool calls this layer observes.

The monitoring layer is a passive observer of the same call stream the
per-call policy engine evaluates. It does not replace that engine, and it
never sees calls the engine has already denied -- by construction, every
event reaching this layer was individually permitted.

``ToolCallEvent`` is deliberately close in shape to the entries Agent-OS
threads through ``ExecutionContext.history`` (``action``, ``timestamp``,
``success``), extended with the fields sequence constraints need:
the resource touched and a numeric magnitude to accumulate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolCallEvent:
    """A single intercepted tool invocation that the per-call engine allowed.

    Args:
        action: Tool name, e.g. ``"customer_db.query"``. Matches the
            ``action`` key used in Agent-OS execution history.
        agent_id: Agent that issued the call.
        session_id: Session the call belongs to. Accumulators are scoped
            per session; this is the partition key.
        timestamp: Unix epoch seconds. Supplied by the interceptor rather
            than read from the clock, so replayed benchmark traces are
            deterministic.
        resource: Optional resource identifier the call touched, e.g. a
            table, bucket, or endpoint. Used by scope constraints.
        magnitude: Numeric quantity this call contributes to cumulative
            aggregates -- rows returned, dollars spent, bytes written.
            Defaults to 0.0 for calls that only contribute to counts.
        success: Whether the call completed successfully.
        metadata: Arbitrary passthrough fields available to constraints.
    """

    action: str
    agent_id: str
    session_id: str
    timestamp: float
    resource: str | None = None
    magnitude: float = 0.0
    success: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
