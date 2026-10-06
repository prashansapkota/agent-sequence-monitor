# Integrating with AGT: what is verified and what is not

seqmon does **not** run inside AGT yet. The benchmark scripts simulate the
per-call engine with hard-coded `allow` verdicts. The demo uses
`seqmon.adapters.FakeAGTInterceptor`, which evaluates the AGT `rules` of a
policy file with a re-implementation of AGT's flat evaluator. This page
records what the AGT source actually shows about where a real hook would go.
Anything not shown in that source is listed in `OPEN_QUESTIONS.md`.

Source: `https://github.com/microsoft/agent-governance-toolkit`, tag `v3.5.0`
(commit `889c70ce5c211da691d1d7260e39e29167156719`, 2026-05-07), at
`../agt-src`. All paths below are under
`agent-governance-python/agent-os/src/agent_os/`. The README previously pinned
commit `5ed63c6e`. That commit is **not in the public repository**
(`git fetch origin 5ed63c6e` → `couldn't find remote ref`), so `v3.5.0` (the
`agent_os_kernel` version the README names) was used instead.

## Verified facts

1. **The per-call interception interface is `ToolCallInterceptor`**
   (`integrations/base.py`). It is a `Protocol` with one method,
   `intercept(request: ToolCallRequest) -> ToolCallResult`.
   - `ToolCallRequest` fields: `tool_name`, `arguments: dict`, `call_id`,
     `agent_id`, `metadata: dict`.
   - `ToolCallResult` fields: `allowed`, `reason`, `modified_arguments`,
     `audit_entry`.
   - There is **no session id, timestamp, resource or result size** on
     `ToolCallRequest`.
2. **Interceptors are chained by `CompositeInterceptor`** (same file). It calls
   each interceptor in order and returns the first result with
   `allowed == False`. So an interceptor placed **last** in the chain only sees
   calls every earlier interceptor allowed. This matches seqmon's assumption
   that it observes only permitted calls. `CompositeInterceptor` reads only
   `.allowed` from each result.
3. **`PolicyInterceptor`** (same file) is the default per-call check
   (`allowed_tools`, blocked patterns, `max_tool_calls`, human approval). It
   holds an optional `ExecutionContext` whose `call_count` it compares against
   `max_tool_calls`. That is AGT's only cross-call state on this path, and it is
   a bare counter with no window.
4. **The YAML policy engine is `PolicyEvaluator.evaluate(context: dict)`**
   (`policies/evaluator.py`). Rules are sorted by `priority` (descending). The
   first rule whose `condition` matches decides. `allow` and `audit` are
   permitted. No match falls back to `defaults.action`. Errors fail closed. The
   `context` dict is supplied by the caller. The evaluator keeps no state
   between calls.
5. **The stateless kernel never consults history** (`stateless.py`). Its module
   docstring says "The kernel never looks up prior requests; the caller is
   responsible for threading context". `ExecutionContext.history` is appended
   to (`action`, `timestamp`, `success`) but not read by `_check_policies`.
6. **AGT has some stateful limiters of its own.** These overlap with parts of
   seqmon's rate and budget rules and need to be compared in the report:
   `mcp_sliding_rate_limiter.py` (`MCPSlidingRateLimiter`, a per-agent
   sliding-window call limit for MCP tools), `policies/rate_limiting.py`
   (token bucket) and `policies/budget.py` (`BudgetPolicy` / `BudgetTracker`:
   `max_tokens`, `max_tool_calls`, `max_cost_usd`, `max_duration_seconds`).
   None of them is expressed in the YAML `PolicyDocument`, and none covers
   ordering or scope.

## What a real hook would look like (not implemented)

Based only on facts 1 and 2: implement an object with
`intercept(request) -> result-with-.allowed`. Add it as the **last** member of
the `CompositeInterceptor`. Inside it:

1. if the session was already terminated by seqmon, return `allowed=False`;
2. translate the `ToolCallRequest` to a `ToolCallEvent`;
3. call `SequenceMonitor.on_action(event)` and return `allowed=True`. The call
   that trips a rule has already passed every per-call check. seqmon acts on
   the session, as `FakeAGTInterceptor.submit` does today.

Step 2 is the problem. The fields seqmon needs are not on `ToolCallRequest`
(fact 1), so they would have to come from `metadata` or `arguments` under a
naming convention AGT does not define. The record count of a query is only
known **after** the tool runs, so a pre-call interceptor cannot supply it.
AGT's `BaseIntegration` has a `post_execute` hook (`integrations/base.py`),
but this project has not checked what it receives. Both points are open
questions.

## Upstream has moved on

On AGT `main` (checked 2026-10-06, `95d92661`), commit `4b8b2114`
(2026-07-29, "replace the v4 policy language with ACS v5 across the Python
runtime") removed `agent_os/policies/schema.py` and `evaluator.py`. The YAML
format targeted here still exists at tags `v4.1.0` and `v5.0.0`. Main also
gained "Context Accumulation Governance" (`policies/context_accumulation.py`,
added 2026-06-17 in `0d674bed`). It accumulates data-sensitivity labels across
actions and gates the next action. That is related prior work on stateful
governance. It works on labels, not counts, rates, ordering or scope. See
`OPEN_QUESTIONS.md`.
