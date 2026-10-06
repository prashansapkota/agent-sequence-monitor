# Claims in Progress_Report_1.docx and their sources

Every number and factual claim in `docs/report/Progress_Report_1.docx` (built by
`docs/report/build_report.py`), next to the file or command it comes from.
Checked on 2026-10-06. Paths are relative to the repository root.

## 1. Project Overview

| Claim | Source |
|---|---|
| AGT source read at tag v3.5.0 | `docs/agt_integration.md` lines 10-16; `WORKLOG.md` 2026-10-06 §1 |
| AGT's evaluator sorts by priority, first match decides, keeps no state between calls | `docs/agt_integration.md` "Verified facts" item 4; `REVIEW.md` "What I ran myself" (`policies/evaluator.py:171-197`) |
| Kernel docstring: "never looks up prior requests" | `docs/agt_integration.md` item 5 (`stateless.py`); `REVIEW.md` (`stateless.py:19-20`) |

## 2. Work Completed

| Claim | Source |
|---|---|
| `sequence_rules` top-level key, optional `sequence_defaults` | `docs/rule_format.md` "Where the rules go"; `src/seqmon/spec.py` |
| PolicyDocument has version/name/description + rules; `a2a_conversation_policy` is a sibling section; `sequence_defaults` mirrors `defaults`; warn/pause/break verbs | `docs/rule_format.md` "Which AGT conventions this mirrors" (lines 241-262) |
| log/escalate/terminate accepted as aliases | `WORKLOG.md` 2026-10-06 §4 (`Response` aliases); `docs/rule_format.md` "Responses" |
| AGT pydantic loader accepts the combined files and ignores sequence keys | `docs/rule_format.md` "Does AGT accept the combined file?"; `scripts/check_agt_compat.py` |
| Rule body keys are the project's own | `docs/progress_report_1.md` §4 last paragraph |
| Four constraint types (cumulative sum/count/distinct, rate per-tool or total, ordering, scope with `max_outside`) | `src/seqmon/spec.py`; `HANDOFF.md` status table; `tests/test_rate.py` (per-tool vs total) |
| SpecError starts with the field path; unknown keys rejected | `WORKLOG.md` 2026-10-06 §3 and fix round 1 M4; `docs/rule_format.md` |
| Four example policies in `policies/examples/` | `ls policies/examples` -> agt_combined, budget, exfiltration, rate |
| 2,133 lines in `src/seqmon/` | `wc -l src/seqmon/*.py src/seqmon/adapters/*.py` -> 2133 total |
| ToolCallEvent fields; attributes such as records, cost_usd | `src/seqmon/events.py`; `WORKLOG.md` 2026-10-06 §4 |
| One sliding window per rule per session: deque + running sum + per-resource counter | `src/seqmon/state.py` `_Window` (lines 52-112) |
| `IncrementalEvaluator.on_action` in `evaluator.py` | `src/seqmon/evaluator.py` line 259 |
| LOG/ESCALATE/TERMINATE; EscalationHook/TerminationHook; approval re-arms the rule | `src/seqmon/response.py`; `WORKLOG.md` fix round 1 M3 |
| Adapters: PerCallEngine, CallFeed, FakePerCallEngine, FakeAGTInterceptor | `src/seqmon/adapters/`; `WORKLOG.md` 2026-10-06 §4 |
| Naive evaluator `bench/naive.py`; property tests vs independent reference | `TEST_REPORT.md` row `tests/test_property_incremental.py`; `tests/naive_reference.py` |
| Cap default 10,000, configurable; manual cap kept instead of `deque(maxlen)` and why | `WORKLOG.md` 2026-10-06 §4 ("Decision: the manual cap is kept ..."); `src/seqmon/state.py` |
| Bound is per session (rules x cap); cross-session expiry only with clock or `shared_time_base` | `HANDOFF.md` status table and "Known weaknesses"; `WORKLOG.md` fix round 2 R2-M1 |
| Interception point `ToolCallInterceptor.intercept`; CompositeInterceptor returns first deny | `docs/agt_integration.md` items 1-2 |
| 36 calls compared, 0 disagreements, only 4 match a rule | `WORKLOG.md` 2026-10-06 §4; `docs/progress_report_1.md` §4; `REVIEW.md` "What I ran myself" |
| 30 seeded scenarios: 11 adversarial, 10 evasion, 9 benign; scorer; four experiments; `run_all.py`; demo | `git show ec4b486` message; `WORKLOG.md` 2026-09-28; `docs/evidence/experiments_output.txt` |
| Timeline table rows | `HANDOFF.md` "What exists" table; `docs/progress_report_1.md` §2 |
| "B must be preceded by A" not implemented | `HANDOFF.md` item 2; `OPEN_QUESTIONS.md` #7 |
| Scope evaluator built 2026-09-15, in first commit 96b5b9e | `git log --date=short`; `git show 96b5b9e:agent-sequence-monitor/src/seqmon/evaluator.py` has `_check_scope` (line 92) |
| Bench scripts use hard-coded allow; demo uses FakePerCallEngine | `HANDOFF.md` "What is incomplete" item 1 |

## 3. Evidence of Progress

