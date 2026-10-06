"""Property tests: the incremental evaluator equals a naive full-history
reference (``tests/naive_reference.py``) on random traces and random policies.

Magnitudes and attribute values are drawn as whole numbers so sums are exact
in floating point; the float-rounding cases are covered separately in
``test_cumulative.py`` (BUGS.md BUG-001/002). Traces stay well under the
per-window backstop cap, which the reference does not model.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from naive_reference import NaiveReference

from seqmon import (
    Aggregate,
    CumulativeConstraint,
    IncrementalEvaluator,
    OrderingConstraint,
    RateConstraint,
    Response,
    ScopeConstraint,
    SequencePolicy,
    ToolCallEvent,
)

ACTIONS = ("db.query", "files.read", "send", "charge")
RESOURCES = (None, "t1", "t2", "t3", "t4")
SESSIONS = ("s1", "s2", "s3")
SETTINGS = settings(
    max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)

windows = st.sampled_from([1.0, 5.0, 10.0, 30.0, 60.0, 300.0])
action_filters = st.lists(st.sampled_from(ACTIONS), max_size=2, unique=True).map(tuple)


@st.composite
def rule(draw, index):
    name = f"r{index}"
    kind = draw(st.sampled_from(["cumulative", "rate", "ordering", "scope"]))
    common = dict(name=name, window=draw(windows), response=Response.WARN)
    if kind == "cumulative":
        agg = draw(st.sampled_from(list(Aggregate)))
        attr = draw(st.sampled_from([None, "rows"])) if agg is Aggregate.SUM else None
        return CumulativeConstraint(
            **common, actions=draw(action_filters),
            threshold=float(draw(st.integers(0, 40))), aggregate=agg, attribute=attr,
        )
    if kind == "rate":
        return RateConstraint(
            **common, actions=draw(action_filters), max_calls=draw(st.integers(0, 6))
        )
    if kind == "ordering":
        before, after = draw(st.lists(st.sampled_from(ACTIONS), min_size=2, max_size=2,
                                      unique=True))
        return OrderingConstraint(**common, before=before, after=after)
    return ScopeConstraint(
        **common, actions=draw(action_filters),
        allowed=tuple(draw(st.lists(st.sampled_from(RESOURCES[1:]), max_size=2,
                                    unique=True))),
        max_outside=draw(st.integers(0, 3)),
    )


@st.composite
def policies(draw):
    n = draw(st.integers(1, 4))
    return SequencePolicy(name="p", rules=[draw(rule(i)) for i in range(n)])


@st.composite
def traces(draw, ordered=True, max_size=120):
    n = draw(st.integers(0, max_size))
    t = 0.0
    out = []
    for _ in range(n):
        if ordered:
            # Steps of 0 exercise equal timestamps; large steps exercise
            # eviction and idle-session restarts.
            t += draw(st.sampled_from([0.0, 0.5, 1.0, 2.0, 5.0, 10.0, 61.0, 400.0]))
            ts = t
        else:
            ts = float(draw(st.integers(0, 600)))
        out.append(ToolCallEvent(
            action=draw(st.sampled_from(ACTIONS)),
            agent_id="a",
            session_id=draw(st.sampled_from(SESSIONS)),
            timestamp=ts,
            resource=draw(st.sampled_from(RESOURCES)),
            magnitude=float(draw(st.integers(-3, 20))),
            attributes={"rows": float(draw(st.integers(0, 15)))},
        ))
    return out


def incremental(policy, events, repeat_alerts):
    ev = IncrementalEvaluator(policy, repeat_alerts=repeat_alerts)
    out = []
    for i, e in enumerate(events):
        for v in ev.on_action(e):
            out.append((v.rule, v.session_id, i, v.observed, v.events_elapsed))
    return out


def reference(policy, events, repeat_alerts):
    ref = NaiveReference(policy, repeat_alerts=repeat_alerts)
    return [(a.rule, a.session_id, a.index, a.observed, a.events_elapsed)
            for a in ref.run(events)]


@SETTINGS
@given(policies(), traces())
def test_incremental_equals_reference_every_alert(policy, events):
    """repeat_alerts=True: every breaching event, with its observed value."""
    assert incremental(policy, events, True) == reference(policy, events, True)


@SETTINGS
@given(policies(), traces())
def test_incremental_equals_reference_first_alert_per_session(policy, events):
    """Default de-duplication, including restart after an idle gap."""
    assert incremental(policy, events, False) == reference(policy, events, False)


@SETTINGS
@given(policies(), traces(ordered=False, max_size=80))
def test_incremental_equals_reference_on_unordered_timestamps(policy, events):
    """Late events are clamped to the session's latest timestamp in both."""
    assert incremental(policy, events, True) == reference(policy, events, True)


@SETTINGS
@given(policies(), traces())
def test_sessions_never_influence_each_other(policy, events):
    """Each session's alerts are the same whether or not other sessions'
    events are interleaved in the same evaluator."""
    together = incremental(policy, events, True)
    for sid in SESSIONS:
        alone_events = [e for e in events if e.session_id == sid]
        alone = [(r, s, o, el) for (r, s, _i, o, el) in incremental(policy, alone_events, True)]
        mixed = [(r, s, o, el) for (r, s, _i, o, el) in together if s == sid]
        assert alone == mixed
