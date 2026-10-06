# Test report: seqmon, Progress Report 1 checkpoint (2026-10-06)

Test-engineering pass on branch `progress-report` (HEAD `ec4b486` plus the
uncommitted working tree). No file under `src/` was modified. Bugs that the
new tests reveal are marked `xfail(strict=True)` and logged in `BUGS.md`.

Evidence (all files in the repo):

| File | Contents |
|---|---|
| `docs/evidence/test_run.txt` | `pytest --cov=seqmon --cov-report=term-missing` session plus the `-rx` xfail reasons |
| `docs/evidence/coverage.txt` | Coverage table |
| `docs/evidence/demo_output.txt` | `scripts/demo_trace.py` |
| `docs/evidence/experiments_output.txt` | `experiments/run_all.py` ("All 7 steps passed") |
| `docs/evidence/figures/*.png` | Copies of `results/figures/` |
| `bench/results/overhead.csv`, `overhead.png`, `machine.txt` | New overhead benchmark (`bench/overhead.py`) |

## Counts

| | Tests | Passed | xfailed (known bugs) | Failed |
|---|---|---|---|---|
| Before this pass (6 files) | 208 | 208 | 0 | 0 |
| Added by this pass (7 files) | 218 | 204 | 14 | 0 |
| **Full suite** | **426** | **412** | **14** | **0** |

The 14 xfails cover 9 bug IDs (BUG-001 to BUG-009); some IDs have several
parametrised cases. Run time is about 11 s without coverage and 33 s with it.

**Coverage of `seqmon`: 96%** (823 statements, 33 missed). By file: `monitor.py`,
`response.py`, `adapters/base.py` and `__init__` at 100%; `state.py` 99%;
`evaluator.py`, `events.py` and `spec.py` 97%; `adapters/fake_agt.py` 85%. Most
of the misses in `fake_agt.py` are the per-call condition operators
(`eq`/`ne`/`lt`/`contains`/...) of the simulated AGT engine.

## What each new test group targets

| File | Tests | Targets |
|---|---|---|
| `tests/test_rule_parsing.py` | 109 (7 xfail) | Every `policies/examples/*.yaml` parses in the AGT layout. The legacy `example.yaml` has all four kinds. Per-call rules stay out of `sequence_rules`. Window units and response spellings are covered. Every invalid case checks that the `SpecError` message **starts with the field path** (`rules[1].max_calls:`, `sequence_rules[0]...`, `rules[0].allowed[1]:`). The invalid cases are: missing fields for each type; wrong types (string, bool, list or null numbers; float `max_calls`; non-string list items); zero, negative, NaN, inf and unparseable windows; negative numbers; unknown type; typo keys; bad `sequence_defaults`; missing file; YAML syntax error; long inline documents. |
| `tests/test_cumulative.py` | 23 (3 xfail) | Exactly at the threshold (no fire) vs one past (fires on that event), for sum, count and distinct. Attribute sums. Negative offsets. Partial and full window eviction. The **exact boundary** at 9, 9.999, 10 and 10.001 s for a 10 s window (half-open `(now-w, now]`). Equal timestamps. Session isolation (2 and 50 interleaved sessions, reset). Once-per-session alerts. Float accumulation (BUG-001/002). |
| `tests/test_rate.py` | 19 (1 xfail) | A burst at one instant fires on call `max_calls+1`. Bursts after a quiet period and across a window edge. A steady rate exactly at the limit for 1 h (no fire) vs just over it. 5,000 calls just under 30/5 min. **Per-tool vs total** limits, both supported: per-tool ignores other tools, total counts every tool, and both fire in order. The shipped `rate.yaml`. Per-session limits. The cap set below `max_calls` (BUG-006). |
| `tests/test_state_bounds.py` | 8 (1 xfail) | 100,000 events, 1/s, 60 s windows, all four kinds: every deque ≤ 60 and every distinct-resource dict ≤ 60 keys. 100,000 events at **one instant**: every deque and dict ≤ cap (500), drops counted. The ordering window is bounded only by the cap. `fired` ≤ rules and `history` = 1,000 under 500,000 violations. **Many sessions** (100,000 one-shot sessions): bounded with a `clock` and with `shared_time_base`, unbounded by default (BUG-009). |
| `tests/test_response.py` | 31 | `LOG/ESCALATE/TERMINATE` ↔ `warn/pause/break` aliasing. Unknown verbs (`block`, `deny`, `quarantine`, ...) are rejected. `Decision.severity`, `.terminate` and `.approval_required` for every combination. The escalation hook is called once with only the escalating violations. Terminate suppresses escalate. Hooks fire once per session, and again after `approve()`. Hooks are per session. `replay` stops at terminate. Hook exceptions propagate. Protocol conformance. Log level per severity. |
| `tests/test_property_incremental.py` | 4 hypothesis properties × 200 examples | The incremental evaluator **equals a naive full-history reference** (`tests/naive_reference.py`, written for these tests; it does not import `bench/naive.py`). The comparison covers rule, session, triggering event, observed value and `events_elapsed`. It runs on random policies (1-4 rules, all four kinds, random filters and windows) and random 3-session traces with equal timestamps, gaps longer than the windows, and negative magnitudes. Variants: every alert (`repeat_alerts=True`); first alert per session, including restart after an idle gap; unordered timestamps (clamp contract); and session non-interference. A quick mutation check (changing the reference window to a closed interval) made 2 of the 4 properties fail. That mutated copy was in the scratchpad and was not committed. |
| `tests/test_review_regressions.py` | 24 (2 xfail) | One or more tests per REVIEW.md BLOCKER/MAJOR, independent of `test_review_fixes.py`. **B1** the report exists. **M1** late, equal and far-future timestamps (with a clock), and the ordering gap is never negative. **M2** `footprint` does not create sessions, terminated session state is dropped, and history is capped. **M3** each breach after an approval asks again, and a refusal ends the session. **M4** AGT verdicts in `action:` and typo keys are rejected with a path. **R2-M1** a far-future stamp or a different time base in another session does not change a verdict. **R2-M2** the report states its code location. The still-open **R3-m1** and **R3-m2** are xfail (BUG-007, BUG-008). **M5** (uncommitted work) is a process item and has no code test. |