| Claim | Source |
|---|---|
| Last commit ec4b486, 2026-09-28; 2026-10-06 work uncommitted | `git log --format='%h %ad %s' --date=short`; `git status`; `HANDOFF.md` item 6 |
| Listing 1 | read at build time from `policies/examples/exfiltration.yaml` (`message:` lines dropped) |
| Listing 2 | read at build time from `src/seqmon/state.py` `_Window.add`, `_Window._evict` (comments/docstrings dropped) |
| Listing 3 and demo numbers: per-call rule denies > 1,000 rows; 400-row pages; ESCALATE step 7; TERMINATE step 17 at 5,260 rows; 15 later calls refused incl. external send; 32 calls / 10,860 rows without the layer; 10 escalations auto-approved | `docs/evidence/demo_output.txt`; `policies/examples/agt_combined.yaml` (`cap-single-query`, header comment) |
| 426 tests, 412 passed, 14 xfailed, 9 bug IDs | `docs/evidence/test_run.txt`; `TEST_REPORT.md` "Counts"; re-run `.venv/bin/python -m pytest -q` on 2026-10-06 -> `412 passed, 14 xfailed` |
| 12 test files | `ls tests/test_*.py \| wc -l` -> 12 |
| Coverage 96%, 823 statements, 33 missed | `docs/evidence/coverage.txt` |
| ruff and mypy strict clean | re-run 2026-10-06: `ruff check .` -> "All checks passed!"; `mypy src` -> "Success: no issues found in 10 source files"; strict per `pyproject.toml` / `WORKLOG.md` §2 |
| 11/11 adversarial, 2/10 evasion, 0/9 benign | `docs/evidence/experiments_output.txt` (run_suite summary) |
| Naive 10,491 us/event vs incremental 3.6 us/event at 100,000 events | `docs/evidence/experiments_output.txt` (experiments/overhead.py table) |
| Figure 1: 2.3-2.6 us/event, 100-100,000 events; 3,369 KiB; 12,050 retained; 6 x 10,000 bound; Apple M1, CPython 3.12.7, 6 rules, 1 event/s | `bench/results/overhead.csv`; `bench/results/machine.txt`; `TEST_REPORT.md` "Overhead" |
| Figure 1 image | `bench/results/overhead.png` |
| Figure 2 image and caption (distinct-table caught by scope first) | `docs/evidence/figures/detection.png`; `docs/evidence/experiments_output.txt` detection table |

## 4. Challenges

| Claim | Source |
|---|---|
| Scope rule kept a lifetime count; fixed to sliding window; regression tests; slow-scope-drift now missed | `WORKLOG.md` 2026-09-28; `tests/test_seqmon.py::test_scope_tolerance_is_per_window_not_lifetime` |
| 10-hour benign session, one stray lookup every two hours | `bench/suites/benign.py` `occasional_lookups` (lines 139-153) |
| 8 of 10 evasions missed and their kinds | `docs/evidence/experiments_output.txt`; `HANDOFF.md` "Known weaknesses" |
| backstop-flush: about 1,000 zero-row calls, 10,000-event cap; options listed | `HANDOFF.md` "Known weaknesses"; `OPEN_QUESTIONS.md` #8 |
| Distinct-table rule shadowed; ablation 13/21 either way; scope fires before 9 tables | `docs/evidence/experiments_output.txt` ablation table; `docs/evidence/figures/detection.png` note |
| 5ed63c6e not public; v3.5.0 used | `OPEN_QUESTIONS.md` #1; `WORKLOG.md` 2026-10-06 §1 |
| AGT main removed the YAML format on 2026-07-29 | `OPEN_QUESTIONS.md` #2 |
| JSON schema rejects combined files (additionalProperties: false) | `OPEN_QUESTIONS.md` #3 |
| ToolCallRequest has no session id, timestamp or result size | `docs/agt_integration.md` item 1; `OPEN_QUESTIONS.md` #4-5 |
| Review findings: timestamps (1e12 event emptied every window), memory per session, approval muting, `action: deny` | `REVIEW.md` M1-M4; `WORKLOG.md` fix round 1 |
| Second round: sweep coupling sessions; fixed with clock / `shared_time_base`; sessions kept by default (BUG-009) | `WORKLOG.md` fix round 2 R2-M1; `BUGS.md` BUG-009 |
| Result files byte-identical after both rounds | `WORKLOG.md` fix round 1 "Effect on results" and fix round 2 "Verification" |
| Nine bugs; BUG-006 major; BUG-001/002 with 500 x $0.10 vs $50 | `BUGS.md` table and BUG-001, BUG-006 |
| Parser accepted bools as numbers; relative paths; macOS hidden .pth workaround | `WORKLOG.md` 2026-09-28 "Problems found and fixed" |

## 5. Next Steps

| Claim | Source |
|---|---|
| Store only last enabling timestamp (MINOR 4) | `WORKLOG.md` fix round 1 "Not done" item 4; `REVIEW.md` line 224 |
| Interceptor last in a CompositeInterceptor; open questions 2-5 | `docs/agt_integration.md`; `OPEN_QUESTIONS.md` #2-5 |
| Clock on the live path | `WORKLOG.md` fix round 2 trade-off paragraph |
| Fix options for BUG-001/002 (minor units, `math.fsum`) | `BUGS.md` BUG-001 "Possible fix" |
| MCPSlidingRateLimiter, BudgetTracker comparison | `OPEN_QUESTIONS.md` #6; `docs/agt_integration.md` item 6 |

## Dropped or changed while checking

- A reason for building scope drift early ("it reuses the cumulative rule's
  window logic") was in a draft. No repo artifact states a reason, so it was
  replaced by the traceable fact that its evaluator is in commit `96b5b9e`.
- A draft caption said memory reached "3.4 MiB". `bench/results/overhead.csv`
  gives 3,369.0 KiB (3.29 MiB), so the caption now says 3,369 KiB.
- The overhead numbers of `experiments/overhead.py` in `README.md` /
  `HANDOFF.md` (about 3.0-3.6 us/event) and of `bench/overhead.py` (2.3-2.6)
  come from different benchmarks. The report uses the `bench/overhead.py`
  figure and cites the single `experiments/overhead.py` comparison from
  `docs/evidence/experiments_output.txt`, naming each script.
