# Review: seqmon checkpoint refinement (branch `progress-report`, uncommitted tree)

Reviewer pass, 2026-10-06. Scope: `HANDOFF.md`, `WORKLOG.md`, `OPEN_QUESTIONS.md`,
`docs/rule_format.md`, `docs/agt_integration.md`, all of `src/seqmon/`, the new
scripts and tests, `git log`, and the AGT source at `../agt-src` (tag `v3.5.0`,
`889c70ce`).

## What I ran myself

| Check | Result |
|---|---|
| `.venv/bin/python -m pytest -q` | 124 passed |
| `.venv/bin/ruff check .` | All checks passed |
| `.venv/bin/mypy src` | Success: no issues found in 10 source files |
| `scripts/demo_trace.py` | ESCALATE at step 7 (`resource-scope-drift`, observed 4 > 3), TERMINATE at step 17 (`bulk-customer-read`, 5260 > 5000), 15 later calls refused. Matches the handoff. |
| README quickstart from scratch in `../review-venv` (Python 3.12.7, `pip install -e ".[dev]"`) | demo, pytest (124), ruff, mypy all clean. `cd / && python -c "import seqmon"` works in this fresh venv. |
| `scripts/check_agt_compat.py` in `../review-venv` + pydantic 2 + jsonschema | Reproduced: pydantic loader accepts all 4 examples and drops `sequence_*`; JSON schema rejects all 4; 36 calls, 0 disagreements. |
| `git cat-file -t 5ed63c6e` in `../agt-src` | `Not a valid object name`. Confirms the pinned commit is absent. |
| AGT source claims in `docs/agt_integration.md` | Verified: `ToolCallRequest` fields (`integrations/base.py:624`), `CompositeInterceptor` first-deny semantics (`:799-814`), `ExecutionContext.session_id` (`:556-559`), flat evaluator priority sort / first match / allow+audit (`policies/evaluator.py:171-197`), `_match_condition` (`:315-343`), fail-closed `except` (`:236-244`), "kernel never looks up prior requests" (`stateless.py:19-20`), `MCPSlidingRateLimiter` and `BudgetTracker` files exist, `additionalProperties: false` (`policy_schema.json:40`), commits `4b8b2114` and `0d674bed` exist with the stated subjects. |
| 2026-09-15 / 2026-09-28 history in `WORKLOG.md` | Verified against `git show`: `96b5b9e` has 26 test functions and 17 files / +1,801; `ec4b486` removes `outside_scope`, adds `isinstance(value, bool)`, `_str_list`, the `OSError` guard and the `__file__`-relative `POLICY`. |

Not re-run: `experiments/run_all.py` (it overwrites `results/`, and this role may
only write `REVIEW.md`), and the Python 3.10 venv. The "byte-identical results" and
"3.10.11 works" claims are unverified by me.

Edge-case probes were run from a scratch script against `src/` (outputs quoted
below). None of the probes modified the repo.

---

## BLOCKER

### B1. The progress report, the user's stated main deliverable, does not exist
- **Where:** repository root / `docs/`. There is no report file. The engineer's summary says "I didn't write the progress report itself".
- **What's wrong:** the user's request says "report is the main priority with details of the work done". This checkpoint delivers code, logs and a handoff, but no report.
- **Why it matters:** the checkpoint is graded on the report. `WORKLOG.md` and `HANDOFF.md` are raw material, not the deliverable.
- **Fix:** write the Progress Report 1 (e.g. `docs/progress_report_1.md`). Build it from the verified facts in this review. It must also carry the caveats in M1-M5 and the honesty items below. If a later workflow stage owns the report, this blocker transfers to that stage. It must not be closed by this engineering pass alone.

---

## MAJOR

