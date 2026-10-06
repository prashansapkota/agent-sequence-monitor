# Open questions

Things this project could not verify from the AGT source, or decisions that
still need an answer. Each item says what was checked. Last updated 2026-10-06.

## AGT integration

1. **Pinned commit `5ed63c6e` does not exist upstream.** `git fetch origin
   5ed63c6e` in a fresh clone of microsoft/agent-governance-toolkit fails with
   `fatal: couldn't find remote ref 5ed63c6e`. Work since 2026-10-06 uses tag
   `v3.5.0` (`889c70ce`), the `agent_os_kernel` version the README names.
   Was `5ed63c6e` from a fork or a rewritten history? The README claims about
   `ExecutionContext.history` were re-checked at `v3.5.0` and hold.
2. **Which AGT version should the project target?** The YAML `PolicyDocument`
   format was removed from AGT `main` on 2026-07-29 (`4b8b2114`, "replace the
   v4 policy language with ACS v5"). It is still present at `v5.0.0`. Should
   the project stay on the v3.5/v4 YAML format, or move to ACS v5
   (`policy-engine/`)? ACS v5 has not been read.
3. **AGT's JSON schema rejects `sequence_rules`.** `policy_schema.json` has
   top-level `"additionalProperties": false`, so `jsonschema` reports
   `Additional properties are not allowed ('sequence_defaults',
   'sequence_rules' were unexpected)` (`scripts/check_agt_compat.py`). The
   pydantic loader accepts the same files and drops those keys. Options:
   upstream a schema change, keep sequence rules in a sibling file, or accept
   the `agentos validate` error. Undecided.
4. **Where do session id, timestamp, resource and magnitude come from?**
   `ToolCallRequest` has only `tool_name`, `arguments`, `call_id`,
   `agent_id`, `metadata`. A real adapter would need a convention (for
   example `metadata["session_id"]`). AGT defines none. The session id does
   exist on `integrations/base.py:ExecutionContext.session_id`. How it reaches
   an interceptor has not been traced.
5. **Result-dependent quantities (rows returned) are only known after
   execution.** A pre-call `ToolCallInterceptor` cannot see them. Is
   `BaseIntegration.post_execute(ctx, output_data)` the right place, and does
   every framework integration call it? Not yet checked.
6. **Overlap with AGT's own stateful pieces.** `MCPSlidingRateLimiter`,
   `policies/rate_limiting.py`, `policies/budget.py` (v3.5.0) and Context
   Accumulation Governance (`main`) each cover part of the space. The report
   needs a fair comparison. Not done yet.

## Design

7. **Ordering "B must be preceded by A"** (required predecessor) is in the
   proposal but not implemented. Only "B must never follow A within the
   window" exists.
8. **The memory backstop is an evasion surface.** Padding a window with
   1,000+ zero-row calls pushes damaging events past the
   `max_events_per_window` cap (`backstop-flush` evasion scenario). The cap is
   now configurable, but configuring it does not fix this. Options: never shed
   events that carry magnitude, or alert when shedding starts. Undecided.
9. **Cross-session correlation.** State is keyed by `session_id`, so splitting
   an attack across sessions evades every rule (`session-split`). Should there
   be an agent-level or tenant-level key?
10. **ESCALATE semantics.** `FakeAGTInterceptor` asks an `approver` callback
    and by default approves (recorded as `auto=True`, and the demo says so).
    Since 2026-10-06 an approval re-arms the rule, so the next breach asks
    again. Should the call that trips ESCALATE itself be held instead?
    Holding it would need a pre-call integration (see 5).
11. **Timestamp trust.** Late timestamps are now clamped to the session's
    latest one. Far-future timestamps are bounded only if a `clock` is
    passed. Should the real adapter always stamp events itself (making the
    clock unnecessary), or take the framework's timestamps and pass a clock?
    Depends on question 4.
12. **Terminated-session ids.** `FakeAGTInterceptor.terminated` keeps one id
    per terminated session for ever, so it can keep refusing that session's
    calls. A real deployment would need a retention rule (or to push the
    termination into AGT's own session handling). Undecided.
