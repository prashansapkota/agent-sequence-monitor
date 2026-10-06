"""Rule specification: YAML sequence policies.

Two document layouts are accepted (see ``docs/rule_format.md``):

**Embedded in an AGT policy file.** Sequence rules live under a top-level
``sequence_rules:`` list in the *same* file as the Agent-OS per-call
``rules:``. seqmon reads only ``sequence_rules`` (and the optional
``sequence_defaults``); the per-call ``rules`` belong to AGT and are left
alone. AGT's pydantic ``PolicyDocument`` loader does not forbid extra
keys, so the per-call engine still loads such a file -- see
``OPEN_QUESTIONS.md`` for the caveat about AGT's stricter JSON schema.

**Standalone** (the original format). A document with no
``sequence_rules`` key whose ``rules:`` list holds sequence rules. Kept so
existing policies and tests keep working.

Both layouts share the Agent-OS top-level fields (``version`` / ``name`` /
``description``). Response vocabulary reuses the A2A conversation policy
verbs -- ``warn`` / ``pause`` / ``break`` -- and also accepts the project's
severity names ``log`` / ``escalate`` / ``terminate`` as synonyms.

Four constraint kinds are supported:

``cumulative``
    An aggregate of ``magnitude`` (or a named numeric ``attribute``, or of
    call counts, or of distinct resources) over a sliding window, compared
    against ``threshold``.
``rate``
    Calls per unit time -- a count over a window, kept distinct because the
    intent and the reported message differ.
``ordering``
    A forbidden ordered pair: ``before`` must not be followed by ``after``
    within the window. Expresses "do not send externally shortly after
    reading customer records".
``scope``
    Resources touched must stay within a declared allowlist. See the note
    in ``ScopeConstraint`` about why this is defined mechanically.

Every validation error is a :class:`SpecError` whose message starts with
the field path, e.g. ``sequence_rules[2].window: ...``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal, TypeVar

import yaml


class Response(str, Enum):
    """What to do when a sequence constraint is violated.

    The canonical values are AGT's A2A conversation-policy verbs. The
    project's severity names are aliases of the same members:

    =============  ===========  =================================
    severity       value        meaning
    =============  ===========  =================================
    ``LOG``        ``warn``     record and continue
    ``ESCALATE``   ``pause``    human approval before next action
    ``TERMINATE``  ``break``    end the session
    =============  ===========  =================================

    ``Response.LOG is Response.WARN`` holds, and ``Response("escalate")``
    returns ``Response.PAUSE``.
    """

    WARN = "warn"
    PAUSE = "pause"
    BREAK = "break"
    # Aliases (same members, second name).
    LOG = "warn"
    ESCALATE = "pause"
    TERMINATE = "break"

    @classmethod
    def _missing_(cls, value: object) -> Response | None:
        """Accept the severity spellings ``log`` / ``escalate`` / ``terminate``."""
        if isinstance(value, str):
            return _SEVERITY_SPELLINGS.get(value.lower())
        return None

    @property
    def severity(self) -> str:
        """The project's severity name: ``LOG``, ``ESCALATE`` or ``TERMINATE``."""
        return _SEVERITY_NAMES[self]


_SEVERITY_SPELLINGS: dict[str, Response] = {
    "log": Response.WARN,
    "escalate": Response.PAUSE,
    "terminate": Response.BREAK,
}
_SEVERITY_NAMES: dict[Response, str] = {
    Response.WARN: "LOG",
    Response.PAUSE: "ESCALATE",
    Response.BREAK: "TERMINATE",
}


class Aggregate(str, Enum):
    """How a cumulative constraint combines events."""

    SUM = "sum"  # total of event.magnitude (or event.attributes[attribute])
    COUNT = "count"  # number of matching events
    DISTINCT = "distinct"  # number of distinct event.resource values


class SpecError(ValueError):
    """Raised when a policy document is malformed.

    The message starts with the path of the offending field, e.g.
    ``sequence_rules[2].window: rule 'r': bad duration 'soon'``.
    """


_DURATION = re.compile(r"^(\d+(?:\.\d+)?)\s*(s|m|h|d)$")
_UNITS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}

