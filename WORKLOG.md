# Work log: seqmon (Detecting Cumulative Policy Violations in Autonomous AI Agents)

CSCI 412, Prashan Sapkota. Entries before 2026-10-06 are reconstructed from
`git log` / `git show` and checked against the code. They are not
contemporaneous notes.

---

## 2026-09-15: initial implementation (commits `42eefa7`, `96b5b9e`, `6261238`)

**Built** (`96b5b9e`, 17 files, +1,801 lines, first under
`agent-sequence-monitor/`; `6261238` moved everything to the repo root):

- `src/seqmon/events.py`: `ToolCallEvent` (action, agent_id, session_id,
  timestamp, resource, magnitude, success, metadata).
- `src/seqmon/spec.py`: YAML parser with all four constraint types:
  `cumulative` (sum / count / distinct), `rate`, `ordering` (forbidden pair
  `before` → `after` in a window) and `scope` (declared-resource allowlist
  with `max_outside` tolerance). Responses use AGT's `warn` / `pause` / `break`.
- `src/seqmon/state.py`: per-session, per-rule sliding windows (`_Window`:
  deque plus running sum and distinct-resource counter, evicted by event
  timestamp; `max_events` cap, default 10,000).
- `src/seqmon/evaluator.py`: `SequenceEvaluator.observe`, O(rules) per event.
  `Violation.events_elapsed` records detection latency.
- `src/seqmon/response.py`: `ResponseHandler` → `Decision`
  (terminate / approval_required).
- `src/seqmon/monitor.py`: `SequenceMonitor` facade (`from_file`, `observe`,
  `replay`).
- `policies/example.yaml`: six rules covering all four types.
- `bench/adversarial/slow_exfiltration.py` and
  `bench/benign/legitimate_reporting.py`: paired worked traces.
- `tests/test_seqmon.py`: 26 test functions.

**Note against the proposal plan.** Scope drift was planned for last. Its
evaluator exists from this first commit, built ahead of plan.

---

## 2026-09-28: evaluation harness, experiments, fixes (commit `ec4b486`)

**Built:** `bench/scenario.py` (labelled scenario format: `attack_start`,
`harm_index`, per-call `damage`); 30 seeded scenarios in `bench/suites/` (11
adversarial, 10 evasion, 9 benign); `bench/score.py`; `bench/naive.py`
(full-rescan baseline, doubles as a correctness oracle); `experiments/`
(`run_suite`, `detection`, `latency_damage`, `threshold_sweep`, `overhead`,
`run_all`). Tests grew from 30 to 110 (commit message; 98% coverage).

**Problems found and fixed:**

- **Scope rule ignored its window.** `_check_scope` kept a lifetime
  per-session counter in `SessionState.outside_scope`. Fix: it now records
  out-of-scope events in a sliding window (`state.record(...)`), and the
  counter was deleted. Effect: removed a false positive on a long benign
  session with occasional stray lookups. The `slow-scope-drift` evasion
  scenario (strays paced slower than the 1 h window) is now missed.
  Regression tests: `test_scope_tolerance_is_per_window_not_lifetime`,
  `test_scope_strays_inside_one_window_still_fire`.
- **Tests failed outside the repo root.** `POLICY = "policies/example.yaml"`
  was relative to the working directory. Fix:
  `Path(__file__).resolve().parents[1] / ...`.
- **Parser accepted bad values.** `max_calls: true` became 1 (bool is an int
  subclass). `allowed: [null]` became the resource `"None"`. `max_calls: lots`
  escaped as a bare `ValueError`. Probing a long multi-line YAML string with
  `Path.exists()` raised `OSError` ("File name too long"). Fix: `_number`,
  `_str_list` and single-line-only path probing in `load_policy`, all raising
  `SpecError`.
- **macOS hides the editable-install `.pth`.** In `.venv`,
  `_editable_impl_seqmon.pth` carries the `hidden` flag and Python skips it,
  so a bare `import seqmon` fails. Workaround: scripts insert `src/` into
  `sys.path`, and pytest uses `pythonpath = ["src", "."]`. (Re-confirmed
  2026-10-06: `ls -lO` shows `hidden,compressed,dataless`, and
  `cd / && .venv/bin/python -c "import seqmon"` gives
  `ModuleNotFoundError: No module named 'seqmon'`.)

