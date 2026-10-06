# Bugs found by the test pass (2026-10-06)

Every bug below has a test marked `xfail(strict=True)`. When a fix lands,
the test starts passing, strict-xfail turns that into a failure, and the
marker must be removed. Nothing in `src/` was changed by this pass.
Reproduce all of them with `.venv/bin/python -m pytest -q -rx`. The
output is saved in `docs/evidence/test_run.txt`.

Severity scale: **major** = a wrong verdict on plausible input;
**minor** = a wrong verdict only at an edge, or a silently accepted bad
config; **low** = a validation or consistency gap with a fail-safe effect.

| ID | Severity | Area | Summary |
|---|---|---|---|
| BUG-001 | minor | `state.py` running sum | Float accumulation makes a sum that is exactly at the threshold fire |
| BUG-002 | minor | `state.py` eviction | Eviction leaves a float residue in `total` |
| BUG-003 | low | `spec.py` rule name | A falsy name (`0`, `""`, `false`) is silently replaced by `rule_<i>` |
| BUG-004 | low | `spec.py` response | `LOG`/`ESCALATE` are case-insensitive, but `WARN`/`PAUSE`/`BREAK` are not |
| BUG-005 | low | `spec.py` actions | `actions: ""` silently means "every action" |
| BUG-006 | major | spec + state cap | A rule that needs more events than the per-window cap can never fire, and nothing reports it |
| BUG-007 | minor | `evaluator.py` clock sweep | A stamp that lags the clock by more than `max_clock_skew` can drop a live session (REVIEW R3-m1) |
| BUG-008 | low | `evaluator.py` idle_ttl | `idle_ttl=NaN` is accepted and silently disables expiry (REVIEW R3-m2) |
| BUG-009 | minor (documented) | `StateStore` | With the default config, sessions that never return are never dropped |

---

### BUG-001: the running float sum overshoots at the threshold
- **Test:** `tests/test_cumulative.py::test_decimal_charges_summing_exactly_to_threshold_do_not_fire`,
  `::test_three_tenths_at_threshold_point_three_do_not_fire`
- **Repro:** cumulative `sum` rule, `attribute: cost_usd`, `threshold: 50`, `window: 1d`.
  Send 500 events with `cost_usd: 0.10`.
- **Expected:** no violation. The sum is exactly $50.00, and the rule fires only when the sum is
  *greater than* the threshold (`docs/rule_format.md`; the same convention is tested for whole numbers).
- **Actual:** fires on event 500 with `observed = 50.00000000000044`. With `threshold: 0.3`, three
  0.1 events give `0.30000000000000004` and fire.
- **Why it matters:** the shipped `policies/examples/budget.yaml` sums decimal dollar amounts against
  10.0 and 50.0 thresholds. This is a false positive at the exact limit, one event early. The
  property tests use whole-number values, so they do not hit it.
- **Possible fix (not applied):** sum in integer minor units, use `math.fsum` on a recompute, or
  compare with a relative tolerance.

### BUG-002: eviction leaves a float residue
- **Test:** `tests/test_cumulative.py::test_residue_after_eviction_does_not_fire_a_zero_threshold`
- **Repro:** cumulative `sum`, `threshold: 0`, `window: 10s`, `repeat_alerts=True`. Send magnitudes
  0.1, 0.2 and 0.3 at t=0, 1 and 2, then 0.0 at t=10.5, 11.5 and 12.5.
- **Expected:** at t=12.5 the window holds only the three 0.0 events, so the sum is 0 and nothing fires.
- **Actual:** `total == 1.1102230246251565e-16`, so the rule fires. `_Window._evict` resets `total` only
  when the deque is *empty*. Subtracting the evicted values leaves the residue whenever the window is
  not empty.
- **Severity note:** this only changes a verdict when the threshold sits within about 1e-16 of the true
  sum. It is the same root cause as BUG-001.

### BUG-003: falsy rule names are silently replaced
- **Test:** `tests/test_rule_parsing.py::test_falsy_non_string_or_empty_name_is_rejected[0|''|False]`
- **Repro:** `rules: [{name: 0, type: rate, max_calls: 1, window: 1s}]`
- **Expected:** `SpecError` starting `rules[0].name:`. A non-string name such as `[x]` already gets this.
- **Actual:** the rule loads as `rule_0`. Cause: `name = raw.get("name") or f"rule_{index}"` runs before
  the string type check (`spec.py`, `_build_rule`).
- **Effect:** alerts and `approve()` calls refer to a name the author never wrote.

### BUG-004: response spellings are inconsistently case-sensitive
- **Test:** `tests/test_rule_parsing.py::test_verb_spellings_are_case_insensitive_like_severity_names[WARN|Pause|BREAK]`
- **Repro:** `response: WARN` raises `SpecError: unknown response 'WARN'`, but `response: LOG` and
  `response: TERMINATE` are accepted.
