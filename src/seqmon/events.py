"""Event model: the stream of intercepted tool calls this layer observes.

The monitoring layer is a passive observer of the same call stream the
per-call policy engine evaluates. It does not replace that engine, and it
never sees calls the engine has already denied -- by construction, every
event reaching this layer was individually permitted.

``ToolCallEvent`` is deliberately close in shape to the entries Agent-OS
threads through ``ExecutionContext.history`` (``action``, ``timestamp``,
``success``; see ``agent_os/stateless.py`` in the AGT source), extended
with the fields sequence constraints need: the resource touched and
numeric quantities to accumulate.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolCallEvent:
    """A single intercepted tool invocation that the per-call engine allowed.

    Args:
        action: Tool name, e.g. ``"customer_db.query"``. Matches the
            ``action`` key used in Agent-OS execution history; also
            readable as :attr:`tool_name`, the name AGT's
            ``ToolCallRequest`` uses.
        agent_id: Agent that issued the call.
        session_id: Session the call belongs to. Accumulators are scoped
            per session; this is the partition key.
        timestamp: Unix epoch seconds. Supplied by the interceptor rather
            than read from the clock, so replayed benchmark traces are
            deterministic.
        resource: Optional resource identifier the call touched, e.g. a
            table, bucket, or endpoint. Used by scope constraints.
        magnitude: The default numeric quantity this call contributes to
            ``sum`` aggregates -- rows returned, dollars spent, bytes
            written. Defaults to 0.0 for calls that only contribute to
            counts.
        success: Whether the call completed successfully.
        metadata: Arbitrary passthrough fields available to constraints.
        attributes: Named numeric quantities, e.g. ``{"records": 400,
            "cost_usd": 0.02}``. A cumulative rule with ``attribute: records``
            sums this entry instead of ``magnitude``. Missing keys count
            as 0.
    """

    action: str
    agent_id: str
    session_id: str
    timestamp: float
    resource: str | None = None
    magnitude: float = 0.0
    success: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    attributes: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Reject non-finite numbers.

        One NaN in a running sum makes the sum NaN until the window empties,
        and ``NaN > threshold`` is always False, so it would silently disable
        the rule. Negative values are allowed (e.g. a refund) and do offset
        a ``sum``; see ``docs/rule_format.md``.
        """
        if not math.isfinite(self.timestamp):
            raise ValueError(f"timestamp must be finite, got {self.timestamp!r}")
        if not math.isfinite(self.magnitude):
            raise ValueError(f"magnitude must be finite, got {self.magnitude!r}")
        for key, val in self.attributes.items():
            if not math.isfinite(val):
                raise ValueError(f"attributes[{key!r}] must be finite, got {val!r}")

    @property
    def tool_name(self) -> str:
        """Alias of :attr:`action`, matching AGT's ``ToolCallRequest.tool_name``."""
        return self.action

    def value(self, attribute: str | None = None) -> float:
        """The numeric quantity this event contributes to a ``sum``.

        Args:
            attribute: Name of an entry in :attr:`attributes`, or ``None``
                for :attr:`magnitude`.
        """
        if attribute is None:
            return self.magnitude
        return float(self.attributes.get(attribute, 0.0))