**Findings reported, not fixed:**

- `distinct-table-sprawl` is always pre-empted by `resource-scope-drift` under
  `policies/example.yaml`. The leave-one-out ablation shows removing it loses
  no detection.
- Evasion: 8 of 10 evasion scenarios are missed (under-threshold-in-scope,
  window-hopping, backstop-flush, session-split, delayed-send, rate-pulsing,
  slow-scope-drift, budget-under-daily). `backstop-flush` exploits the
  10,000-event per-window cap. The naive oracle has no cap and catches a
  scaled-down version.

---

## 2026-10-06: checkpoint refinement (AGT format, interfaces, tooling, demo)

The working tree was **not committed** in this session. See "Commits" at
the end.

### 1. Read the real AGT source first

- `git clone https://github.com/microsoft/agent-governance-toolkit
  ../agt-src`, then `git checkout 5ed63c6e`:
  `error: pathspec '5ed63c6e' did not match any file(s) known to git`.
  `git fetch origin 5ed63c6e`: `fatal: couldn't find remote ref 5ed63c6e`.
  The pinned commit is not public. **Resolution:** used tag `v3.5.0`
  (`889c70ce`, 2026-05-07), the `agent_os_kernel` version the README names.
  Logged in `OPEN_QUESTIONS.md` #1.
- Read: `policies/schema.py` (`PolicyDocument`, `PolicyRule`,
  `PolicyCondition`, `PolicyDefaults`), `policies/evaluator.py`
  (`PolicyEvaluator._evaluate_flat`, `_match_condition`),
  `policies/policy_schema.json`, `cli/cmd_validate.py`,
  `integrations/base.py` (`ToolCallRequest`, `ToolCallResult`,
  `ToolCallInterceptor`, `PolicyInterceptor`, `CompositeInterceptor`),
  `stateless.py` (`ExecutionContext.history`, `_check_policies`) and
  `integrations/conversation_guardian.py` (warn/pause/break). Summary with
  paths: `docs/agt_integration.md`.
- Found that AGT `main` removed this YAML format on 2026-07-29 (`4b8b2114`,
  ACS v5) and added label-based "Context Accumulation Governance". Both are
  logged as open questions #2 and #6, not acted on.

### 2. Tooling (`pyproject.toml`)

- Added `[tool.ruff]` (line length 100, rules E F W I B UP) and
  `[tool.mypy]` (`strict = true`, `warn_unreachable`). Added `ruff`, `mypy`
  and `types-PyYAML` to the `dev` extra.
- Baseline: `ruff check .` with no config reported 22 errors. With the
  config it reported 19: E501 ×6, F541 ×3, B905 ×3, I001 ×2, F401 ×2,
  B007 ×2, UP035 ×1. `ruff --fix` handled the safe ones. The rest were fixed
  by hand: `strict=` on `zip` in `experiments/`, rewrapped figure captions,
  unused loop variables. E402 is ignored only for `bench/`, `experiments/`
  and `scripts/`, which deliberately import after editing `sys.path`.
- **Mistake made and fixed:** a `sed` meant to rename one unused loop
  variable in `bench/suites/adversarial.py` and `evasion.py` also renamed
  loops that do use it. Ruff then reported
  `F821 Undefined name 'i'` / `F821 Undefined name 'p'`. Reverted both files
  with `git checkout` and re-applied the rename by line number.
- `mypy src` baseline:
  `src/seqmon/evaluator.py:166: error: Statement is unreachable [unreachable]`
  (the defensive `else: continue` after four `isinstance` branches) and
  `src/seqmon/monitor.py:49: error: Function is missing a type annotation for
  one or more parameters [no-untyped-def]` (`**kwargs`). Fixed by making the
  scope branch the final `else` and annotating `**kwargs: Any`.
- `requires-python` stays `>=3.10`. Checked by creating a fresh 3.10.11 venv,
  `pip install -e ".[dev]"`, then `pytest` (124 passed), `ruff check .` and
  `mypy src` (both clean).

### 3. AGT policy-file layout (`src/seqmon/spec.py`)