- **Expected:** one rule for both spellings. `Response._missing_` lowercases its input, but it only
  looks it up in the severity-name table, not in the verb values.

### BUG-005: `actions: ""` matches every action
- **Test:** `tests/test_rule_parsing.py::test_empty_string_actions_is_rejected`
- **Repro:** `rules: [{name: r, type: rate, max_calls: 1, window: 1s, actions: ""}]`
- **Expected:** `SpecError` at `rules[0].actions`. A non-empty string becomes a one-item list.
- **Actual:** `raw.get("actions") or []` turns `""` into `[]`, which means "every action". A rule meant
  for one tool becomes a global rule. It fails safe (it fires more often), but nothing reports it.

### BUG-006: a rule that cannot fire under the per-window cap is accepted silently
- **Test:** `tests/test_rate.py::test_rule_that_cannot_fire_under_the_cap_is_rejected` (xfail);
  `::test_rule_that_cannot_fire_under_the_cap_is_silently_dead_today` pins the current behaviour.
- **Repro:**
  ```yaml
  sequence_defaults: {max_events_per_window: 100}
  sequence_rules:
    - {name: r, type: rate, max_calls: 150, window: 1h}
  ```
  Then 1,000 calls at the same instant.
- **Expected:** the policy or the monitor is rejected, or at least a warning is given. The window can
  never hold 151 events, so the rule is dead.
- **Actual:** no violation for any number of calls. Excess events are counted in `_Window.dropped`
  but not reported. The same applies to `aggregate: count` with `threshold >= cap`, and with the
  default cap of 10,000 to any `max_calls >= 10000`.
- **Why major:** a silently dead safety rule is the failure mode REVIEW M4 called the worst kind for
  a monitor. This one comes from the combination of two settings that are each valid. It is related
  to the known `backstop-flush` evasion, but it needs no adversary.

### BUG-007: a clock-lagging stamp lets the sweep drop a live session (REVIEW R3-m1)
- **Test:** `tests/test_review_regressions.py::test_R3_m1_lagging_stamps_do_not_drop_live_session`
- **Repro:** cumulative `sum > 100`, `window: 1h`. Use a clock that reads 600 s ahead of the event
  stamps, with `max_clock_skew=60`. Session A sends +60 at stamp 0, other sessions keep the sweep
  running, and A sends +60 at stamp 3,350.
- **Expected:** a violation (120 > 100 within one hour).
- **Actual:** none, and `clamped_events == 0`. Only the upper bound `clock() + skew` is enforced, so the
  sweep at `clock() - skew` drops A while it still has a live window event.

### BUG-008: `idle_ttl=NaN` is accepted (REVIEW R3-m2)
- **Test:** `tests/test_review_regressions.py::test_R3_m2_nan_idle_ttl_is_rejected`
- **Repro:** `IncrementalEvaluator(policy, idle_ttl=float("nan"))`
- **Expected:** `ValueError`, as already raised for `max_clock_skew=NaN` and for `0 < idle_ttl < longest window`.
- **Actual:** accepted. Every `ttl > 0` test is then False, so expiry is silently disabled.

### BUG-009: sessions are unbounded in the default configuration (documented limitation)
- **Test:** `tests/test_state_bounds.py::test_many_sessions_bounded_in_default_configuration`
- **Repro:** `SequenceMonitor(policy)` with no `clock` and no `shared_time_base`. Send 100,000
  one-event sessions, 0.01 s apart, against a 10 s rule.
- **Expected (for a bounded-memory monitor):** about 1,000 sessions held, as with a clock.
- **Actual:** all 100,000 sessions are held. This is disclosed in `HANDOFF.md`, `README.md` and
  report §7.6 as a deliberate trade-off (REVIEW R2-M1). It is listed here so that the growth path has
  a failing test that a fix would flip. With a `clock` or `shared_time_base=True` the store stays
  bounded (`test_many_sessions_bounded_with_a_clock`, `..._with_shared_time_base`, both passing).

## Checked and not bugs

- Window boundary `(now - window, now]`: consistent across cumulative, rate, ordering and scope, and
  against the independent reference.
- Total (all-tool) rate limits are supported (empty `actions`). See `tests/test_rate.py`.
- Hook exceptions propagate to the caller. They are not swallowed (`test_hook_exception_propagates_to_the_caller`).
- An ordering rule accepts an `actions:` key and ignores it. The `OrderingConstraint` docstring
  documents this, so it is not filed. A stricter parser could reject it.
- "B must be preceded by A" ordering is not implemented. This is a known missing feature
  (`HANDOFF.md`), not a bug, so there is no test.