DEFAULT_MAX_EVENTS_PER_WINDOW = 10_000
"""Per-rule window cap used when neither the policy nor the caller sets one."""


def parse_duration(text: str | int | float) -> float:
    """Parse ``"30m"`` / ``"1h"`` / ``"90s"`` into seconds.

    Bare numbers are accepted and treated as seconds.

    Raises:
        SpecError: If the duration is unparseable or non-positive.
    """
    if isinstance(text, bool):
        raise SpecError(f"bad duration {text!r}; expected e.g. '30s', '5m', '2h', '1d'")
    if isinstance(text, (int, float)):
        seconds = float(text)
    else:
        match = _DURATION.match(str(text).strip())
        if not match:
            raise SpecError(f"bad duration {text!r}; expected e.g. '30s', '5m', '2h', '1d'")
        seconds = float(match.group(1)) * _UNITS[match.group(2)]
    if not math.isfinite(seconds) or seconds <= 0:
        raise SpecError(f"duration must be positive and finite, got {text!r}")
    return seconds


@dataclass(frozen=True)
class _Base:
    """Fields shared by every constraint kind.

    Attributes:
        name: Unique rule name; used as the state key and in alerts.
        window: Sliding-window span in seconds.
        response: What to do on violation.
        message: Human-readable explanation reported with the violation.
        actions: Tool names the rule tracks; empty means every action.
    """

    name: str
    window: float
    response: Response
    message: str = ""
    actions: tuple[str, ...] = ()

    def matches_action(self, action: str) -> bool:
        """Whether this constraint tracks the given action."""
        return not self.actions or action in self.actions


@dataclass(frozen=True)
class CumulativeConstraint(_Base):
    """Aggregate of magnitude/attribute/count/distinct-resources over a window.

    Attributes:
        threshold: The rule fires when the aggregate exceeds this value.
        aggregate: ``sum``, ``count`` or ``distinct``.
        attribute: For ``sum`` only: name of the event attribute to sum
            (``event.attributes[attribute]``). ``None`` sums
            ``event.magnitude``.
    """

    threshold: float = 0.0
    aggregate: Aggregate = Aggregate.SUM
    attribute: str | None = None


@dataclass(frozen=True)
class RateConstraint(_Base):
    """Maximum number of matching calls per window.

    Attributes:
        max_calls: The rule fires on call number ``max_calls + 1`` within
            one window.
    """

    max_calls: int = 0


@dataclass(frozen=True)
class OrderingConstraint(_Base):
    """``after`` must not occur following ``before`` within the window.

    Named from the violating agent's perspective: the violation is
    "``after`` happened after ``before`` happened". The shared ``actions``
    filter is ignored; the two actions are named here.

    Attributes:
        before: The enabling action, e.g. ``customer_db.query``.
        after: The action forbidden while ``before`` is in the window.
    """

    before: str = ""
    after: str = ""


@dataclass(frozen=True)
class ScopeConstraint(_Base):
    """Resources touched must stay inside a declared allowlist.

    Scope drift is the least crisply definable of the four constraints.
    This defines it mechanically and falsifiably as *declared-scope
    escape*: the session declares its resource set up front, and any
    access outside that set is drift. ``max_outside`` allows a tolerance
    before the response fires, so a single stray access is distinguishable
    from progressive expansion.

    The tolerance applies per ``window``: the rule fires when more than
    ``max_outside`` out-of-scope accesses fall inside one window. A long
    session's occasional strays therefore do not add up forever -- and,
    symmetrically, drift paced slower than the window is not caught.

    This deliberately does not attempt semantic notions of "related to the
    original task" -- that would require a definition of task similarity
    the evaluation could not falsify.

    Attributes:
        allowed: The declared resource allowlist (exact matches).
        max_outside: Out-of-scope accesses tolerated per window.
    """

    allowed: tuple[str, ...] = ()
    max_outside: int = 0


Constraint = CumulativeConstraint | RateConstraint | OrderingConstraint | ScopeConstraint
"""Union of the four rule types."""