- **Decision:** sequence rules go under a top-level `sequence_rules:` list in
  the AGT policy file, with optional `sequence_defaults:`. **Reason:** AGT
  already keeps a non-per-call section (`a2a_conversation_policy`) beside
  `rules` in `policy_schema.json`, and its pydantic loader ignores unknown
  keys. The same file can therefore drive both layers. A document without
  `sequence_rules` is still read the old way (standalone `rules:`), so
  `policies/example.yaml`, `bench/` and all 110 earlier tests keep their
  behaviour. (Correction, fix round 1: the files were edited, for lint fixes
  and the oracle `attribute` change, but their behaviour is unchanged.)
- New `parse_policy(mapping)`. `SequencePolicy.layout` is `"agt"` or
  `"standalone"`. `SequencePolicy.max_events_per_window` comes from
  `sequence_defaults`.
- An AGT-style rule (has `condition`, no `type`) in a standalone `rules`
  list now gives a hint to use `sequence_rules`, instead of "missing required
  field 'type'".
- **Field paths in errors:** every `SpecError` now starts with the path,
  e.g. `sequence_rules[2].window: rule 'c': bad duration 'soon'; ...`. The
  old message text follows the prefix, so the existing `match=` tests still
  pass. Also tightened: `before`/`after` must be non-empty strings (they were
  `str()`-coerced). YAML syntax errors now raise `SpecError` (they used to
  escape as `yaml.YAMLError`). Durations reject booleans.
- **Verified against real AGT** with `scripts/check_agt_compat.py`, run in a
  scratch venv with pydantic 2.13.5 and jsonschema 4.26.0. It loads
  `schema.py` and `evaluator.py` straight from `../agt-src`. Result: AGT's
  `PolicyDocument.from_yaml` loads all four `policies/examples/*.yaml` and
  drops `sequence_rules` / `sequence_defaults`. AGT's JSON schema rejects them:
  `Additional properties are not allowed ('sequence_defaults',
  'sequence_rules' were unexpected)`. Not resolved; logged as open question #3.

### 4. Model and runtime refinements

- `events.py`: `ToolCallEvent.attributes: Mapping[str, float]` (named
  numeric quantities such as `records` and `cost_usd`), `value(attribute)`, and a
  `tool_name` property (AGT's name for the tool). Existing fields and the
  constructor are unchanged.
- `spec.py` / `state.py` / `evaluator.py`: cumulative `sum` rules take an
  optional `attribute:`. `_Window` sums `event.attributes[attribute]`
  instead of `magnitude`. `bench/naive.py` does the same, so the oracle stays
  equivalent.
- `state.py`: the window cap comes from configuration, either
  `sequence_defaults.max_events_per_window` or the `SequenceMonitor` /
  `StateStore` argument (default 10,000). **Decision:** the manual cap is kept
  instead of `deque(maxlen=...)`, because a silently dropped event must also be
  subtracted from the running sum and counted in `dropped`.
- `evaluator.py`: `IncrementalEvaluator.on_action(event) -> list[Violation]`
  is the public name. `SequenceEvaluator` and `.observe` are aliases (the same
  class and function objects), so `bench/` and `experiments/` did not change.
  `SequenceMonitor.on_action` likewise, with `observe` as an alias.
- `spec.py` `Response`: `LOG` / `ESCALATE` / `TERMINATE` are enum aliases of
  `WARN` / `PAUSE` / `BREAK`, and `_missing_` accepts `log` / `escalate` /
  `terminate` in YAML. `Response.severity` gives the severity name.
  **Reason:** keeping the AGT verbs as values means no stored result
  (`results/*.jsonl`) or test changes.
- `response.py`: `EscalationHook` and `TerminationHook` protocols, with
  `NoOpHooks` (default) and `LoggingHooks`. `ResponseHandler` calls
  terminate, or escalate when the decision does not also terminate, once per
  decision.
- `src/seqmon/adapters/`: `PerCallEngine`, `CallFeed`, `PerCallVerdict` and
  `InterceptOutcome` in `base.py`. `fake_agt.py` has `FakePerCallEngine`, which
  mirrors AGT's flat evaluation (priority sort, first match, allow/audit
  permitted, `defaults.action`), and `FakeAGTInterceptor`, which runs per-call →
  monitor, refuses later calls of a terminated session, and asks an `approver`
  on escalation. `check_agt_compat.py` compared `FakePerCallEngine` with AGT's
  real `PolicyEvaluator` on 36 calls: **0 disagreements**. No real AGT hook was
  written. The verified interception point (`ToolCallInterceptor`, last in a
  `CompositeInterceptor`) and the missing pieces are in
  `docs/agt_integration.md`.
