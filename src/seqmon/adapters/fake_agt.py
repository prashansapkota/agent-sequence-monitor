"""An in-process stand-in for the AGT per-call engine, for demos and tests.

**This is not AGT.** It re-implements, from reading the source, the small
part of AGT's flat YAML evaluation the demo needs, so that one policy file
can drive both layers without installing AGT (which pulls in pydantic and
the rest of ``agent_os``). What it mirrors, all from
``agent-governance-python/agent-os/src/agent_os/policies/`` at tag v3.5.0:

- ``schema.py``: a rule is ``name`` + ``condition {field, operator,
  value}`` + ``action`` (``allow`` / ``deny`` / ``audit`` / ``block``) +
  ``priority`` + ``message``; ``defaults.action`` applies when nothing
  matches.
- ``evaluator.py`` (``PolicyEvaluator._evaluate_flat``): rules sorted by
  ``priority`` descending, first match wins, ``allow`` and ``audit`` are
  permitted; no match falls back to ``defaults.action``.
- ``evaluator.py`` (``_match_condition``): a context field that is absent
  never matches; operators ``eq ne gt lt gte lte in matches contains``.

- ``evaluator.py`` (``evaluate``): an exception while matching fails
  closed, i.e. the call is denied.

What it does **not** mirror: folder-scoped discovery, external OPA/Cedar
backends and ``inherit``/``scope``/``override``. The context dict handed
to rules is this project's convention (see :func:`event_context`), not
something AGT defines.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..evaluator import Violation
from ..events import ToolCallEvent
from ..monitor import SequenceMonitor
from ..spec import SpecError
from .base import InterceptOutcome, PerCallEngine, PerCallVerdict

_OPERATORS = ("eq", "ne", "gt", "lt", "gte", "lte", "in", "matches", "contains")
_ACTIONS = ("allow", "deny", "audit", "block")
_PERMITTED = ("allow", "audit")


def event_context(event: ToolCallEvent) -> dict[str, Any]:
    """The context dict a per-call rule's ``condition.field`` is looked up in.

    Keys: ``tool_name``, ``agent_id``, ``session_id``, ``resource``,
    ``magnitude``, plus every entry of ``event.attributes``. AGT's own
    integrations build their own context; these names are only this
    fake's convention.
    """
    ctx: dict[str, Any] = {
        "tool_name": event.action,
        "agent_id": event.agent_id,
        "session_id": event.session_id,
        "resource": event.resource,
        "magnitude": event.magnitude,
    }
    ctx.update(event.attributes)
    return ctx


def _match(operator: str, ctx_value: Any, target: Any) -> bool:
    """Mirror of AGT ``_match_condition`` for one operator."""
    if ctx_value is None:
        return False
    if operator == "eq":
        return bool(ctx_value == target)
    if operator == "ne":
        return bool(ctx_value != target)
    if operator == "gt":
        return bool(ctx_value > target)
    if operator == "lt":
        return bool(ctx_value < target)
    if operator == "gte":
        return bool(ctx_value >= target)
    if operator == "lte":
        return bool(ctx_value <= target)
    if operator == "in":
        return bool(ctx_value in target)
    if operator == "contains":
        return bool(target in ctx_value)
    return bool(re.search(str(target), str(ctx_value)))  # "matches"


class FakePerCallEngine:
    """Stateless per-call evaluation of AGT-style ``rules`` (see module doc).

    Args:
        rules: The AGT ``rules`` list (raw YAML mappings).
        default_action: AGT ``defaults.action``; ``allow`` if omitted.

    Raises:
        SpecError: If a rule is malformed; the message carries its path,
            e.g. ``rules[1].condition.operator``.
    """

    def __init__(self, rules: Sequence[Mapping[str, Any]], default_action: str = "allow") -> None:
        if default_action not in _ACTIONS:
            raise SpecError(f"defaults.action: unknown action {default_action!r}")
        self.default_action = default_action
        parsed: list[tuple[int, int, str, str, Any, str, str]] = []
        for i, rule in enumerate(rules):
            path = f"rules[{i}]"
            if not isinstance(rule, Mapping):
                raise SpecError(f"{path}: per-call rule must be a mapping")
            name = rule.get("name")
            if not isinstance(name, str) or not name:
                raise SpecError(f"{path}.name: per-call rule needs a name")
            cond = rule.get("condition")
            if not isinstance(cond, Mapping):
                raise SpecError(f"{path}.condition: rule {name!r}: missing condition mapping")
            field = cond.get("field")
            if not isinstance(field, str):
                raise SpecError(f"{path}.condition.field: rule {name!r}: must be a string")
            op = cond.get("operator")
            if op not in _OPERATORS:
                raise SpecError(
                    f"{path}.condition.operator: rule {name!r}: unknown operator {op!r}"
                )
            if "value" not in cond:
                raise SpecError(f"{path}.condition.value: rule {name!r}: missing value")
            action = rule.get("action")
            if action not in _ACTIONS:
                raise SpecError(f"{path}.action: rule {name!r}: unknown action {action!r}")
            priority = rule.get("priority", 0)
            if isinstance(priority, bool) or not isinstance(priority, int):
                raise SpecError(f"{path}.priority: rule {name!r}: must be an integer")
            message = str(rule.get("message", ""))
            parsed.append((priority, i, name, field, (op, cond["value"]), action, message))
        # AGT: list.sort(key=priority, reverse=True) -- stable, so equal
        # priorities keep file order. Sorting on (-priority, index) is the same.
        parsed.sort(key=lambda r: (-r[0], r[1]))
        self._rules = parsed

    @classmethod
    def from_mapping(cls, doc: Mapping[str, Any]) -> FakePerCallEngine:
        """Build from a parsed AGT policy document (``rules`` + ``defaults``)."""
        rules = doc.get("rules") or []
        if not isinstance(rules, list):
            raise SpecError("rules: 'rules' must be a list")
        defaults = doc.get("defaults") or {}
        if not isinstance(defaults, Mapping):
            raise SpecError("defaults: must be a mapping")
        return cls(rules, str(defaults.get("action", "allow")))

    def check(self, event: ToolCallEvent) -> PerCallVerdict:
        """Evaluate one call against the per-call rules; no history is used.

        Like AGT, an error while matching (e.g. comparing a number with a
        string) fails closed: the call is denied.
        """
        try:
            return self._check(event)
        except Exception as exc:  # noqa: BLE001 -- mirrors AGT's fail-closed catch
            return PerCallVerdict(
                allowed=False, action="deny", reason=f"Policy evaluation error: {exc}"
            )

    def _check(self, event: ToolCallEvent) -> PerCallVerdict:
        ctx = event_context(event)
        for _priority, _i, name, field, (op, value), action, message in self._rules:
            if _match(op, ctx.get(field), value):
                return PerCallVerdict(
                    allowed=action in _PERMITTED,
                    action=action,
                    matched_rule=name,
                    reason=message or f"Matched rule '{name}'",
                )
        return PerCallVerdict(
            allowed=self.default_action in _PERMITTED,
            action=self.default_action,
            reason="No rules matched; default action applied",
        )


Approver = Callable[[str, Sequence[Violation]], bool]
"""Decides an escalation: ``(session_id, violations) -> approved``."""


def _approve_all(session_id: str, violations: Sequence[Violation]) -> bool:
    return True


@dataclass(frozen=True)
class EscalationRecord:
    """One escalation the interceptor handled.

    Attributes:
        session_id: The escalated session.
        rules: Names of the escalating rules.
        approved: The approver's answer.
        auto: True if no approver was configured and the default approved.
    """

    session_id: str
    rules: tuple[str, ...]
    approved: bool
    auto: bool


class FakeAGTInterceptor:
    """A :class:`~seqmon.adapters.CallFeed` that chains a per-call engine
    and a :class:`~seqmon.SequenceMonitor`, the way the real integration
    would.

    For each call: if the session was terminated by the monitor, the call
    is refused; otherwise the per-call engine decides; a permitted call is
    then passed to ``monitor.on_action``. The call that trips a rule has
    already been permitted, so it counts as executed -- the monitor acts on
    the *session*, not on that call. After ``TERMINATE`` every later call
    in the session is refused and the monitor's state for it is dropped
    (only the session id is kept, in ``terminated``, so later calls can be
    refused). On ``ESCALATE`` the ``approver`` is asked; if it refuses, the
    session is treated as terminated; if it approves, the escalating rules
    are re-armed (``SequenceMonitor.approve``), so the next breach asks
    again. Every escalation is recorded in ``escalations``. If no approver
    is given, a default approves everything (matching the replay
    convention in ``bench/score.py``) and the record says ``auto=True``.

    Args:
        per_call: The stateless per-call engine.
        monitor: The sequence monitor.
        approver: Escalation decision callback.
    """

    def __init__(
        self,
        per_call: PerCallEngine,
        monitor: SequenceMonitor,
        approver: Approver | None = None,
    ) -> None:
        self.per_call = per_call
        self.monitor = monitor
        self.auto_approve = approver is None
        self.approver: Approver = approver or _approve_all
        # One id per terminated session: the minimum state needed to keep
        # refusing its calls. Not expired (see WORKLOG 2026-10-06, M2).
        self.terminated: set[str] = set()
        self.escalations: list[EscalationRecord] = []

    @classmethod
    def from_policy_file(
        cls, path: str | Path, approver: Approver | None = None, **monitor_kwargs: Any
    ) -> FakeAGTInterceptor:
        """Build both layers from ONE AGT-style policy file.

        The file's ``rules``/``defaults`` drive the fake per-call engine and
        its ``sequence_rules`` drive the monitor. ``monitor_kwargs`` go to
        ``SequenceMonitor``.
        """
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or "sequence_rules" not in doc:
            raise SpecError(f"{path}: expected an AGT policy file with 'sequence_rules'")
        per_call = FakePerCallEngine.from_mapping(doc)
        monitor = SequenceMonitor.from_file(path, **monitor_kwargs)
        return cls(per_call, monitor, approver=approver)

    def _terminate(self, session_id: str) -> None:
        self.terminated.add(session_id)
        self.monitor.reset(session_id)

    def submit(self, event: ToolCallEvent) -> InterceptOutcome:
        """Run one call through both layers; see the class docstring."""
        if event.session_id in self.terminated:
            return InterceptOutcome(
                event, None, None, executed=False, reason="session terminated"
            )
        verdict = self.per_call.check(event)
        if not verdict.allowed:
            return InterceptOutcome(
                event, verdict, None, executed=False, reason=f"per-call {verdict.action}"
            )
        decision = self.monitor.on_action(event)
        session = event.session_id
        if decision.terminate:
            self._terminate(session)
        elif decision.approval_required:
            pausing = [v for v in decision.violations if v.response.severity == "ESCALATE"]
            approved = self.approver(session, pausing)
            self.escalations.append(
                EscalationRecord(
                    session, tuple(v.rule for v in pausing), approved, self.auto_approve
                )
            )
            if approved:
                self.monitor.approve(session, pausing)
            else:
                self._terminate(session)
        return InterceptOutcome(event, verdict, decision, executed=True)