@dataclass
class SequencePolicy:
    """A parsed sequence policy document.

    Attributes:
        name: Policy name (AGT ``name``).
        version: Policy version string (AGT ``version``).
        description: Free text (AGT ``description``).
        rules: The sequence rules, in file order.
        max_events_per_window: Per-rule window cap from
            ``sequence_defaults.max_events_per_window``; ``None`` if the
            file does not set one.
        layout: ``"agt"`` if the rules came from ``sequence_rules`` in an
            AGT policy file, ``"standalone"`` if from a sequence-only
            ``rules`` list.
    """

    name: str = "unnamed"
    version: str = "1.0"
    description: str = ""
    rules: list[Constraint] = field(default_factory=list)
    max_events_per_window: int | None = None
    layout: Literal["agt", "standalone"] = "standalone"


_T = TypeVar("_T", int, float)


def _require(raw: Mapping[str, Any], key: str, rule_name: str, path: str) -> Any:
    if key not in raw:
        raise SpecError(f"{path}.{key}: rule {rule_name!r}: missing required field {key!r}")
    return raw[key]


def _number(
    raw: Mapping[str, Any],
    key: str,
    rule_name: str,
    path: str,
    cast: type[_T],
    default: _T | None = None,
) -> _T:
    """Read a numeric field, reporting bad values as ``SpecError``.

    The field is required unless ``default`` is given.

    The value must be a YAML number (not a string or a boolean), finite
    and non-negative; an ``int`` field must be a whole number. Each of
    these used to slip through: ``max_calls: "5"`` and ``max_calls: 2.9``
    were coerced, and ``threshold: .nan`` produced a rule that never fires.
    """
    if default is None:
        value = _require(raw, key, rule_name, path)
    else:
        value = raw.get(key, default)
    where = f"{path}.{key}: rule {rule_name!r}"
    # bool is an int subclass, so ``max_calls: true`` would silently become 1.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpecError(f"{where}: field {key!r} must be a number, got {value!r}")
    if not math.isfinite(value) or value < 0:
        raise SpecError(
            f"{where}: field {key!r} must be a finite number >= 0, got {value!r}"
        )
    if cast is int and not isinstance(value, int):
        raise SpecError(f"{where}: field {key!r} must be a whole number, got {value!r}")
    return cast(value)


def _str_list(value: Any, key: str, rule_name: str, path: str) -> list[str]:
    """Accept a single string or a list of strings."""
    where = f"{path}.{key}: rule {rule_name!r}"
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        raise SpecError(f"{where}: field {key!r} must be a string or a list, got {value!r}")
    # Coercing with str() would turn ``allowed: [null]`` into the resource
    # "None" -- a silently wrong allowlist rather than a reported error.
    for i, v in enumerate(value):
        if not isinstance(v, str):
            raise SpecError(
                f"{path}.{key}[{i}]: rule {rule_name!r}: field {key!r} must contain "
                f"only strings, got {v!r}"
            )
    return list(value)


def _string(raw: Mapping[str, Any], key: str, rule_name: str, path: str) -> str:
    value = _require(raw, key, rule_name, path)
    if not isinstance(value, str) or not value:
        raise SpecError(
            f"{path}.{key}: rule {rule_name!r}: field {key!r} must be a non-empty "
            f"string, got {value!r}"
        )
    return value


_COMMON_KEYS = frozenset({"name", "type", "window", "response", "message", "actions"})
_TYPE_KEYS: dict[str, frozenset[str]] = {
    "cumulative": _COMMON_KEYS | {"threshold", "aggregate", "attribute"},
    "rate": _COMMON_KEYS | {"max_calls"},
    "ordering": _COMMON_KEYS | {"before", "after"},
    "scope": _COMMON_KEYS | {"allowed", "max_outside"},
}
_AGT_VERDICTS = frozenset({"allow", "deny", "audit", "block"})