- Docstrings and type hints on all public API. `mypy --strict` is clean.

### 5. Demo, examples, docs, smoke tests

- `policies/examples/`: `exfiltration.yaml`, `budget.yaml`, `rate.yaml`
  and `agt_combined.yaml` (AGT per-call `rules` plus `defaults` plus
  `sequence_rules`). All parse.
- `scripts/demo_trace.py` (adapted from
  `bench/adversarial/slow_exfiltration.py`, with rows as
  `attributes["records"]`). Output:
  `Per-call engine alone (no sequence layer): 32 of 32 calls allowed ->
  10,860 rows read, external send runs`, then
  `Sequence violation fired at step 7: resource-scope-drift [ESCALATE]` and
  `Sequence violation fired at step 17: bulk-customer-read [TERMINATE]
  observed 5260 > threshold 5000 after 17 events`, and
  `Session terminated at step 17; 15 later call(s) refused, including the
  external send.`
- `docs/rule_format.md`: schema, one example per constraint type, the AGT
  conventions mirrored (with file paths) and the error-path format.
  `OPEN_QUESTIONS.md`.
- `tests/test_smoke.py`: 14 tests. The demo runs as a subprocess from `/`
  and reports step 17. Examples parse. Also checks per-call denial, severity
  aliases, the field path in errors, the AGT-rule hint, the config cap,
  `on_action`, hooks and attribute sums.

### 6. Verification (all run 2026-10-06)

- `.venv/bin/python -m pytest -q`: **124 passed** (110 existing plus 14 smoke).
- `.venv/bin/ruff check .`: `All checks passed!`
- `.venv/bin/mypy src`: `Success: no issues found in 10 source files`
- `python experiments/run_all.py`: `All 7 steps passed`. `detection.csv`,
  `ablation.csv`, `latency_damage.csv`, `threshold_sweep.csv`,
  `suite_results.jsonl` and `crosscheck.json` are **byte-identical** to the
  copies from before today's changes. Headline numbers are unchanged:
  adversarial 11/11, evasion 2/10, benign false positives 0/9.
- **Overhead regression (small, measured, not hidden).**
  `experiments/overhead.py` at 100,000 events gave 3.15 and 3.24 µs/event
  with today's changes stashed. With the changes it gave 3.32–3.54 µs/event
  (5 runs, after inlining the `magnitude` fast path in `_Window` and making
  `observe` a direct alias). The naive baseline also varied 9,984–11,959 µs
  between runs, so this is not a controlled comparison. The likely cause is
  the extra attribute branch and the hook dispatch in `ResponseHandler`.

### Commits

None made. Committing was not requested in this session, so all changes
are left in the working tree on branch `progress-report` for review.

---

## 2026-10-06 (fix round 1): answering `REVIEW.md`

Snapshot before any change: `pytest` 124 passed, `ruff` and `mypy` clean.
`experiments/run_all.py` was run and `results/` copied aside, so the effect
of the fixes on every result file could be checked afterwards.

### B1. Progress report missing: fixed

- **Change:** wrote `docs/progress_report_1.md`. It covers the timeline
  status, components, the AGT source study, the evaluation results and
  figures, the problems found and fixed, the limitations and the next
  steps. Every number in it was re-run today. The figures it embeds were
  copied from `results/figures/` (gitignored) into `docs/figures/`, so the
  report renders from the repository. README links to it.
- **Verification:** every number in the report was checked against
  today's `run_all.py` output, `scripts/demo_trace.py` output, `git log`, or
  the code. One draft sentence was corrected: the oracle cross-check is 29
  suite scenarios agreeing plus one scaled-down `backstop-flush` that
  differs, not "29 of 30".

