# Handoff: state of seqmon at Progress Report 1 (2026-10-06)

**The progress report is `docs/progress_report_1.md`.** Read with
`WORKLOG.md` (history, with dates; the two fix rounds answering `REVIEW.md`
are the last entries), `OPEN_QUESTIONS.md`, `docs/rule_format.md` and
`docs/agt_integration.md`.

## How to check everything in 1 minute

```bash
.venv/bin/python -m pytest -q          # 208 passed
.venv/bin/ruff check .                  # All checks passed!
.venv/bin/mypy src                      # Success: no issues found in 10 source files
.venv/bin/python scripts/demo_trace.py  # TERMINATE at step 17; escalations auto-approved
.venv/bin/python scripts/demo_trace.py --deny-escalations  # session ends at step 7
.venv/bin/python experiments/run_all.py # regenerates results/ (gitignored)
```

## What exists

Compared with the proposal timeline: weeks 1–2 (rule format), 3–5 (runtime),
6–8 (harness and scenarios) and a first pass of 9–11 (experiments) are done.

| Proposal component | Where | Status |
|---|---|---|
| Rule spec in AGT YAML conventions, same file as per-call rules | `src/seqmon/spec.py`, `docs/rule_format.md`, `policies/examples/` | Done (`sequence_rules:`). Standalone layout still supported. |
| Cumulative aggregate over a rolling window | `CumulativeConstraint` (sum / count / distinct; `attribute:` for named quantities) | Done |
| Rate limits, per tool or total | `RateConstraint` (`actions` empty = total) | Done |
| Ordering: B must never follow C | `OrderingConstraint` (`before` → `after` within window) | Done |
| Ordering: B must be preceded by A | — | **Not implemented** |
| Scope drift vs declared scope | `ScopeConstraint` | Done (built ahead of plan, 2026-09-15) |
| Session state, bounded memory | `src/seqmon/state.py` (per-rule deques, running sums, configurable cap, idle-session expiry) | Done. Bounded per session by rules x `max_events_per_window`; a session returning after the longest window starts fresh; violation history capped at 1,000. Abandoned sessions are swept only with a `clock` or `shared_time_base=True` (R2-M1); by default they are kept. Also unbounded: `FakeAGTInterceptor.terminated` (one id per terminated session). |
| Incremental evaluator | `IncrementalEvaluator.on_action` (alias `SequenceEvaluator.observe`) | Done, O(rules) per event. Late timestamps clamped; future ones bounded only with a `clock`. |
| Responses log / escalate / terminate | `Response` (aliases of warn / pause / break), `Decision`, `EscalationHook` / `TerminationHook`, `SequenceMonitor.approve` | Done. Default hooks are no-ops. The runtime must hold the session on ESCALATE; approval re-arms the rule. |
| AGT adapter | `src/seqmon/adapters/` (`PerCallEngine`, `CallFeed`, `FakeAGTInterceptor`) | Interfaces and fake only. **No real AGT hook.** |
| Harness and scenarios | `bench/` (30 seeded scenarios), `bench/score.py`, `bench/naive.py` oracle | Done (scripted traces only) |
| Experiments | `experiments/` (detection, damage before detection, threshold sweep, overhead) | Done for scripted traces |

Results on scripted traces against `policies/example.yaml`, reproduced on
2026-10-06 after the review fixes (byte-identical to the outputs before
them): adversarial 11/11 detected, evasion 2/10, benign false positives 0/9.
Overhead is about 3.4-3.5 µs/event, flat from 5,000 to 100,000 events. The
naive rescan takes about 10,000 µs/event at 100,000 events. This is a
microbenchmark on one machine.

## What is incomplete

1. **No real AGT integration.** The per-call engine is simulated: hard-coded
   `allow` in `bench/`, and `FakePerCallEngine` in the demo. The latter matched
   AGT's real `PolicyEvaluator` on 36 calls (`scripts/check_agt_compat.py`).
   The real hook is blocked on open questions 3–5 (schema rejection, where
   session id and timestamp come from, result sizes only known after
   execution).
2. **The "must be preceded by" ordering form** is missing.
3. **No LLM-driven agent.** All traces are hand-built and seeded. End-to-end
   overhead and non-scripted behaviour are unmeasured.
4. **AGT version drift.** The YAML format targeted here was removed from AGT
   `main` on 2026-07-29 (ACS v5). It is still present at `v5.0.0`. A target
   version needs to be chosen (open question 2).
5. **Comparison with AGT's own stateful pieces** (`MCPSlidingRateLimiter`,
   `BudgetTracker`, Context Accumulation Governance on `main`) has not been
   written.
6. **No commits were made on 2026-10-06.** All of today's changes,
   including both fix rounds, are uncommitted in the working tree on
   `progress-report`. Commits were not requested by the user. A suggested
   split is in `WORKLOG.md` (fix round 1, M5, plus one commit for round 2).
   The report's Code line says the work is uncommitted (R2-M2). If the
   user commits and pushes, replace that line with the commit hash.
7. **REVIEW.md status.** Round 1: B1 and M1-M4 are fixed. M5 (commits) is
   declined until the user asks. Round 2: R2-M1 is fixed (the idle sweep
   no longer reads other sessions' timestamps). R2-M2 is handled by
   rewording the report. R2-m1 to R2-m4 are fixed. Still open: MINOR 4, 10,
   part of 11 and part of 14 (see `WORKLOG.md`).

## Known weaknesses

- **8 of 10 evasion scenarios beat the monitor.** These are: staying just
  under a threshold, hopping between windows, splitting across sessions
  (state is per session), sending just outside the ordering window, pulsing
  under a rate limit, drifting slower than the scope window, and staying
  under the daily budget. All are inherent to fixed-threshold, fixed-window,
  per-session rules.
- **The memory backstop can be flushed.** About 1,000 zero-row calls push
  damaging events out of the per-window cap (`backstop-flush`). Making the
  cap configurable (this checkpoint) does not fix it.
- **Detection is never before the tripping call.** The monitor sees calls
  the per-call engine already allowed, so the call that crosses a threshold
  runs. For ordering rules that call *is* the harm (the external send).
- **Rule shadowing.** Under `policies/example.yaml`, `distinct-table-sprawl`
  never fires first, because `resource-scope-drift` always pre-empts it.
- **Thresholds trade off false positives.** Lowering `bulk-customer-read` to
  4,000 catches every row-exfiltration scenario in the suite but flags
  `high-volume-analytics` (1/9 false positives).
- **AGT's JSON schema rejects combined files** (`additionalProperties:
  false`). The pydantic loader accepts them.
- **Overhead is about 3.0-3.6 µs/event** across the 2026-10-06 runs and
  session sizes. These are uncontrolled, noisy measurements; see `WORKLOG.md`.
- **Timestamps must be trusted** unless a `clock` is passed. Without one, a
  single far-future timestamp still empties that session's windows. Use one
  time base per monitor. Since R2-M1 it no longer affects other sessions
  unless `shared_time_base=True` is set.
- **No cross-session expiry by default.** Without a `clock` or
  `shared_time_base=True`, sessions that never return are kept. No shipped
  entry point (demo, `bench/`) sets either.
- **The work is uncommitted.** `HEAD` and `origin/progress-report` are
  `ec4b486`. The report says so in its Code line. Committing and pushing
  before submission is the user's decision (REVIEW.md M5, R2-M2).
- **ESCALATE does not pause anything by itself.** The demo auto-approves
  every escalation and says so.
- **Editable install on macOS.** The `.venv` `.pth` file is marked `hidden`,
  so a bare `import seqmon` fails outside pytest and the scripts. See the
  README note.