def _check_keys(raw: Mapping[str, Any], kind: Any, name: str, path: str) -> None:
    """Reject keys the rule type does not define.

    A typo such as ``attrbute:`` or ``max_outsde:`` used to be ignored,
    leaving a rule that silently did something else (or nothing).
    ``action:`` gets its own message: in AGT's per-call ``rules`` it is the
    verdict (``allow``/``deny``/...), so it is not accepted here as a
    tool-name filter.
    """
    if "action" in raw:
        value = raw["action"]
        hint = (
            f"{value!r} is an AGT per-call verdict; a sequence rule's outcome is "
            "set with 'response:' (warn/pause/break or log/escalate/terminate)"
            if isinstance(value, str) and value.lower() in _AGT_VERDICTS
            else "use 'actions:' for the tool-name filter"
        )
        raise SpecError(
            f"{path}.action: rule {name!r}: 'action' is not a sequence-rule field; {hint}"
        )
    allowed = _TYPE_KEYS.get(kind) if isinstance(kind, str) else None
    if allowed is None:
        return  # unknown type is reported by the caller
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise SpecError(
            f"{path}.{unknown[0]}: rule {name!r}: unknown field {unknown[0]!r} for "
            f"type {kind!r}; allowed fields: {sorted(allowed)}"
        )


def _build_rule(raw: Any, index: int, section: str = "rules") -> Constraint:
    """Build one constraint from its mapping at ``section[index]``."""
    path = f"{section}[{index}]"
    if not isinstance(raw, dict):
        raise SpecError(f"{path}: rule at index {index} must be a mapping")

    name = raw.get("name") or f"rule_{index}"
    if not isinstance(name, str):
        raise SpecError(f"{path}.name: rule name must be a string, got {name!r}")

    if "type" not in raw and "condition" in raw:
        raise SpecError(
            f"{path}: rule {name!r} looks like an AGT per-call rule (it has "
            "'condition'); put sequence rules under 'sequence_rules:'"
        )
    kind = _require(raw, "type", name, path)
    _check_keys(raw, kind, name, path)

    try:
        response = Response(raw.get("response", "warn"))
    except ValueError:
        raise SpecError(
            f"{path}.response: rule {name!r}: unknown response "
            f"{raw.get('response')!r}; expected one of "
            f"{[r.value for r in Response]} or {sorted(_SEVERITY_SPELLINGS)}"
        ) from None

    actions = _str_list(raw.get("actions") or [], "actions", name, path)

    message = raw.get("message", "")
    if not isinstance(message, str):
        raise SpecError(f"{path}.message: rule {name!r}: message must be a string")

    try:
        window = parse_duration(_require(raw, "window", name, path))
    except SpecError as exc:
        if str(exc).startswith(f"{path}."):
            raise
        raise SpecError(f"{path}.window: rule {name!r}: {exc}") from None

    common: dict[str, Any] = {
        "name": name,
        "window": window,
        "response": response,
        "message": message,
        "actions": tuple(actions),
    }

    if kind == "cumulative":
        try:
            aggregate = Aggregate(raw.get("aggregate", "sum"))
        except ValueError:
            raise SpecError(
                f"{path}.aggregate: rule {name!r}: unknown aggregate {raw.get('aggregate')!r}"
            ) from None
        attribute = raw.get("attribute")
        if attribute is not None:
            if not isinstance(attribute, str) or not attribute:
                raise SpecError(
                    f"{path}.attribute: rule {name!r}: attribute must be a non-empty "
                    f"string, got {attribute!r}"
                )
            if aggregate is not Aggregate.SUM:
                raise SpecError(
                    f"{path}.attribute: rule {name!r}: 'attribute' only applies to "
                    f"aggregate 'sum', not {aggregate.value!r}"
                )
        return CumulativeConstraint(
            **common,
            threshold=_number(raw, "threshold", name, path, float),
            aggregate=aggregate,
            attribute=attribute,
        )

    if kind == "rate":
        return RateConstraint(**common, max_calls=_number(raw, "max_calls", name, path, int))

    if kind == "ordering":
        before = _string(raw, "before", name, path)
        after = _string(raw, "after", name, path)
        if before == after:
            raise SpecError(
                f"{path}.after: rule {name!r}: 'before' and 'after' are both "
                f"{before!r}; such a rule can never fire"
            )
        return OrderingConstraint(**common, before=before, after=after)

    if kind == "scope":
        allowed = _str_list(_require(raw, "allowed", name, path), "allowed", name, path)
        return ScopeConstraint(
            **common,
            allowed=tuple(allowed),
            max_outside=_number(raw, "max_outside", name, path, int, default=0),
        )

    raise SpecError(
        f"{path}.type: rule {name!r}: unknown type {kind!r}; expected one of "
        "'cumulative', 'rate', 'ordering', 'scope'"
    )