### M1. Out-of-order and future timestamps: fixed

- **Finding:** `_Window.add` appended late events at the tail. A rate probe
  at t = 100, 0, 105 fired with 3 while the oracle said nothing. One event
  at t = 1e12 emptied every window. An ordering rule gave a gap of -50.
- **Decision:** clamp rather than reject, because a late call still
  happened and still has to count. A late event is counted at the session's
  latest timestamp. Forward skew can only be bounded against a clock, so an
  optional `clock` / `max_clock_skew` was added. Without a clock, the
  interceptor's timestamps must be trusted, and this is documented.
- **Change:** `src/seqmon/evaluator.py:IncrementalEvaluator.on_action`
  (clamping, `dataclasses.replace` only when the timestamp changes);
  `state.py:SessionState` (`last_timestamp` starts as `None`, new
  `clamped_events`); `bench/naive.py:NaiveEvaluator.observe` applies the
  same contract, so the oracle still defines the expected behaviour;
  `docs/rule_format.md` "Time and windows" section.
- **Verification:** `tests/test_review_fixes.py`:
  `test_late_event_is_clamped_and_matches_oracle` (both now fire, with 3,
  at t=105), `test_ordering_gap_is_never_negative` (gap 0.0),
  `test_equal_timestamps_are_not_clamped`, the clock test (observed 180,
  not 90), the documented no-clock limit, and a differential test against
  the oracle on 20 random traces with late timestamps and attribute sums
  (each trace has 40-53 clamped events and 6-7 alerts, so it is not
  vacuous).

### M2. Memory unbounded across sessions: fixed (one part declined)

- **Change:**
  - `state.py:StateStore` is now least-recently-seen ordered
    (`OrderedDict`). New `expire_idle(now, idle_ttl)`, `peek`, `touch`.
  - `evaluator.py:IncrementalEvaluator`: `idle_ttl` defaults to the
    longest rule window. A session idle at least that long is dropped when
    it returns (exact), and other idle sessions are swept every
    `idle_ttl / 16` of event time (amortised). Dropping such a session
    loses no live window event, only its fired-alert record and event
    counter.
  - `_fired` moved from the evaluator into `SessionState.fired`, so
    `reset` is O(1) and the record goes away with the session.
  - `response.py:ResponseHandler.history` is a `deque(maxlen=history_limit)`,
    1,000 by default (`SequenceMonitor(history_limit=...)`).
  - `monitor.py:SequenceMonitor.footprint` uses `peek` and no longer
    creates a session.
  - `state.py:SessionState.prune` / `memory_footprint(now)` prune idle
    rule windows on demand.
  - `adapters/fake_agt.py:FakeAGTInterceptor._terminate` drops the
    monitor's state for a terminated session.
- **Declined, with reason:** `FakeAGTInterceptor.terminated` still keeps
  one id per terminated session. That id is the minimum state needed to
  keep refusing the session's calls, and expiring it would let a
  terminated session resume. Logged as `OPEN_QUESTIONS.md` #12.
