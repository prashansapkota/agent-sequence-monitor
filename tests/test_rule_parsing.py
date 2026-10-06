"""Rule parsing: shipped examples load; malformed rules fail with SpecError
whose message starts with the offending field's path."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from seqmon import (
    CumulativeConstraint,
    OrderingConstraint,
    RateConstraint,
    Response,
    ScopeConstraint,
    SpecError,
    load_policy,
    parse_policy,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "policies" / "examples").glob("*.yaml"))


def base(kind, **extra):
    rule = {"name": "r", "type": kind, "window": "1m"}
    rule.update({
        "cumulative": {"threshold": 10},
        "rate": {"max_calls": 3},
        "ordering": {"before": "a", "after": "b"},
        "scope": {"allowed": ["x"]},
    }.get(kind, {}))
    rule.update(extra)
    return rule


def fails(doc, path_prefix):
    with pytest.raises(SpecError) as info:
        parse_policy(doc)
    msg = str(info.value)
    assert msg.startswith(path_prefix), msg
    return msg


# --- valid ---------------------------------------------------------------

def test_examples_directory_is_not_empty():
    assert len(EXAMPLES) >= 4


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_every_example_parses_in_agt_layout(path):
    policy = load_policy(path)
    assert policy.layout == "agt"
    assert policy.rules, f"{path.name} has no sequence rules"
    names = [r.name for r in policy.rules]
    assert len(names) == len(set(names))
    for r in policy.rules:
        assert r.window > 0
        assert isinstance(r.response, Response)


def test_legacy_example_parses_all_four_kinds():
    policy = load_policy(ROOT / "policies" / "example.yaml")
    kinds = {type(r) for r in policy.rules}
    assert kinds == {CumulativeConstraint, RateConstraint, OrderingConstraint, ScopeConstraint}
    assert policy.layout == "standalone"


def test_agt_combined_keeps_per_call_rules_out_of_sequence_rules():
    policy = load_policy(ROOT / "policies" / "examples" / "agt_combined.yaml")
    names = {r.name for r in policy.rules}
    assert "block-shell" not in names and "cap-single-query" not in names
    assert policy.max_events_per_window == 10000


def test_example_values_are_typed_as_documented():
    budget = load_policy(ROOT / "policies" / "examples" / "budget.yaml")
    daily = next(r for r in budget.rules if r.name == "daily-budget-drain")
    assert isinstance(daily, CumulativeConstraint)
    assert daily.window == 86400.0 and daily.threshold == 50.0
    assert daily.attribute == "cost_usd" and daily.response is Response.ESCALATE
    rate = load_policy(ROOT / "policies" / "examples" / "rate.yaml")
    total = next(r for r in rate.rules if r.name == "total-call-rate")
    assert total.actions == () and total.max_calls == 600 and total.window == 600.0


@pytest.mark.parametrize("text, seconds", [
    ("90s", 90), ("5m", 300), ("2h", 7200), ("1d", 86400), ("1.5h", 5400),
    (" 30 s ", 30), (45, 45), (0.25, 0.25),
])
def test_window_formats(text, seconds):
    p = parse_policy({"rules": [base("rate", window=text)]})
    assert p.rules[0].window == pytest.approx(seconds)


@pytest.mark.parametrize("spelling, member", [
    ("warn", Response.WARN), ("pause", Response.PAUSE), ("break", Response.BREAK),
    ("log", Response.LOG), ("escalate", Response.ESCALATE), ("terminate", Response.TERMINATE),
    ("Escalate", Response.PAUSE), ("TERMINATE", Response.BREAK),
])
def test_response_spellings(spelling, member):
    assert parse_policy({"rules": [base("rate", response=spelling)]}).rules[0].response is member


def test_response_defaults_to_warn():
    assert parse_policy({"rules": [base("rate")]}).rules[0].response is Response.WARN


def test_yaml_string_and_path_give_the_same_policy(tmp_path):
    text = "rules:\n  - {name: r, type: rate, max_calls: 3, window: 1m}\n"
    f = tmp_path / "p.yaml"
    f.write_text(text)
    assert load_policy(text).rules == load_policy(f).rules == load_policy(str(f)).rules


# --- invalid: missing fields -----------------------------------------------

@pytest.mark.parametrize("kind, field", [
    ("cumulative", "threshold"), ("cumulative", "window"), ("cumulative", "type"),
    ("rate", "max_calls"), ("rate", "window"),
    ("ordering", "before"), ("ordering", "after"), ("ordering", "window"),
    ("scope", "allowed"), ("scope", "window"),
])
def test_missing_field_reports_its_path(kind, field):
    rule = base(kind)
    del rule[field]
    msg = fails({"rules": [base("rate", name="ok"), rule]}, f"rules[1].{field}:")
    assert "missing required field" in msg


def test_missing_field_path_uses_sequence_rules_section_in_agt_layout():
    rule = base("rate")
    del rule["max_calls"]
    fails({"rules": [], "sequence_rules": [rule]}, "sequence_rules[0].max_calls:")


# --- invalid: wrong types ----------------------------------------------------

@pytest.mark.parametrize("kind, field, value", [
    ("cumulative", "threshold", "100"),
    ("cumulative", "threshold", True),
    ("cumulative", "threshold", [1]),
    ("cumulative", "threshold", None),
    ("rate", "max_calls", "5"),
    ("rate", "max_calls", 2.5),
    ("rate", "max_calls", False),
    ("scope", "max_outside", "1"),
    ("scope", "allowed", 5),
    ("scope", "allowed", {"a": 1}),
    ("ordering", "before", 3),
    ("ordering", "after", ""),
    ("rate", "actions", 7),
    ("rate", "message", 12),
    ("cumulative", "attribute", 3),
    ("cumulative", "aggregate", "avg"),
    ("rate", "response", "kill"),
    ("rate", "response", None),
    ("rate", "window", True),
    ("rate", "window", "soon"),
    ("rate", "window", [1]),
])
def test_wrong_type_reports_its_path(kind, field, value):
    fails({"rules": [base(kind, **{field: value})]}, f"rules[0].{field}")


def test_non_string_list_item_reports_item_path():
    fails({"rules": [base("scope", allowed=["ok", None])]}, "rules[0].allowed[1]:")


def test_rule_that_is_not_a_mapping():
    fails({"rules": [base("rate"), "oops"]}, "rules[1]:")


def test_rules_that_is_not_a_list():
    fails({"rules": {"name": "r"}}, "rules:")


def test_non_string_name():
    fails({"rules": [base("rate", name=["x"])]}, "rules[0].name:")


# --- invalid: negative / zero windows and numbers --------------------------

@pytest.mark.parametrize("window", [0, 0.0, -1, "0s", "-5m", "0m", float("nan"),
                                    float("inf"), "1y", "5 minutes", ""])
def test_non_positive_or_bad_window(window):
    fails({"rules": [base("rate", window=window)]}, "rules[0].window:")


@pytest.mark.parametrize("kind, field", [
    ("cumulative", "threshold"), ("rate", "max_calls"), ("scope", "max_outside"),
])
def test_negative_numbers_rejected(kind, field):
    fails({"rules": [base(kind, **{field: -1})]}, f"rules[0].{field}:")


@pytest.mark.parametrize("kind, field", [
    ("cumulative", "threshold"), ("rate", "max_calls"), ("scope", "max_outside"),
])
def test_zero_numbers_are_allowed(kind, field):
    assert parse_policy({"rules": [base(kind, **{field: 0})]}).rules


# --- invalid: unknown type / keys ---------------------------------------------

@pytest.mark.parametrize("kind", ["sequence", "Cumulative", "budget", "", None, 3])
def test_unknown_constraint_type(kind):
    msg = fails({"rules": [{"name": "r", "type": kind, "window": "1m"}]}, "rules[0].type:")
    assert "unknown type" in msg


@pytest.mark.parametrize("kind, typo", [
    ("cumulative", "treshold"), ("rate", "max_call"), ("scope", "alowed"),
    ("ordering", "befor"), ("rate", "windows"),
])
def test_unknown_key_reports_its_path(kind, typo):
    fails({"rules": [base(kind, **{typo: 1})]}, f"rules[0].{typo}:")


def test_attribute_only_with_sum():
    fails({"rules": [base("cumulative", aggregate="count", attribute="rows")]},
          "rules[0].attribute:")


def test_duplicate_names_report_the_second_index():
    fails({"rules": [base("rate"), base("cumulative")]}, "rules[1].name:")


def test_per_call_rule_in_standalone_rules_is_explained():
    msg = fails({"rules": [{"name": "x", "condition": {}, "action": "deny"}]}, "rules[0]")
    assert "sequence_rules" in msg


@pytest.mark.parametrize("value", [0, -3, True, "10", 1.5])
def test_bad_max_events_per_window(value):
    fails({"sequence_defaults": {"max_events_per_window": value}, "rules": []},
          "sequence_defaults.max_events_per_window:")


def test_unknown_sequence_defaults_key():
    fails({"sequence_defaults": {"max_events": 3}, "rules": []}, "sequence_defaults.max_events:")


def test_missing_file_is_a_spec_error():
    with pytest.raises(SpecError, match="not found"):
        load_policy(ROOT / "policies" / "no_such_file.yaml")


def test_yaml_syntax_error_is_a_spec_error():
    with pytest.raises(SpecError, match=re.escape("<root>")):
        load_policy("rules:\n  - {name: r, type: rate\n")


def test_very_long_yaml_document_is_not_probed_as_a_path():
    rules = "".join(
        f"  - {{name: r{i}, type: rate, max_calls: 1, window: 1m}}\n" for i in range(200)
    )
    assert len(load_policy("rules:\n" + rules).rules) == 200


# --- documented-as-bug parsing holes (BUGS.md) ---------------------------------

@pytest.mark.xfail(strict=True, reason="BUG-003: falsy rule name silently replaced by 'rule_<i>'")
@pytest.mark.parametrize("name", [0, "", False])
def test_falsy_non_string_or_empty_name_is_rejected(name):
    fails({"rules": [base("rate", name=name)]}, "rules[0].name:")


@pytest.mark.xfail(strict=True, reason="BUG-004: 'LOG' is accepted but 'WARN'/'PAUSE'/'BREAK' "
                                       "are rejected (case handled only for severity names)")
@pytest.mark.parametrize("spelling", ["WARN", "Pause", "BREAK"])
def test_verb_spellings_are_case_insensitive_like_severity_names(spelling):
    assert parse_policy({"rules": [base("rate", response=spelling)]}).rules


@pytest.mark.xfail(strict=True, reason="BUG-005: actions: '' (empty string) silently means "
                                       "'every action' instead of being rejected")
def test_empty_string_actions_is_rejected():
    fails({"rules": [base("rate", actions="")]}, "rules[0].actions:")