## Overhead (bench/overhead.py)

Machine (`bench/results/machine.txt`): Apple M1, 8 cores, 8 GB, macOS 26.6
(25G5028f), CPython 3.12.7. The policy is `policies/example.yaml` (6 rules). The
stream is 1 event/s and cycles through every action the policy tracks. Timing
is the best of 3 runs. Memory is measured with `tracemalloc` in a separate run.

| Session length | µs/event (whole session) | µs/event (last 1,000) | Heap held (KiB) | Events retained |
|---|---|---|---|---|
| 100 | 2.39 | 2.39 | 38.6 | 135 |
| 1,000 | 2.30 | 2.30 | 187.4 | 1,085 |
| 10,000 | 2.41 | 2.44 | 892.9 | 3,717 |
| 100,000 | 2.52 | 2.59 | 3,369.0 | 12,050 |

Per-event evaluation time stays between 2.3 and 2.6 µs from 100 to 100,000
events. It does not grow with session length, and the small variation is
within run-to-run noise (two earlier runs of the same script gave 2.21-2.45 µs).
Memory grows from 38.6 KiB to 3.4 MiB over this range. The reason is that the
policy's longest window is one day, which is 86,400 events at 1 event/s, so it
has not filled by 10,000 events. At 100,000 events the budget rule's window
holds the 10,000-event cap. Retained events (12,050) are within the documented
bound of 6 rules × 10,000. Those figures plateau once every window is full or
capped, but this benchmark does not run long enough to show the plateau. This
is a microbenchmark of the monitor alone on one machine, not end-to-end agent
overhead.

## Bugs found

See `BUGS.md` for the repro and expected vs actual of each.

- **BUG-006 (major):** a rule whose `max_calls` (or `count` threshold) is at
  or above the per-window cap can never fire, and nothing rejects or warns.
- **BUG-001 / BUG-002 (minor):** float accumulation and eviction residue
  make a sum exactly at the threshold fire. This affects the decimal-dollar
  `budget.yaml`.
- **BUG-007 (minor, REVIEW R3-m1):** with a clock, a lagging stamp lets the
  sweep drop a live session without any record.
- **BUG-009 (minor, documented):** with the default config, sessions that
  never return are kept forever.
- **BUG-003, -004, -005, -008 (low):** parser and constructor validation
  gaps: falsy names, case of the verb spellings, `actions: ""`, and
  `idle_ttl=NaN`.

## Other changes in this pass

- `pyproject.toml`: added `hypothesis>=6.100` to the `dev` extra (installed
  6.168.5 in `.venv`).
- `.gitignore`: added `!bench/results/`, because `results/` was also ignoring
  the benchmark evidence.
- `experiments/run_all.py` was re-run, which regenerated `results/` (gitignored).
  It reported adversarial 11/11, evasion 2/10 and benign false positives 0/9,
  the same headline numbers as `HANDOFF.md`.