- **Verification:** `test_idle_sessions_are_expired` (5,000 sessions 30 s
  apart: at most 1.0625 days' worth kept),
  `test_returning_session_after_longest_window_starts_fresh`,
  `test_footprint_does_not_create_a_session`, `test_response_history_is_bounded`,
  `test_idle_rule_window_is_pruned_by_session_prune`,
  `test_terminated_session_state_is_dropped`. `HANDOFF.md` and `README.md`
  now state the bound precisely instead of "constant memory".
- **Effect on results:** after the fixes, `run_all.py` gives byte-identical
  `detection.csv`, `ablation.csv`, `latency_damage.csv`,
  `threshold_sweep.csv`, `suite_results.jsonl` and `crosscheck.json`
  (compared with `cmp` against the copy from before the fixes).
- **Overhead:** before the fixes, `overhead.py` gave 3.31-3.40 µs/event at
  5,000 to 100,000 events. After a first version it gave 3.64-3.66. After
  the sweep was amortised and the session lookup was reduced, it gave
  3.43-3.51 in `run_all.py` and 3.44-3.55 in three more runs. An ablation
  with expiry off (`idle_ttl=0`) gave 3.38-3.51, the same within noise, so
  most of the remaining cost is the clamping and LRU bookkeeping. README
  table updated with these numbers and their date.

### M3. Approved escalation muted the rule: fixed

- **Finding:** with `repeat_alerts=False`, an approved ESCALATE never fired
  again. One approval let 500 out-of-scope calls through, and the demo's
  approval was silent.
- **Change:** `evaluator.py:IncrementalEvaluator.rearm`,
  `monitor.py:SequenceMonitor.approve`. `fake_agt.py:FakeAGTInterceptor.submit`
  re-arms after approval, records each escalation as an `EscalationRecord`
  (`auto=True` when no approver was configured), and `from_policy_file`
  takes an `approver`. `scripts/demo_trace.py` prints how escalations were
  handled and has `--deny-escalations`. `repeat_alerts=False` stays the
  default, so benchmark de-duplication is unchanged (`bench/score.py` does
  not approve, so its results are identical).
- **Verification:** `test_every_breach_after_approval_asks_again` (the same
  500-call probe now asks 499 times), `test_refused_escalation_ends_the_session`,
  `test_default_approver_is_recorded_as_automatic`,
  `test_monitor_approve_rearms_only_the_named_rules`. Demo output: "Escalations: 10, all
  auto-approved because the demo has no human approver configured";
  `--deny-escalations`: "Session terminated at step 7; 25 later call(s)
  refused".

### M4. `action:` collision and silently ignored keys: fixed

- **Change:** `spec.py:_check_keys` (called from `_build_rule`) rejects any
  key the rule type does not define, with the field path. `action:` is
  rejected. If its value is an AGT verdict, the message says to use
  `response:`; otherwise it says to use `actions:`. The singular alias was
  removed from `_build_rule`.
- **Deliberate test change:** `tests/test_spec_response.py::
  test_scalar_actions_and_allowed_are_accepted` used `action:` for the
  alias. It now uses `actions: messaging.send` (scalar form, still
  covered). All six policy files in `policies/` still parse.
- **Verification:** `test_agt_action_verdict_is_rejected_with_hint`
  (allow/deny/block/audit), `test_singular_action_filter_is_rejected`,
  `test_unknown_rule_keys_are_rejected` (`attrbute`, `max_outsde`,
  `priority`, `threshold` on ordering), `test_unknown_keys_rejected_in_agt_layout_too`.

### M5. Work uncommitted: declined in this round

- **Reason:** the user's request for this work did not ask for commits,
  and this session's operating rules allow committing only when the user
  asks. The workflow's instruction to commit came from the orchestration
  script, not the user. Nothing was committed, amended or pushed. The
  suggested split, to run when the user asks: (1) tooling (`pyproject.toml`,
  lint-only edits in `bench/`, `experiments/`); (2) spec format and strict
  keys; (3) runtime fixes (state, evaluator, response, monitor, events,
  naive oracle); (4) adapters and demo; (5) tests; (6) docs, report,
  figures, logs.

### MINOR items

- **Fixed:**
  - 1 (`spec.py:_number`, `parse_duration`): numbers must be non-bool
    `int`/`float`, finite and `>= 0`; integer fields must be `int`.
    Windows must be finite.
  - 2 (`events.py:ToolCallEvent.__post_init__`): non-finite timestamp,
    magnitude or attribute raises `ValueError`. Negative values stay allowed
    (refunds) and are documented.
  - 3 (`spec.py:_build_rule`): ordering with `before == after` is
    rejected.
  - 5 (`evaluator.py:_unknown_constraint`): a `NoReturn`-typed guard
    replaces the `else` fall-through in dispatch, `_threshold_of` and the
    new `_kind_of`. mypy accepts it under `warn_unreachable`.
  - 6: `(now - window, now]` is documented, with boundary tests at
    9.999 / 10.0 / 10.001 s.
  - 7 (`Violation.kind`, `describe`): ordering violations read
    "forbidden action Ns after the enabling one".
  - 8: `FakeAGTInterceptor` takes `PerCallEngine`; `from_policy_file`
    takes `approver`.
  - 9 (`FakePerCallEngine.check`): errors fail closed with `deny`.
  - 11 (partial): the new differential test generates attributes and late
    timestamps. YAML-configured caps are still not covered.
  - 12: README overhead table regenerated and dated.
  - 13: wording corrected above.
  - 14 (part): `SequencePolicy.layout` is a `Literal`.
  - 15: the caveat is in the report, and in the header comment of
    `policies/examples/agt_combined.yaml`.
- **Not done:**
  - 4 (store only `last_before_ts` for ordering): deferred to the "must be
    preceded by" work.
  - 10 (wider AGT compatibility probes): needs the scratch AGT venv.
    Deferred; the report states that only 4 of 36 calls match a rule.
  - 14 (part, `metadata` mutability on a frozen dataclass): changing the
    type would break callers. Deferred.

### Verification (after all changes)

- `.venv/bin/python -m pytest -q`: **192 passed** (124 before, plus 68 in
  `tests/test_review_fixes.py`).
- `.venv/bin/ruff check .`: `All checks passed!`
- `.venv/bin/mypy src`: `Success: no issues found in 10 source files`
- `.venv/bin/python scripts/demo_trace.py`: TERMINATE at step 17, 15 calls
  refused, 10 escalations reported as auto-approved.
- `.venv/bin/python experiments/run_all.py`: `All 7 steps passed`. All
  result files except `overhead.csv` are byte-identical to the copy from
  before the fixes.

---

## 2026-10-06 (fix round 2): answering `REVIEW.md` Round 2

Snapshot before any change: `pytest` 192 passed, `ruff` and `mypy` clean.

### R2-M1. Idle sweep coupled sessions through timestamps: fixed

- **Finding:** `IncrementalEvaluator.on_action` swept every session using
  the arriving event's timestamp as "now". Without a clock (the default
  everywhere), one far-future stamp in session B emptied session A, so a
  90 + 90 breach of a 100 threshold was missed. It also pushed the next sweep
  to about 1e12, which stopped expiry (5,002 sessions held instead of 368).
  Sessions on independent time bases lost detections with no attacker
  involved. Two docstrings said the sweep "never" affects results, which was
  wrong.