_STANDALONE_TOP_LEVEL = frozenset(
    {"version", "name", "description", "rules", "sequence_defaults"}
)


def _parse_defaults(raw: Any) -> int | None:
    """Parse the optional ``sequence_defaults`` mapping."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise SpecError("sequence_defaults: must be a mapping")
    unknown = sorted(set(raw) - {"max_events_per_window"})
    if unknown:
        raise SpecError(f"sequence_defaults.{unknown[0]}: unknown field")
    if "max_events_per_window" not in raw:
        return None
    value = raw["max_events_per_window"]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SpecError(
            "sequence_defaults.max_events_per_window: must be a positive integer, "
            f"got {value!r}"
        )
    return value


def parse_policy(raw: Mapping[str, Any]) -> SequencePolicy:
    """Build a :class:`SequencePolicy` from an already-parsed YAML mapping.

    If ``raw`` has a ``sequence_rules`` key it is treated as an AGT policy
    file and only ``sequence_rules`` is read; otherwise ``rules`` is read
    as a standalone sequence-rule list.

    Raises:
        SpecError: If the document is malformed. The message starts with
            the offending field's path.
    """
    if not isinstance(raw, Mapping):
        raise SpecError("<root>: policy document must be a YAML mapping")

    layout: Literal["agt", "standalone"]
    if "sequence_rules" in raw:
        section, layout = "sequence_rules", "agt"
    else:
        section, layout = "rules", "standalone"
        # In the AGT layout unknown top-level keys belong to AGT; in the
        # standalone layout the whole file is ours, so a typo is an error.
        unknown = sorted(str(k) for k in set(raw) - _STANDALONE_TOP_LEVEL)
        if unknown:
            raise SpecError(
                f"{unknown[0]}: unknown top-level field in a standalone sequence "
                f"policy; allowed: {', '.join(sorted(_STANDALONE_TOP_LEVEL))}"
            )

    rules_raw = raw.get(section)
    if rules_raw is None:
        rules_raw = []
    if not isinstance(rules_raw, list):
        raise SpecError(f"{section}: '{section}' must be a list")

    policy = SequencePolicy(
        name=str(raw.get("name", "unnamed")),
        version=str(raw.get("version", "1.0")),
        description=str(raw.get("description", "") or ""),
        rules=[_build_rule(r, i, section) for i, r in enumerate(rules_raw)],
        max_events_per_window=_parse_defaults(raw.get("sequence_defaults")),
        layout=layout,
    )

    seen: set[str] = set()
    for i, rule in enumerate(policy.rules):
        if rule.name in seen:
            raise SpecError(f"{section}[{i}].name: duplicate rule name {rule.name!r}")
        seen.add(rule.name)

    return policy


def load_policy(source: str | Path) -> SequencePolicy:
    """Load a sequence policy from a YAML file path or a YAML string.

    Accepts both an AGT policy file with a ``sequence_rules`` section and
    the standalone sequence-only format.

    Raises:
        SpecError: If the file is missing or the document is malformed.
    """
    text = str(source)
    # Only a single line can be a path. Probing a multi-line YAML document
    # with Path.exists() raises OSError ("File name too long") once the
    # document passes the OS filename limit.
    looks_like_path = isinstance(source, Path) or (
        "\n" not in text and not text.lstrip().startswith(("-", "{"))
    )
    if looks_like_path:
        path = Path(source)
        try:
            is_file = path.is_file()
        except OSError:
            is_file = False
        if is_file:
            text = path.read_text(encoding="utf-8")
        elif path.suffix in (".yaml", ".yml"):
            # Otherwise parsed as a one-word YAML string and reported as
            # "must be a YAML mapping" -- misleading for a typo'd path.
            raise SpecError(f"policy file not found: {source}")

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SpecError(f"<root>: YAML syntax error: {exc}") from None
    if not isinstance(raw, dict):
        raise SpecError("<root>: policy document must be a YAML mapping")
    return parse_policy(raw)
