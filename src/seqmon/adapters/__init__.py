"""Adapters between a per-call governance engine and the sequence monitor.

``base`` defines the interfaces; ``fake_agt`` is an in-process stand-in for
the Microsoft Agent Governance Toolkit (AGT) per-call engine, used by the
demo and the tests. There is no adapter for the real AGT runtime yet; the
verified integration point and the open questions are written up in
``docs/agt_integration.md`` and ``OPEN_QUESTIONS.md``.
"""

from .base import CallFeed, InterceptOutcome, PerCallEngine, PerCallVerdict
from .fake_agt import EscalationRecord, FakeAGTInterceptor, FakePerCallEngine

__all__ = [
    "CallFeed",
    "EscalationRecord",
    "FakeAGTInterceptor",
    "FakePerCallEngine",
    "InterceptOutcome",
    "PerCallEngine",
    "PerCallVerdict",
]