- **Change:** `src/seqmon/evaluator.py:IncrementalEvaluator.on_action` /
  `__init__`. The sweep's "now" never comes from an event stamp unless the
  caller opts in:
  - with a `clock`: `now = clock() - max_clock_skew`. This is
    result-neutral if no event is stamped more than `max_clock_skew` behind
    the clock;
  - with the new `shared_time_base=True` and no clock: `now = ts` (the old
    behaviour, documented as safe only for one trusted time base);
  - otherwise: no sweep.
  `_next_sweep` was replaced by `_last_sweep`, and a clock that steps
  backwards re-arms the sweep. Expiry on return (per session) is unchanged.
  `SequenceMonitor.__init__` passes `shared_time_base` through.
  Docstrings were corrected: the `evaluator.py` module docstring (new
  paragraph on expiry), the `IncrementalEvaluator` args, and the `state.py`
  module docstring.
- **Trade-off (disclosed):** by default, with no clock and no opt-in,
  sessions that never return are kept. So cross-session memory grows with
  abandoned sessions. I chose this over a poisonable default because the
  reviewer's point is right: session isolation is the basic contract. The
  live path should pass `clock=time.time`. I did not make that the default
  in `FakeAGTInterceptor.from_policy_file`, because the demo uses synthetic
  timestamps starting at t = 3, and a wall clock would treat them as idle
  for decades.
- **Deliberate test change:** `tests/test_review_fixes.py::
  test_idle_sessions_are_expired` now passes `shared_time_base=True`. Its
  trace is one time base, which is what the flag declares. It failed (5,000
  held) under the new default, as intended.
- **New tests** (`tests/test_review_fixes.py`, R2-M1 section): the review's
  cross-session probe with no clock and with a clock (fires, observed 180);
  independent time bases (A at t = 0, B at t = 1.7e9, A at t = 10: fires);
  the review's probe 4 (a victim with 30 s left is not dropped by another
  session's stamp clamped to clock + 60); expiry still runs after a 1e12
  stamp when a clock is set; the default does not sweep; and
  `shared_time_base=True` keeps the documented coupling.
