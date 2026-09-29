"""Rule specification: YAML sequence policies.

Document layout intentionally mirrors the Agent-OS policy document
(``version`` / ``name`` / ``description`` / ``rules``) so a sequence policy
can sit alongside a per-call policy, or eventually inside one under a
``sequence_rules`` key, without a second file format to learn.

Response vocabulary reuses the toolkit's A2A conversation policy verbs --
``warn`` / ``pause`` / ``break`` -- rather than inventing new ones. ``pause``
is the human-approval escalation; ``break`` terminates the session.

Four constraint kinds are supported:

``cumulative``
    An aggregate of ``magnitude`` (or of call counts, when ``aggregate:
    count``) over a sliding window, compared against ``threshold``.
``rate``
    Calls per unit time -- a cumulative count constraint with the window
    expressed as the rate denominator. Kept distinct because the intent
    and the reported message differ.
``ordering``
    A forbidden ordered pair: ``before`` must not be followed by ``after``
    within the window. Expresses "do not send externally shortly after
    reading customer records".
``scope``
    Resources touched must stay within a declared allowlist. See the note
    in ``ScopeConstraint`` about why this is defined mechanically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class Response(str, Enum):
    """What to do when a sequence constraint is violated."""

    WARN = "warn"      # log and continue
    PAUSE = "pause"    # require human approval before proceeding
    BREAK = "break"    # terminate the session


class Aggregate(str, Enum):
    """How a cumulative constraint combines events."""

    SUM = "sum"        # total of event.magnitude
    COUNT = "count"    # number of matching events
    DISTINCT = "distinct"  # number of distinct event.resource values


class SpecError(ValueError):
    """Raised when a policy document is malformed."""


_DURATION = re.compile(r"^(\d+(?:\.\d+)?)\s*(s|m|h|d)$")
_UNITS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}


def parse_duration(text: str | int | float) -> float:
    """Parse ``"30m"`` / ``"1h"`` / ``"90s"`` into seconds.

    Bare numbers are accepted and treated as seconds.

    Raises:
        SpecError: If the duration is unparseable or non-positive.
    """
    if isinstance(text, (int, float)):
        seconds = float(text)
    else:
        match = _DURATION.match(str(text).strip())
        if not match:
            raise SpecError(
                f"bad duration {text!r}; expected e.g. '30s', '5m', '2h', '1d'"
            )
        seconds = float(match.group(1)) * _UNITS[match.group(2)]
    if seconds <= 0:
        raise SpecError(f"duration must be positive, got {text!r}")
    return seconds


@dataclass(frozen=True)
class _Base:
    """Fields shared by every constraint kind."""

    name: str
    window: float           # seconds
    response: Response
    message: str = ""
    actions: tuple[str, ...] = ()   # empty = match any action

    def matches_action(self, action: str) -> bool:
        """Whether this constraint tracks the given action."""
        return not self.actions or action in self.actions


@dataclass(frozen=True)
class CumulativeConstraint(_Base):
    """Aggregate of magnitude/count/distinct-resources over a window."""

    threshold: float = 0.0
    aggregate: Aggregate = Aggregate.SUM


@dataclass(frozen=True)
class RateConstraint(_Base):
    """Maximum number of matching calls per window."""

    max_calls: int = 0


@dataclass(frozen=True)
class OrderingConstraint(_Base):
    """``after`` must not occur following ``before`` within the window.

    Named from the violating agent's perspective: the violation is
    "``after`` happened after ``before`` happened".
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
    """

    allowed: tuple[str, ...] = ()
    max_outside: int = 0


Constraint = (
    CumulativeConstraint | RateConstraint | OrderingConstraint | ScopeConstraint
)


@dataclass
class SequencePolicy:
    """A parsed sequence policy document."""

    name: str = "unnamed"
    version: str = "1.0"
    description: str = ""
    rules: list[Constraint] = field(default_factory=list)


def _require(raw: dict[str, Any], key: str, rule_name: str) -> Any:
    if key not in raw:
        raise SpecError(f"rule {rule_name!r}: missing required field {key!r}")
    return raw[key]


def _number(
    raw: dict[str, Any], key: str, rule_name: str, cast: type, default: Any = None
) -> Any:
    """Read a numeric field, reporting bad values as ``SpecError``.

    The field is required unless ``default`` is given.

    Without this a typo such as ``max_calls: lots`` escapes as a bare
    ``ValueError`` from ``int()``, breaking the documented contract that
    malformed documents raise ``SpecError``.
    """
    value = _require(raw, key, rule_name) if default is None else raw.get(key, default)
    # bool is an int subclass, so ``max_calls: true`` would silently become 1.
    if isinstance(value, bool):
        raise SpecError(
            f"rule {rule_name!r}: field {key!r} must be a number, got {value!r}"
        )
    try:
        return cast(value)
    except (TypeError, ValueError):
        raise SpecError(
            f"rule {rule_name!r}: field {key!r} must be a number, got {value!r}"
        ) from None


def _str_list(value: Any, key: str, rule_name: str) -> list[str]:
    """Accept a single string or a list of strings."""
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        raise SpecError(
            f"rule {rule_name!r}: field {key!r} must be a string or a list, "
            f"got {value!r}"
        )
    # Coercing with str() would turn ``allowed: [null]`` into the resource
    # "None" -- a silently wrong allowlist rather than a reported error.
    bad = [v for v in value if not isinstance(v, str)]
    if bad:
        raise SpecError(
            f"rule {rule_name!r}: field {key!r} must contain only strings, "
            f"got {bad[0]!r}"
        )
    return list(value)


def _build_rule(raw: dict[str, Any], index: int) -> Constraint:
    if not isinstance(raw, dict):
        raise SpecError(f"rule at index {index} must be a mapping")

    name = raw.get("name") or f"rule_{index}"
    kind = _require(raw, "type", name)

    try:
        response = Response(raw.get("response", "warn"))
    except ValueError:
        raise SpecError(
            f"rule {name!r}: unknown response {raw.get('response')!r}; "
            f"expected one of {[r.value for r in Response]}"
        ) from None

    actions = raw.get("actions") or ([raw["action"]] if "action" in raw else [])
    actions = _str_list(actions, "actions", name)

    common = {
        "name": name,
        "window": parse_duration(_require(raw, "window", name)),
        "response": response,
        "message": raw.get("message", ""),
        "actions": tuple(actions),
    }

    if kind == "cumulative":
        try:
            aggregate = Aggregate(raw.get("aggregate", "sum"))
        except ValueError:
            raise SpecError(
                f"rule {name!r}: unknown aggregate {raw.get('aggregate')!r}"
            ) from None
        return CumulativeConstraint(
            **common,
            threshold=_number(raw, "threshold", name, float),
            aggregate=aggregate,
        )

    if kind == "rate":
        return RateConstraint(
            **common, max_calls=_number(raw, "max_calls", name, int)
        )

    if kind == "ordering":
        return OrderingConstraint(
            **common,
            before=str(_require(raw, "before", name)),
            after=str(_require(raw, "after", name)),
        )

    if kind == "scope":
        allowed = _str_list(_require(raw, "allowed", name), "allowed", name)
        return ScopeConstraint(
            **common,
            allowed=tuple(allowed),
            max_outside=_number(raw, "max_outside", name, int, default=0),
        )

    raise SpecError(
        f"rule {name!r}: unknown type {kind!r}; expected one of "
        "'cumulative', 'rate', 'ordering', 'scope'"
    )


def load_policy(source: str | Path) -> SequencePolicy:
    """Load a sequence policy from a YAML file path or a YAML string.

    Raises:
        SpecError: If the document is malformed.
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

    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise SpecError("policy document must be a YAML mapping")

    rules_raw = raw.get("rules", [])
    if not isinstance(rules_raw, list):
        raise SpecError("'rules' must be a list")

    policy = SequencePolicy(
        name=raw.get("name", "unnamed"),
        version=str(raw.get("version", "1.0")),
        description=raw.get("description", ""),
        rules=[_build_rule(r, i) for i, r in enumerate(rules_raw)],
    )

    seen: set[str] = set()
    for rule in policy.rules:
        if rule.name in seen:
            raise SpecError(f"duplicate rule name {rule.name!r}")
        seen.add(rule.name)

    return policy