### M1. Out-of-order and future timestamps corrupt every window
- **Where:** `src/seqmon/state.py:63-71` (`add` appends at the tail regardless of timestamp) and `:85-95` (`_evict` pops only from the head, using the *incoming* event's timestamp as "now"). `src/seqmon/evaluator.py:104-107` (ordering gap).
- **What's wrong:** the deque is assumed to be in timestamp order, but nothing on the live path enforces it. Only `bench/scenario.py:128-130` rejects unordered traces, and that runs offline. Probes:
  - Rate `max_calls: 2, window: 10s`, events at t = 100, 0, 105: the incremental evaluator fires at 105 with observed 3. The naive oracle (`bench/naive.py`) reports nothing. This is a false positive caused by a stale event stuck behind a newer one.
  - Cumulative `threshold: 100, window: 1h`: 90 at t=0, then one event at t=1e12, then 90 at t=2. The far-future event evicts everything (`total` reset to 0.0), and the t=2 read sees 90. A single bad timestamp clears the window, which is an evasion.
  - Ordering `before: a, after: b`: `a` at t=100, then `b` at t=50, fires with `observed = -50`.
- **Why it matters:** `docs/agt_integration.md` says timestamps would come from the interceptor, and that multi-threaded or async tool calls complete out of order. The incremental/oracle equivalence claimed in README is only true for monotonic input.
- **Fix:** decide on a contract and enforce it in `IncrementalEvaluator.on_action`. Either reject or clamp `event.timestamp < state.last_timestamp` (e.g. `ts = max(ts, state.last_timestamp)`), and bound forward skew. Document the contract in `docs/rule_format.md`. Add tests for late, equal and far-future timestamps.

### M2. "Constant memory" holds per session, not per monitor; several structures grow without limit
- **Where:**
  - `src/seqmon/state.py:176-197`: `StateStore._sessions` is never evicted. `drop` is only called from `reset`.
  - `src/seqmon/evaluator.py:160`: `_fired` grows with every (session, rule) that ever fired. `reset` (`:214-217`) rebuilds the whole set, which costs O(|_fired|) per call.
  - `src/seqmon/response.py:155,165`: `ResponseHandler.history` appends every violation forever. With `repeat_alerts=True` this is one entry per breaching event.
  - `src/seqmon/adapters/fake_agt.py:204,233`: `FakeAGTInterceptor.terminated` grows forever. A terminated session's monitor state is never dropped.
  - `src/seqmon/monitor.py:101-103`: `footprint()` calls `store.get`, which *creates* a session as a side effect.
  - `src/seqmon/state.py:139-160`: a rule's window is only pruned when an event for that rule arrives. An idle rule keeps up to `max_events` stale events indefinitely.
- **Probes:** 50,000 one-event sessions leave `len(store) == 50000`. 1,000 terminated sessions leave 1,000 entries each in `terminated`, `store` and `_fired`. `footprint("never-seen")` raises `len(store)` from 0 to 1. A rule whose window holds 100 events still holds 100 events after an event for a different rule at t = 1e6 s.
- **Why it matters:** `HANDOFF.md:29` marks "Session state, constant memory" as **Done**, and the README sells bounded memory as a core property. Per session, memory is bounded by rules × `max_events_per_window`, not by the window. Per process, it is unbounded in the number of sessions. A long-running interceptor serving many sessions leaks.
- **Fix:** add idle-session expiry to `StateStore`, e.g. drop sessions whose `last_timestamp` is older than the largest rule window. Clear `_fired` entries in the same place, and key `_fired` per session so `reset` is O(rules). Drop monitor state when a session terminates. Cap `ResponseHandler.history` (or make it opt-in). Make `footprint` use `_sessions.get`. Reword the claim to "bounded per session by rules × max_events_per_window" until expiry exists.

### M3. With the default `repeat_alerts=False`, an approved ESCALATE mutes that rule for the rest of the session
- **Where:** `src/seqmon/evaluator.py:191-194`. `src/seqmon/adapters/fake_agt.py:170-171,203,234-237`. The demo builds the interceptor at `scripts/demo_trace.py:83` with no approver.
- **What's wrong:** once a rule fires for a session it never fires again. In the interceptor an ESCALATE asks the approver once; if approved, the session continues with that rule permanently silent. Probe: scope rule `max_outside: 1, response: escalate` against 500 out-of-scope calls. The approver is asked **once**, and all 500 execute. The demo's step-7 ESCALATE is approved by the default `_approve_all`, and the demo output does not say so.
- **Why it matters:** `repeat_alerts=False` was introduced to keep the benchmark's latency statistic clean (`evaluator.py:144-146`), but it is also the default for the live monitor. "Human approval before next action" (`spec.py:65`, `response.py:10-11`, `docs/rule_format.md:73`) then means "one approval buys unlimited further drift". The report must not present the step-7 escalation as an enforced human gate.
- **Fix:** separate benchmark de-duplication from enforcement. For example, re-arm a rule after approval (clear its `_fired` key, or re-alert when the observed value next increases). Alternatively default `repeat_alerts=True` in `SequenceMonitor`/`FakeAGTInterceptor` and keep `False` only in `bench/`. Make the demo print "escalation auto-approved (no approver configured)". Add a test that a second breach after approval escalates again.

### M4. Sequence rules reuse AGT's `action` key with a different meaning, and unknown keys are silently ignored, so typos create dead rules
- **Where:** `src/seqmon/spec.py:371-372` (`action` is a synonym for `actions`, a tool-name filter) and `:345-440` (no unknown-key check in rules, although `sequence_defaults` does reject unknown keys at `:449-451`).
- **What's wrong:** in AGT, `action:` is the verdict (`allow|deny|audit|block`, `policies/schema.py:51-56`). Someone writing a sequence rule by AGT habit with `action: deny` gets `actions == ('deny',)`, a filter matching a tool literally named `deny`, so the rule never fires. Probe confirmed. Likewise `attrbute: records` (typo) silently sums `magnitude`. `max_outsde: 10` silently becomes 0. `priority: 5` and `foo: bar` are accepted and ignored.
- **Why it matters:** the design goal is "follows AGT's YAML conventions so sequence rules live in the same policy file". A key that means something different in the adjacent `rules:` list is a trap, and a silently dead safety rule is the worst failure mode for a monitor. It also contradicts `docs/rule_format.md:179-195`, which implies malformed input is reported.
- **Fix:** reject unknown keys per rule type with a field-path `SpecError`, as `sequence_defaults` already does. Drop the singular `action` alias, or reject values in `{allow, deny, audit, block}` with a hint pointing to `response:`. Add tests.

### M5. Work is uncommitted, contrary to the project ground rules
- **Where:** `git status`: 20 modified and 9 untracked paths, no commits since `ec4b486`. `WORKLOG.md:250-254` says "Committing was not requested".
- **What's wrong:** the project's working rules call for small, descriptive commits on `progress-report`. A checkpoint report that cites "the code" has nothing stable to point to. A stray `git checkout`, like the one used to undo the `sed` slip (`WORKLOG.md:123-128`), could destroy the whole day's work.
- **Fix:** commit in the split the engineer proposed (tooling / spec / runtime / adapters / examples+demo / docs), using the existing identity. Then update `WORKLOG.md` "Commits" and `HANDOFF.md:66-67` with the hashes.

---

## MINOR

1. **Numeric validation is looser than documented.** `src/seqmon/spec.py:311-312`: `cast(value)` accepts `max_calls: 2.9` (it becomes 2) and `max_calls: "5"` (string, it becomes 5). Negative `max_calls`/`threshold` are accepted. `threshold: .nan` and `window: .nan` are accepted: `parse_duration` (`:146`) does `nan <= 0 → False`, a NaN window never evicts, and a NaN threshold never fires. `window: .inf` is accepted. Probe output: `('c', nan, inf)`, `('c2', -5.0, nan)`, `('r3', -1)`. `docs/rule_format.md:192` says "Numbers are type-checked". *Fix:* require `int`/`float` (not `str`), `math.isfinite`, `>= 0`, and an integer for `max_calls`.
2. **Event values are unvalidated.** `src/seqmon/state.py:68,76`: one NaN `magnitude`/attribute makes `total` NaN until the window empties, which silently disables the rule (probe: NaN, then 1e6, does not fire). A negative value (e.g. a refund) offsets real spend (probe: -1000 then +1000 does not fire at threshold 100). *Fix:* reject or clamp non-finite and negative values in `ToolCallEvent.__post_init__` or at `_Window.add`, or document negative values as intended.
3. **`ordering` with `before == after` parses but can never fire.** `src/seqmon/spec.py:422-427` and `evaluator.py:100-102` (the `before` branch returns first). Probe: `fires: [0, 0, 0]`. The oracle hard-codes the same skip (`bench/naive.py:77`), so the differential test cannot catch it. *Fix:* reject at parse time.
4. **Ordering stores every `before` event when it only needs the latest timestamp.** `src/seqmon/evaluator.py:99-107`: only `win.count() > 0` and `win.events[-1]` are used. Up to 10,000 events per ordering rule per session are retained for one float of information. *Fix:* keep `last_before_ts` in state. This also makes the planned "B must be preceded by A" form trivial: fire when `after` arrives and `last_before_ts` is missing or expired. That is evidence the design can be extended without a rewrite, which the report can state.
5. **Dispatch fall-through makes adding a 5th constraint type unsafe.** `src/seqmon/evaluator.py:185`: `else:  # ScopeConstraint` replaced the defensive branch to satisfy mypy `unreachable`. A new constraint class would be routed to `_check_scope` and fail with `AttributeError` at runtime instead of a type error at check time. `_threshold_of` (`:125-132`) has the same pattern. *Fix:* `elif isinstance(rule, ScopeConstraint)` plus `else: typing.assert_never(rule)` (via `typing_extensions` on 3.10), or a dispatch table keyed by type.
6. **Window boundary semantics are undocumented and untested.** `src/seqmon/state.py:86-87` evicts `timestamp <= now - span`, so the window is the half-open interval `(now - span, now]`. An event exactly `span` old is out. That is consistent with the oracle (`bench/naive.py:61`, `> cutoff`), but no test hits the exact boundary (the closest are `tests/test_seqmon.py:93-100` at 2× span and `:158-163`). Equal timestamps behave correctly (probe: rate 2 fires on the third same-second call). *Fix:* state `(now - window, now]` in `docs/rule_format.md` and add boundary tests at `span - ε`, `span` and `span + ε`.
7. **`Violation.describe` is misleading for ordering rules.** `src/seqmon/evaluator.py:64-71` prints "observed 3 > threshold 0" when 3 is a gap in seconds. *Fix:* type-specific wording, e.g. "`after` 3 s after `before` (window 600 s)".
8. **The adapter leaks a concrete type.** `src/seqmon/adapters/fake_agt.py:197`: `FakeAGTInterceptor.__init__` takes `per_call: FakePerCallEngine` although `base.PerCallEngine` exists for exactly this. `from_policy_file` (`:207`) forwards only monitor kwargs, so an `approver` cannot be set through it (the demo therefore always auto-approves; see M3). *Fix:* annotate with `PerCallEngine` and add an `approver` parameter.
9. **`FakePerCallEngine` raises where AGT fails closed.** `src/seqmon/adapters/fake_agt.py:64-84`. Probe: `condition: {field: records, operator: gt, value: "1000"}` raises `TypeError` out of `check()`. AGT catches it and denies (`policies/evaluator.py:236-244`). The module docstring lists "the fail-closed exception path" as not mirrored, but the effect is that the demo interceptor crashes on a policy AGT would handle. *Fix:* wrap `_match` in `try/except` and return a deny verdict, which mirrors AGT.
10. **The compatibility evidence is weaker than "0 disagreements over 36 calls" suggests.** `scripts/check_agt_compat.py:93-99`: 32 of the 36 calls are the demo trace, which matches no rule and takes the default path. Only 4 probes exercise rule matching. The `eq/ne/lt/lte/gte/contains/matches` operators, equal-priority ties, absent fields and the backends-before-defaults path (`policies/evaluator.py:199-202`) are not compared. *Fix:* add a probe table that covers each operator and a priority tie, and report "N rule-matching calls" separately in the WORKLOG and the report.
11. **The differential oracle test does not cover the new code paths.** `bench/naive.py:67` gained `attribute` support, but the random-trace differential test does not generate `attributes`, YAML-configured `max_events_per_window`, or unordered timestamps (M1). The only coverage for attribute sums is one smoke test.
12. **README overhead table is stale against the WORKLOG.** `README.md:186-191` still shows 3.2 µs/event at 100,000 events. `WORKLOG.md:243-250` and `HANDOFF.md:84-85` report 3.32-3.54 µs/event after this checkpoint. *Fix:* regenerate the table, or label it with the date and commit it was measured at.
13. **Small wording inaccuracies in the logs.** `WORKLOG.md:149` says `bench/` and the earlier tests are "unchanged", but `bench/naive.py`, `bench/suites/*`, `experiments/*` and `tests/test_seqmon.py` were edited (lint fixes plus the oracle `attribute` change). The engineer's summary repeats "bench/ and the experiments are untouched". The edits are harmless, but say "behaviour unchanged".
14. **Typing nits.** `SequencePolicy.layout: str` (`spec.py:275`) should be `Literal["agt", "standalone"]`. `ToolCallEvent.metadata: dict` on a frozen dataclass (`events.py:58`) is mutable and unhashable, so `frozen=True` is only shallow.
15. **The headline "one policy file for both layers" needs its caveat everywhere it appears.** It holds only for AGT's lenient pydantic loader. AGT's own JSON schema (`policy_schema.json:40`, `additionalProperties: false`) rejects every example file, so `agentos validate` will flag them. This is disclosed in `docs/rule_format.md:220-233` and `OPEN_QUESTIONS.md` #3, but the report and README must not present the combined file as AGT-valid. Note also that only the top-level layout, `defaults`-style block and `warn/pause/break` verbs come from AGT. The rule body keys (`type`, `window`, `threshold`, `actions`, `response`) are this project's own, and AGT's `quarantine` verb is not accepted.

---

## Honesty audit: claims the code does not support (or only partly)

| Claim | Location | Finding |
|---|---|---|
| "Session state, constant memory ... Done" | `HANDOFF.md:29` | Partly true. Bounded per session by rules × cap; unbounded across sessions (M2). |
| ESCALATE = "human approval before next action" | `spec.py:65`, `response.py:10-11`, `docs/rule_format.md:73` | Not enforced by the monitor. The interceptor asks once, after the call ran; the default approves; the rule is then muted (M3). |
| Demo "ESCALATE at step 7" | `scripts/demo_trace.py` output, engineer summary | True that it fired. It was auto-approved silently and had no effect on the run (M3). |
| "Numbers are type-checked" | `docs/rule_format.md:192` | Strings, floats-for-ints, NaN, inf and negatives pass (MINOR 1). |
| Incremental evaluator agrees with the naive oracle | `README.md` (correctness oracle paragraph) | Only for monotonic timestamps and traces without attributes (M1, MINOR 11). |
| "0 disagreements over 36 calls" | `WORKLOG.md:204`, `docs/rule_format.md:234-235` | Literally true; only 4 calls exercise rule matching (MINOR 10). |
| "bench/ and experiments untouched/unchanged" | Engineer summary, `WORKLOG.md:149` | Files were edited; behaviour is unchanged (MINOR 13). |
| Overhead 3.2 µs/event | `README.md:186-191` | Stale vs. 3.3-3.5 µs/event reported in WORKLOG/HANDOFF (MINOR 12). |
| AGT source facts, pinned-commit absence, schema rejection, history entries | `docs/agt_integration.md`, `OPEN_QUESTIONS.md`, `WORKLOG.md` | **Verified** (see "What I ran myself"). |
| Test/ruff/mypy/demo results, fresh-venv quickstart | `HANDOFF.md:8-14`, README | **Verified** on 3.12.7. Python 3.10 and `run_all.py` byte-identity not re-run. |

## Design fit summary

- **Incremental:** yes. There is no history rescan on the hot path. Sums and distinct counts are maintained on insert and evict (`state.py:63-95`); the per-event cost is O(rules). The only O(n) operations are `reset` (`evaluator.py:217`) and `memory_footprint` (`state.py:173`), both off the hot path.
- **AGT conventions:** the top-level layout and response verbs are AGT-derived and verified. The rule bodies are not (MINOR 15), and the `action` key collides (M4).
- **Adapter:** the interfaces in `adapters/base.py` are clean and honestly labelled. The fake leaks its concrete type (MINOR 8). The interception point chosen (last in `CompositeInterceptor`) is backed by the source.
- **Extensibility:** "must be preceded by" fits the existing window machinery without a rewrite (MINOR 4). The isinstance fall-through should be fixed first (MINOR 5).

## Verdict

**CHANGES REQUIRED**

Required before the checkpoint is accepted:
- B1: write the report.
- M5: commit the work.
- M1-M4: fix them, or, if time does not allow, state each one explicitly in the report as a known limitation with the probe evidence above.

The MINOR items can be scheduled.

---

# Round 2 (re-review of fix round 1, 2026-10-06)

Scope: only the areas changed since Round 1 (the whole tree is still uncommitted,
so "changed" means the files named in the engineer's fix report and the
2026-10-06 fix-round entry in `WORKLOG.md`). Read: `docs/progress_report_1.md`,
`src/seqmon/{state,evaluator,monitor,response,spec,events}.py`,
`src/seqmon/adapters/fake_agt.py`, `tests/test_review_fixes.py`, and the changed
parts of `HANDOFF.md`, `README.md` and `docs/rule_format.md`.

## What I ran myself (Round 2)

| Check | Result |
|---|---|
| `.venv/bin/python -m pytest -q` | 192 passed |
| `.venv/bin/ruff check .` / `.venv/bin/mypy src` (`strict = true`, `pyproject.toml:42`) | All checks passed / no issues in 10 source files |
| `scripts/demo_trace.py` | TERMINATE at step 17, 15 refused, "Escalations: 10, all auto-approved ...". Matches the report. |
| `scripts/demo_trace.py --deny-escalations` | Session ends at step 7, 25 refused. Matches the report. |
| README quickstart in a **recreated** `../review-venv` (Python 3.12, `pip install -e ".[dev]"`) | demo, pytest (192), ruff, mypy clean. `cd / && python -c "import seqmon"` works. |
| `experiments/run_all.py` in a throwaway copy of the tree (`../review-copy`, since deleted) | "All 7 steps passed". `ablation.csv`, `crosscheck.json`, `detection.csv`, `latency_damage.csv`, `threshold_sweep.csv`, `suite_results.jsonl` are **byte-identical** (`cmp`) to `results/`. `overhead.csv` differs (timings: 2.99 / 3.44 / 3.44 / 3.45 µs/event). The "byte-identical except overhead" claim is verified. |
| `docs/figures/*.png` vs `results/figures/*.png` | Identical (`cmp`). |
| Report numbers vs `results/*.csv` | Every number in §1 and §5 checked: detection table, ablation 13/21, threshold rows 3000/4000/5000/8000, latency (16/20, 5,100-6,000 rows, 379/h vs 343/h), overhead table, crosscheck (29 agree, 1 scaled backstop-flush differs), slow-exfiltration damage 1,260/10,860. All match. Code size 2,054 lines / tests 1,264 lines / 5 files: match `wc -l`. |
| AGT claims new in the report (§4.4-4.5) | Verified in `../agt-src`: `4b8b2114` (2026-07-29, "replace the v4 policy language with ACS v5"), `0d674bed` (2026-06-17, accumulated-context governance), `policies/{schema,evaluator}.py` present at `v5.0.0`. |
| "Scope drift working since the first commit" | Verified: `git show 96b5b9e:agent-sequence-monitor/src/seqmon/spec.py` has `class ScopeConstraint` (line 130). |

Probes were run from a scratch script against `src/` only. Outputs are quoted below.

## Status of Round 1 items

| Item | Status | Evidence |
|---|---|---|
| **B1** report missing | **Closed.** | `docs/progress_report_1.md` exists. It covers the timeline, architecture, rule types, the demo, the AGT study, results, fixes and limitations. Every number I checked traces to `results/` or a command (table above). It states the simulated AGT engine, the scripted traces, the 2/10 evasion result, the schema rejection and the auto-approved demo escalations. Two wording issues remain (R2-m3, R2-m4), plus the cross-session timestamp effect (R2-M1), which it does not mention. |
| **M1** timestamps | **Closed for late and equal timestamps. Partly open for future timestamps.** | Late events are clamped up to the session's latest timestamp (`evaluator.py:229-233`). The oracle applies the same contract, and 20 randomized late-arrival differential tests pass (`tests/test_review_fixes.py:110-141`). The rate probe t = 100, 0, 105 now fires in both evaluators. Window boundary probe: `max_calls: 1, window: 10s`, events at 0 and 10 → no fire; at 0 and 9.999 → fires. This matches the documented `(now - window, now]`. Far-future stamps are bounded only when a `clock` is passed, and none of the shipped entry points pass one. See R2-M1 for a new cross-session consequence. |
| **M2** memory across sessions | **Closed in the trusted-timestamp case. Reopened by R2-M1.** | Idle expiry is correct at the boundary: the return-path drop uses `ts - last >= ttl` (`evaluator.py:234`), and windows evict `<= now - span`. So when `ttl` = the longest window, a dropped session has no live event. `fired` moved into `SessionState` (`state.py:153`). `reset` is O(1). `history` is a `deque(maxlen=1000)` (`response.py:164`). `footprint` uses `peek` (`monitor.py:134`). Baseline probe: 5,000 one-event sessions 10 s apart with a 1 h TTL leave **368** sessions held. `FakeAGTInterceptor.terminated` is still unbounded. This is disclosed (`HANDOFF.md:32`, `README.md:182-184`, `fake_agt.py:242-244`) and acceptable for a demo adapter. |
| **M3** approval mutes rule | **Closed.** | `SequenceMonitor.approve` → `IncrementalEvaluator.rearm` (`monitor.py:121-127`, `evaluator.py:305-314`). The interceptor calls it on approval and terminates on refusal (`fake_agt.py:283-294`). Probe: 500 out-of-scope calls with a counting approver gave **499** asks and 499 `EscalationRecord`s. The demo prints that its escalations are auto-approved. |
| **M4** `action:` / unknown keys | **Closed.** | `_check_keys` (`spec.py:360-389`). Probes: `action: deny` is rejected with the hint pointing to `response:`. `priority: 5` and `max_outsde: 1` are rejected with the field path and the allowed fields. `before == after` is rejected. Duplicate rule names are rejected. |
| **M5** uncommitted | **Open (deferred to the user).** | `git status`: still 21 modified + 10 untracked paths. No commits since `ec4b486`. `origin/progress-report` is at `ec4b486`. The engineer's reason holds: the user's own request does not mention commits, and this session's rules only allow committing when the user asks. I do not count it against the code. But see R2-M2, which covers what this does to the report. |
| MINOR 1, 3, 5, 6, 7, 8, 9, 12, 15 | Closed (spot-checked). | Numbers: `spec.py:288-320` (strings, NaN and negatives rejected). `before == after` rejected. `assert_never`-style guard: `evaluator.py:143-170,272-273`. Boundary tested. Ordering `describe` wording: `evaluator.py:85-86`. `per_call: PerCallEngine` + `approver` in `from_policy_file`: `fake_agt.py:232-262`. Fail-closed: `fake_agt.py:152-163`. README overhead table dated. |
| MINOR 2 | Closed as a documented decision. | Non-finite values are rejected (`events.py:62-76`). Negatives are allowed by design and documented (`events.py:67-68`). |
| MINOR 4, 10, 11 (YAML cap), 14 (metadata) | Deferred, logged in `WORKLOG.md`. | Acceptable for this checkpoint. Listed under known limitations below. |

---

## New findings (Round 2)

### MAJOR

#### R2-M1. Idle-session sweep couples sessions through timestamps: one far-future stamp in any session wipes every other session and then disables expiry
- **Where:** `src/seqmon/evaluator.py:239-245` (global sweep with `now = ts` of whichever event arrives, and `_next_sweep = ts + ttl/16` set from it). `src/seqmon/state.py:243-259` (`expire_idle`). Docstrings that claim this is result-neutral: `evaluator.py:241-242` ("this only affects memory, never results") and `state.py:30-33` ("a dropped session had no live event in any window anyway").
- **What's wrong:** the per-session clamp from M1 keeps each session monotonic. But the sweep compares **every** session's `last_timestamp` with the timestamp of the current event, which can come from a different session. Without a `clock` (the default for `SequenceMonitor`, `FakeAGTInterceptor.from_policy_file`, the demo and `bench/score.py`), this has three effects. Probes, policy `cumulative threshold 100, window 1h, terminate`:
  1. **Cross-session evasion.** Session A adds 90 at t=0. Session B sends one event at t=1e12. Session A adds 90 at t=2. Result: `A footprint after B@1e12: 0`, and the second 90 does **not** fire (expected 180 > 100). In Round 1, a bad stamp only cleared its own session. Now one session can clear everyone else's state, including their `fired` records.
  2. **The M2 fix is undone.** After that single 1e12 stamp, `_next_sweep` is about 1e12. So no sweep runs again for real-time sessions. 5,000 more one-event sessions spanning 50,000 s leave **5,002** sessions held (`expired 1`), against **368** without the bad stamp. Memory grows without limit again.
  3. **Wrong results without any adversary.** Sessions on independent time bases (for example, recorded traces replayed into one monitor, or per-agent clocks): A adds 60 at t=0, B has an event at t=1.7e9, A adds 60 at t=10. A is swept, and the A breach is **missed** (`False`). The same two A events in an isolated monitor fire (`True`).
  4. With a `clock`, the forward bound limits this to `max_clock_skew`. Even so, a victim session whose only event still had 30 s left in its window was dropped after another session sent a stamp clamped to `clock + 60` (`victim footprint ... 0`). That is small, but it is a cross-session effect, and it is not documented.
- **Why it matters:** session isolation is the basic contract of a per-session monitor. The fix round turned a per-session limitation ("timestamps must be trusted", which is disclosed) into a cross-session one that is **not** disclosed. `docs/rule_format.md:197-201`, `HANDOFF.md:95-96` and report §7.6 say only that a far-future stamp "empties the windows". Report §6.2 says the memory problem is fixed with no caveat. `differential` tests cannot catch this: `random_unordered_trace` uses one shared time base with lateness of at most 400 s against a 3,600 s TTL (`tests/test_review_fixes.py:110-124`).
- **Fix (any one of these, smallest first):**
  - Drive the sweep from a monitor-wide high-water mark that can only advance by a bounded step per event. Never let an event's `ts` push `now` more than `max_clock_skew` past the previous mark. Reset `_next_sweep` if the mark falls back. Or sweep from `clock()` only, and skip the sweep entirely when no clock is given.
  - Make `clock=time.time` the default in the live paths (`FakeAGTInterceptor.from_policy_file`, and any real interceptor). Keep `clock=None` only in `bench/`, where traces share one time base.
  - Whatever the choice, correct the two docstrings, and add the probe above as a test ("a future stamp in session B does not change session A's verdict"). Add one sentence each to `docs/rule_format.md` §timestamps, `HANDOFF.md` and report §7.6: "one time base per monitor; without a clock, one bad timestamp affects all sessions and stops idle expiry".

#### R2-M2. The report points readers to code that does not contain the reported work
- **Where:** `docs/progress_report_1.md:6-7` (`**Code:** https://github.com/prashansapkota/agent-sequence-monitor, branch progress-report`) and §9 "How to reproduce".
- **What's wrong:** `origin/progress-report` and local `HEAD` are both `ec4b486` (2026-09-28). Everything the report describes from this checkpoint is uncommitted: the fix round, `adapters/`, `scripts/demo_trace.py`, `policies/examples/`, `docs/`, `tests/test_review_fixes.py` and the report itself. A grader who follows the link gets 110 tests, no demo, no `--deny-escalations`, and none of the §6.2 fixes.
- **Why it matters:** the report's credibility rests on "every number comes from a file in this repository". At the cited location, that is currently false. This is the user-visible consequence of M5, so it needs a decision before the report is submitted, not a code change.
- **Fix:** this is the user's call, since commits were not requested. Either (a) the user commits (the six-commit split in `WORKLOG.md` is sensible) and pushes before submitting, and the report then cites the commit hash; or (b) the report's Code line says "working tree on branch `progress-report` (uncommitted as of 2026-10-06; last commit `ec4b486`)".

### MINOR

- **R2-m1. Unsafe values are accepted for the new knobs.** `evaluator.py:196-215`, `monitor.py:54-66`. `max_clock_skew=-5` is accepted. Every stamp is then clamped *below* the clock, and a `clock` that lags the trace silently rewrites every timestamp. A positive `idle_ttl` smaller than the longest window is also accepted, and it drops live state: `idle_ttl=1` turns "90 then 90 two seconds later" (180 > 100) into **no fire**. *Fix:* reject `max_clock_skew < 0`. Reject (or at least warn on) `0 < idle_ttl < max window`, or document it in the docstring as "results may change".
- **R2-m2. Top-level typos are still silent in the standalone layout.** `spec.py:512-545`. `rules: [...]` plus `foo: 1` parses. Rule-level keys are now strict, and so is `sequence_defaults` (`spec.py:500`), so this is the last silent spot. In the AGT layout, unknown top-level keys rightly belong to AGT. *Fix:* in the standalone layout, reject keys outside `{version, name, description, rules, sequence_defaults}` (or whatever the Agent-OS header set is).
- **R2-m3. Overhead wording in the report.** `docs/progress_report_1.md:299-303` says "after them, repeated runs gave 3.4-3.6". The report's own table in that section has 3.2 at 1,000 events (from `overhead.csv`: 3.15), and my rerun gave 2.99-3.45. *Fix:* "about 3.0-3.6 µs/event across runs and sizes". Keep the "noisy, uncontrolled" caveat that is already there.
- **R2-m4. "All four are fixed" overstates M1.** `docs/progress_report_1.md:338`. The far-future half of M1 is fixed only when a `clock` is configured, and nothing shipped configures one (R2-M1). *Fix:* "Four problems; three fixed outright, the timestamp one fixed for late events and bounded for future ones only when a clock is configured (§7.6)."

### Not findings (checked and fine)
- **Incremental design:** still no rescan. The sweep is O(dropped + 1) amortised, and `rearm` is O(rules approved).
- **The re-arm path:** the interceptor asks the approver synchronously, before `submit` returns. So the next call in the session cannot run until the answer is in. That matches "approval before next action" for this adapter. The monitor docstring (`response.py:10-16`) is honest that a real runtime must provide the hold.
- **The edited test** (`test_scalar_actions_and_allowed_are_accepted` now uses `actions:`) is a deliberate, logged change that follows from M4.

---

## Known limitations (still open after Round 2)

These are disclosed in the report, `HANDOFF.md` or `WORKLOG.md` unless marked otherwise:
1. **M5 / R2-M2:** the work is uncommitted, and the report's code link points at `ec4b486`. This is the user's decision.
2. **R2-M1:** cross-session timestamp coupling in the idle sweep. **Not yet disclosed.**
3. Without a `clock`, timestamps must be trusted (disclosed). No shipped entry point sets a clock.
4. `FakeAGTInterceptor.terminated` grows by one id per terminated session (disclosed, OPEN_QUESTIONS #12).
5. MINOR 4 (ordering keeps every `before` event instead of a timestamp), MINOR 10 (only 4 of 36 compat calls match a rule), MINOR 11 (no YAML-configured cap in the differential test), MINOR 14 (`metadata` is a mutable dict on a frozen dataclass): deferred and logged.
6. Project-level limits stated in report §7: simulated AGT engine, scripted traces, 8/10 evasions succeed, detection never precedes the tripping call, combined file fails AGT's JSON schema, "must be preceded by" not implemented.

## Verdict (Round 2)

**CHANGES REQUIRED**

The Round 1 blocker is resolved. The report is substantive and accurate against the code and `results/`, and I reproduced its numbers independently. M3 and M4 are cleanly fixed. M1 and M2 are fixed for the normal case. The remaining required changes are small:
- **R2-M1:** stop the idle sweep from letting one session's timestamp drop other sessions, or turning off expiry (or default a clock on the live path). Correct the two "never results" docstrings and add the cross-session test. At minimum, disclose it in report §7.6 and `docs/rule_format.md`.
- **R2-M2 / M5:** the user should decide before submission: commit and push, then cite the hash; or reword the report's Code line to say the work is uncommitted.
- R2-m3 and R2-m4 are one-line report edits and should go in with the above. R2-m1 and R2-m2 can be scheduled.

---

# Round 3 (re-review of fix round 2, 2026-10-06)

Scope: only what fix round 2 changed (still uncommitted; `HEAD` = `origin/progress-report` = `ec4b486`, `git status` 31 paths), plus the Round 2 items. Read: `src/seqmon/evaluator.py` (module docstring, `__init__`, `on_action`), `src/seqmon/state.py` (docstring, `StateStore`), `src/seqmon/monitor.py` (new arguments), `src/seqmon/spec.py:492-566`, `tests/test_review_fixes.py:377-465`, report §1, §6.2, §7, and the changed passages of `README.md`, `HANDOFF.md` and `docs/rule_format.md`.

## What I ran myself (Round 3)

| Check | Result |
|---|---|
| `.venv/bin/python -m pytest -q` | 208 passed |
| `.venv/bin/ruff check .` / `.venv/bin/mypy src` | All checks passed / no issues in 10 source files |
| `scripts/demo_trace.py` / `--deny-escalations` | TERMINATE at step 17, 15 refused, 10 auto-approved / session ends at step 7, 25 refused. Unchanged, matches report. |
| `pip install -e ".[dev]"` into `../review-venv`, then pytest; `cd / && python -c "import seqmon"` | 208 passed; import ok |
| `experiments/run_all.py` in a throwaway copy (`../review-copy`, deleted afterwards) | "All 7 steps passed". `ablation.csv`, `detection.csv`, `latency_damage.csv`, `threshold_sweep.csv`, `crosscheck.json`, `suite_results.jsonl` identical to `results/` (`cmp`). `overhead.csv` timings only: 2.98 / 3.5 / 3.45 / 3.54 µs/event, inside the report's "about 3.0-3.6". |
| Probes (scratch script against `src/`, policy `cumulative sum > 100, window 1h, terminate`) | See below. |

Probe outputs:
- A +90 at t=0, B one event at t=1e12, A +90 at t=2: **default fires 180**, **with a clock fires 180**, with `shared_time_base=True` fires nothing (the documented, opt-in coupling).
- 5,000 one-event sessions 10 s apart, default config: **5,000 held** (no sweep, as documented).
- `IncrementalEvaluator(..., max_clock_skew=nan | -1)` → `ValueError`; `idle_ttl=1` → `ValueError` with a clear message ("use >= 3600.0, or 0 to disable expiry"). `idle_ttl=-5`, `nan`, `inf` are accepted (see R3-m2).
- Clock whose readings run 600 s ahead of the event stamps (`max_clock_skew=60`), A +60 at stamp 0, other sessions keep the sweep running, A +60 at stamp 3,350 (same 1 h window): **no fire**, `clamped_events == 0`. The same two events in an isolated monitor fire 120. See R3-m1.

## Status of Round 2 items

| Item | Status | Evidence |
|---|---|---|
| **R2-M1** cross-session sweep | **Closed.** | `evaluator.py:284-299`: the sweep's "now" is `clock() - max_clock_skew`, or the event stamp only if `shared_time_base=True`; otherwise there is no sweep. The cross-session probe now fires in the default and the clock configurations. With a clock, a 1e12 stamp is clamped (`evaluator.py:272`) and cannot push `_last_sweep`. With `shared_time_base`, `now < last_sweep` re-arms the sweep (`:296`), and it stays O(1) per event because `expire_idle` stops at the first live LRU entry (`state.py:257-261`). The "never affects results" docstrings are corrected (`evaluator.py:25-43`, `state.py:26-40`). Seven new tests cover the probes (`tests/test_review_fixes.py:377-431`). The trade-off (no cross-session expiry by default) is disclosed in report §6.2 and §7.6, `README.md:183-188`, `HANDOFF.md:32,102-103` and `docs/rule_format.md:207-215`. |
| **R2-M2** report links to code without the work | **Closed by rewording.** | `docs/progress_report_1.md:6-11` now states that the work is uncommitted, that the link shows `ec4b486`, and what that commit lacks. Accurate against `git log`. |
| **M5** uncommitted | **Open (the user's decision, not counted against the code).** | Still 31 changed or untracked paths and no commits since `ec4b486`. |
| **R2-m1** unsafe knob values | **Closed** (one leftover, R3-m2). | `evaluator.py:243-252`. Tests at `:434-451`. |
| **R2-m2** standalone top-level typos | **Closed.** | `spec.py:492-494, 535-543`. The AGT layout still allows foreign keys (`:460` test). |
| **R2-m3** overhead wording | **Closed.** | Report §5.4 line 305: "about 3.0-3.6 µs/event". My rerun (2.98-3.54) is inside it. |
| **R2-m4** "all four fixed" | **Closed, with one leftover** (R3-m3). | Report §6.2 lines 341-344. |

## New findings (Round 3)

No BLOCKER and no MAJOR.

### MINOR

- **R3-m1. With a clock, a stamp that lags the clock by more than `max_clock_skew` can drop a live session, and nothing records it.**
  - **Where:** `src/seqmon/evaluator.py:270-272` (only the upper bound `now + max_clock_skew` is enforced) and `:288-299` (the sweep at `clock() - max_clock_skew`).
  - **What's wrong:** the probe above. The interceptor's stamps run 10 minutes behind its clock (for example, stamps taken on the agent host and a clock on the monitor host). A live 60 + 60 breach is missed, and `clamped_events` stays 0, so there is no trace of it. The condition is documented (`evaluator.py:34-36`, `docs/rule_format.md:207-209`), and the clock is opt-in, which no shipped path sets. That is why this is MINOR.
  - **Why it matters:** a clock is what the docs recommend for live use, and a clock offset between hosts is ordinary. The failure is a silent missed detection.
  - **Fix:** with a clock, clamp into `[clock() - max_clock_skew, clock() + max_clock_skew]` and count it in `clamped_events`. Then a session the sweep could drop never has a live window event. Or, at minimum, count the stamps that fall below the lower bound so the integrator can see them.
- **R3-m2. `idle_ttl` of NaN or +inf is accepted.**
  - **Where:** `evaluator.py:243-253`.
  - **What's wrong:** NaN fails every `ttl > 0` test, so it silently disables expiry, as `0` does. The docstring documents only "`0` or less".
  - **Fix:** reject non-finite values other than an explicit `math.inf`, or document them. This is cosmetic.
- **R3-m3. Report §6.2 still counts memory as "fixed outright".**
  - **Where:** `docs/progress_report_1.md:341-342` ("Three are fixed outright") and the table row at `:350` ("Sessions idle longer than the longest rule window are dropped").
  - **What's wrong:** in every shipped entry point (no clock, no `shared_time_base`), the original M2 probe still reproduces: sessions that never return are all held (5,000 of 5,000). The paragraph at `:362-375` and §7.6 disclose this, so it is a wording issue, not a hidden one.
  - **Fix:** say "two fixed outright; memory is fixed per session and on return, while the cross-session sweep needs a clock or `shared_time_base` (below)". Or change the row to say "dropped when they return, or by a sweep when a clock is configured".

### Not findings (checked and fine)
- The edited test `test_idle_sessions_are_expired` (`tests/test_review_fixes.py:160`) now passes `shared_time_base=True`. That is correct: its trace is on one time base, and the change is logged.
- `bench/naive.py` (the oracle) never had a cross-session sweep. Removing it from the default changes memory only, not the differential results. The `results/` files reproduce byte for byte.
- `shared_time_base` with a 1e12 stamp holds 5,001 sessions because of head-of-line blocking in the LRU order. This is documented in the report (§6.2) and in the test name.

## Known limitations (still open after Round 3)

1. **M5:** the work is uncommitted, and `origin/progress-report` is `ec4b486`. The report now says so (R2-M2 closed). Committing and pushing before submission is the user's decision.
2. **No cross-session expiry by default.** Sessions that never return are kept unless a `clock` or `shared_time_base=True` is set, and no shipped entry point sets either. Disclosed (report §7.6, README, HANDOFF).
3. **Timestamps must be trusted without a clock.** With a clock, stamps lagging it by more than `max_clock_skew` can silently drop live state (R3-m1). The condition is documented; the missing signal is not.
4. `FakeAGTInterceptor.terminated` grows by one id per terminated session. Disclosed.
5. MINOR 4, 10, 11 and 14 from Round 1 are deferred and logged in `WORKLOG.md`.
6. Project-level limits in report §7: simulated AGT engine, scripted traces, 8 of 10 evasions succeed, detection never precedes the tripping call, the combined file fails AGT's JSON schema, "must be preceded by" is not implemented, n = 9 benign scenarios.

## Verdict (Round 3)

**APPROVE**

Every Round 2 code finding is fixed and tested, and I reproduced each fix with my own probes. R2-M2 is resolved by accurate rewording. The report's numbers still trace to `results/`, which I regenerated independently. The three new MINORs (R3-m1 to R3-m3) can be scheduled, and none of them changes a reported result. The one open process item (M5: commit and push before submission, then cite the hash) is the user's call and is listed above, not hidden.