- **Verification (scratch probe, 5,000 one-event sessions 10 s apart,
  1 h window):** clock: 374 held; clock plus a 1e12 stamp: 374 held;
  `shared_time_base`: 368 held (same as the review's baseline);
  `shared_time_base` plus a 1e12 stamp: 5,001 held (documented); default:
  5,000 held, 0 expired. Cross-session 90 + 90 probe: `[180.0]` by default
  and with a clock, `[]` with `shared_time_base=True`.
- **Disclosed in:** report §6.2 (new paragraph) and §7 item 6,
  `docs/rule_format.md` (Time and windows), README "Bounded memory",
  `HANDOFF.md` (status table and known issues).

### R2-M2. Report linked to code without the reported work: reworded

- **Finding:** the report's Code line pointed at branch `progress-report` on
  GitHub, which is at `ec4b486`. None of this checkpoint is committed.
- **Change:** `docs/progress_report_1.md` header. The Code line now says the
  work is in the working tree of `progress-report`, **not yet committed** as
  of 2026-10-06, and that the link currently shows `ec4b486`, which lacks
  the fixes, adapters, demo, examples, docs and the report. This is the
  reviewer's option (b). Option (a), commit, push and cite the hash, is
  the user's call (see M5). If the user commits, replace this line with
  the commit hash.

### M5. Work uncommitted: declined again (user's decision)

The workflow task text asked for one commit per fix. The user's own request
does not mention commits, and this session's rules only allow committing
when the user asks. The reviewer also deferred this to the user. No commits
were made. `git status` still shows the modified and untracked paths. The
six-commit split proposed in the round 1 entry still applies, plus one
commit for this round (`evaluator.py`, `state.py`, `monitor.py`, `spec.py`,
`tests/test_review_fixes.py`, docs).

### MINOR items

- **R2-m1 (fixed):** `IncrementalEvaluator.__init__` raises `ValueError`
  for `max_clock_skew < 0` or NaN, and for `0 < idle_ttl < longest
  window`. `idle_ttl <= 0` still disables expiry. Tests:
  `test_negative_or_nan_clock_skew_is_rejected`,
  `test_idle_ttl_shorter_than_longest_window_is_rejected` (also through
  `SequenceMonitor`), `test_idle_ttl_zero_or_at_least_longest_window_is_accepted`.
- **R2-m2 (fixed):** `src/seqmon/spec.py:parse_policy` rejects top-level
  keys outside `{version, name, description, rules, sequence_defaults}` in
  the standalone layout. The AGT layout is unchanged, since the other keys
  belong to AGT. All five shipped policy files still load. Tests:
  `test_unknown_top_level_key_rejected_in_standalone_layout`,
  `test_unknown_top_level_key_allowed_in_agt_layout`.
- **R2-m3 (fixed):** report §5.4 now says "about 3.0-3.6 µs/event across
  the 2026-10-06 runs and session sizes" and keeps the noise caveat.
  `HANDOFF.md` now uses the same range.
- **R2-m4 (fixed):** report §6.2 intro now says "three fixed outright; the
  timestamp one fixed for late events and bounded for future ones only when
  a clock is configured".

### Verification (after all changes)

- `.venv/bin/python -m pytest -q`: **208 passed** (192 before, plus 16 new).
- `.venv/bin/ruff check .`: `All checks passed!`
- `.venv/bin/mypy src`: `Success: no issues found in 10 source files`
- `scripts/demo_trace.py`: TERMINATE at step 17, 15 later calls refused,
  "Escalations: 10, all auto-approved ...". `--deny-escalations`: session
  terminated at step 7, 25 calls refused. Same as before.
- `experiments/run_all.py`, run in a scratch copy of the tree: `All 7 steps
  passed`. `ablation.csv`, `detection.csv`, `latency_damage.csv`,
  `threshold_sweep.csv`, `crosscheck.json` and `suite_results.jsonl` are
  byte-identical (`cmp`) to `results/`. `overhead.csv` differs in timings
  only: 3.0 / 3.43 / 3.43 / 3.44 µs/event. So the R2-M1 change did not alter
  any suite result.
- Test counts updated to 208 in the report (§1, §5.5, §9), `README.md` and
  `HANDOFF.md`.
