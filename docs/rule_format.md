# Sequence rule format

seqmon rules are YAML. They are designed to sit in the **same policy file**
as the Agent Governance Toolkit (AGT) per-call rules, under a separate
top-level key, so one file describes both what a single call may do and what
a session may do.

The parser is `src/seqmon/spec.py` (`load_policy`, `parse_policy`). Every
example on this page is a file under `policies/examples/` and is parsed by
`tests/test_smoke.py`.

## Where the rules go

```yaml
version: "1.0"                 # AGT top-level fields, shared
name: reporting-agent-policy
description: ...

rules:                         # AGT per-call rules. seqmon never reads these.
  - name: cap-single-query
    condition: {field: records, operator: gt, value: 1000}
    action: deny
    priority: 90

defaults:                      # AGT per-call default. seqmon never reads this.
  action: allow

sequence_defaults:             # seqmon settings (optional)
  max_events_per_window: 10000

sequence_rules:                # seqmon sequence rules
  - name: bulk-customer-read
    type: cumulative
    ...
```

Two layouts are accepted:

| Layout | How it is recognised | Rules read from |
|---|---|---|
| AGT file (`layout == "agt"`) | has a `sequence_rules` key | `sequence_rules` |
| Standalone (`layout == "standalone"`) | no `sequence_rules` key | `rules` |

The standalone layout is the original format (`policies/example.yaml`, used by
`bench/` and most tests) and is kept working. If a standalone `rules` entry has
an AGT `condition` and no `type`, the parser stops with a hint to move sequence
rules under `sequence_rules:`.

### `sequence_defaults`

| Field | Type | Meaning |
|---|---|---|
| `max_events_per_window` | positive int | Hard cap on events kept in each rule's window (the memory backstop). The `SequenceMonitor(max_events_per_window=...)` argument overrides it. If neither is set, the cap is 10,000. |

Unknown keys are rejected.

## Fields shared by every rule

| Field | Required | Type | Meaning |
|---|---|---|---|
| `name` | no (default `rule_<index>`) | string | Unique within the file. Used as the state key and in alerts. |
| `type` | yes | `cumulative` / `rate` / `ordering` / `scope` | Constraint type. |
| `window` | yes | duration (`90s`, `30m`, `2h`, `1d`, or a bare number of seconds) | Sliding-window span. |
| `response` | no (default `warn`) | see below | What happens on violation. |
| `message` | no | string | Reported with the violation. |
| `actions` | no | string or list of strings | Tool names the rule tracks. Empty means every tool. Ignored by `ordering`. |

Each rule type accepts only its own fields (listed below) plus these shared
ones. Any other key is an error, so a typo such as `attrbute:` or
`max_outsde:` cannot leave a silently different rule. The singular `action:`
is rejected on purpose: in AGT's per-call `rules` it is the verdict
(`allow`/`deny`/`audit`/`block`), so `action: deny` in a sequence rule would
otherwise have become a tool filter matching nothing. The error says to use
`response:` (outcome) or `actions:` (tool filter).

### Responses (severities)

| Severity | Spelling in YAML | Effect |
|---|---|---|
| LOG | `log` or `warn` | Record and continue. |
| ESCALATE | `escalate` or `pause` | `Decision.approval_required`; the `EscalationHook` is called. The monitor cannot hold the agent itself: the embedding runtime must pause the session until a human answers, then call `SequenceMonitor.approve(session_id, violations)`. Approval re-arms the rule, so the next breach escalates again. `FakeAGTInterceptor` does this with its `approver` callback (default: approve everything, recorded as `auto=True`). |
| TERMINATE | `terminate` or `break` | `Decision.terminate`; the `TerminationHook` is called. Session ends. |

Both spellings give the same `seqmon.Response` member (`Response.LOG is
Response.WARN`). The `warn`/`pause`/`break` names come from AGT (see the last
section).

## The four constraint types

### 1. Cumulative aggregate over a rolling window

Fires when an aggregate over the events in the last `window` exceeds
`threshold`.

| Field | Required | Meaning |
|---|---|---|
| `threshold` | yes | number |
| `aggregate` | no (default `sum`) | `sum` of a numeric quantity, `count` of calls, `distinct` resources |
| `attribute` | no, `sum` only | sum `event.attributes[attribute]` (e.g. `records`, `cost_usd`). Without it, `sum` uses `event.magnitude`. |

From `policies/examples/exfiltration.yaml`:

```yaml
sequence_rules:
  - name: bulk-customer-read
    type: cumulative
    actions: [customer_db.query]
    aggregate: sum
    attribute: records        # sum event.attributes["records"]
    threshold: 5000
    window: 1h
    response: terminate
```

Budget drain (`policies/examples/budget.yaml`) is the same type with
`attribute: cost_usd`.

### 2. Rate limit (per tool or total)

Fires on call number `max_calls + 1` inside one window. With `actions` it
limits those tools; without `actions` it limits all calls together.

From `policies/examples/rate.yaml`:

```yaml
sequence_rules:
  - name: message-flood          # per tool
    type: rate
    actions: [messaging.send]
    max_calls: 30
    window: 5m
    response: terminate

  - name: total-call-rate        # every tool
    type: rate
    max_calls: 600
    window: 10m
    response: escalate
```

### 3. Ordering

Fires when `after` happens while a `before` event is still inside the window:
"B must never follow A within the window". From
`policies/examples/exfiltration.yaml`:

```yaml
sequence_rules:
  - name: read-then-exfiltrate
    type: ordering
    before: customer_db.query
    after: messaging.send_external
    window: 10m
    response: terminate
```

Only the "must never follow" form is implemented. The proposal also lists
"B must be preceded by A" (a required predecessor). That form is **not
implemented yet** (see `HANDOFF.md`).

### 4. Scope drift (declared-scope escape)

Fires when more than `max_outside` calls inside one window touch a resource
that is not on the declared `allowed` list. Calls with no `resource` are
ignored. From `policies/examples/agt_combined.yaml`:

```yaml
sequence_rules:
  - name: resource-scope-drift
    type: scope
    actions: [customer_db.query, files.read, files.write]
    allowed: [customer_db.orders, customer_db.order_items, files/reports]
    max_outside: 3
    window: 1h
    response: escalate
```

The proposal planned scope drift last. The evaluator for it was **built ahead
of plan**, in the first commit (2026-09-15). Its definition is deliberately
mechanical (exact-match allowlist, a count per window). It does not try to judge
whether a resource is "related to the task". Since 2026-09-28 the count is per
window, not per lifetime. As a result, drift paced more slowly than the window
is missed (the `slow-scope-drift` evasion scenario). Always give a scope rule an
`actions` filter. Without one it also judges the agent's own infrastructure
calls (LLM completions, internal chat) as drift.

## Time and windows

- A window is the half-open interval `(now - window, now]`. An event exactly
  `window` seconds old has left it (tested at `window - 0.001`, `window`,
  `window + 0.001` in `tests/test_review_fixes.py`).
- Timestamps come from the interceptor, not the agent, and are treated as
  monotonic per session. A late event (older than the session's latest) is
  counted at the session's latest timestamp. It still counts; it just cannot
  sit behind newer events or give an ordering rule a negative gap. Clamped
  events are counted in `SessionState.clamped_events`.
- There is no forward bound unless a clock is supplied:
  `SequenceMonitor(policy, clock=time.time, max_clock_skew=60)` clamps any
  timestamp more than 60 s ahead of the clock. Without one, a single
  far-future timestamp would empty every window, so the timestamps must be
  trusted. Use one time base per monitor.
- A session whose next event comes at least the longest rule window after
  its previous one starts fresh (all its windows are empty by then),
  including which rules have already alerted. This is per session and never
  changes a result.
- Sessions that never come back are swept only if the monitor has a shared
  "now". With a `clock`, the sweep uses `clock() - max_clock_skew`, which is
  result-neutral as long as no event is stamped more than `max_clock_skew`
  behind the clock. With `shared_time_base=True` and no clock, it uses the
  arriving event's timestamp. That is only correct when every session's
  timestamps share one trusted time base: one bad timestamp then affects
  all sessions and stops idle expiry. By default (neither), there is no
  sweep, so held sessions grow with abandoned sessions. `idle_ttl=0`
  disables expiry. An `idle_ttl` shorter than the longest window, or a
  negative `max_clock_skew`, raises `ValueError`.
- Event numbers (`timestamp`, `magnitude`, `attributes`) must be finite;
  `ToolCallEvent` raises `ValueError` otherwise. Negative values are allowed
  (for example a refund) and do reduce a `sum`.

## Validation errors

Every problem raises `seqmon.SpecError`. The message starts with the path of the
offending field. Indices are 0-based, as in AGT's JSON-schema error paths:

```
sequence_rules[2].window: rule 'c': bad duration 'soon'; expected e.g. '30s', '5m', '2h', '1d'
sequence_rules[0].allowed[1]: rule 'r': field 'allowed' must contain only strings, got None
sequence_rules[1].name: duplicate rule name 'bulk-read'
sequence_defaults.max_events_per_window: must be a positive integer, got 0
<root>: policy document must be a YAML mapping
```

Numbers must be YAML numbers, finite and `>= 0`: `max_calls: true`,
`max_calls: "5"`, `threshold: [1]`, `threshold: .nan`, `threshold: -5` and
`window: .inf` are errors, not silently coerced. `max_calls` and `max_outside`
must be whole numbers (`2.9` is an error). An `ordering` rule whose `before`
and `after` are the same tool is an error, because it could never fire. String lists reject non-strings. A missing `.yaml` path
is reported as `policy file not found`. A YAML syntax error is reported as a
`SpecError`.

## Which AGT conventions this mirrors

All of these were read in the AGT source at tag `v3.5.0` (commit `889c70ce`,
2026-05-07), checked out at `../agt-src`:

- **Top-level `version` / `name` / `description` and a `rules` list.** From
  `PolicyDocument` in
  `agent-governance-python/agent-os/src/agent_os/policies/schema.py`.
- **Per-call rule shape** (`name`, `condition {field, operator, value}`,
  `action` in `allow|deny|audit|block`, `priority`, `message`) and the
  `defaults.action` fallback. From the same file. `seqmon.adapters.FakePerCallEngine`
  mirrors the evaluation order in `.../policies/evaluator.py`
  (`PolicyEvaluator._evaluate_flat`, `_match_condition`).
- **A separate top-level section for non-per-call policy.** AGT's JSON schema
  (`.../policies/policy_schema.json`) already has a sibling section,
  `a2a_conversation_policy`, next to `rules`. `sequence_rules` follows that
  pattern.
- **Response verbs `warn` / `pause` / `break`.** These are the action values of
  `a2a_conversation_policy` (`on_escalation_detected` etc.) in
  `policy_schema.json`, and `BREAK = "break"` in
  `.../integrations/conversation_guardian.py`.
- **`sequence_defaults`** mirrors AGT's `defaults` block.

### Does AGT accept the combined file?

`scripts/check_agt_compat.py` checks this against the real AGT code. It needs a
checkout plus `pydantic` and `jsonschema`. Result on 2026-10-06:

- `PolicyDocument.from_yaml` (pydantic, no `extra="forbid"`) loads all four
  example files. It **silently ignores** `sequence_rules` and
  `sequence_defaults`.
- AGT's JSON schema sets `"additionalProperties": false` at the top level. So it
  **rejects** the files: `Additional properties are not allowed
  ('sequence_defaults', 'sequence_rules' were unexpected)`. Any tool that
  validates against that schema (for example `agentos validate` when
  `jsonschema` is installed, `cli/cmd_validate.py`) will report an error. See
  `OPEN_QUESTIONS.md`.
- `FakePerCallEngine` and AGT's `PolicyEvaluator` gave identical verdicts and
  matched rules on all 36 calls compared (the demo trace plus 4 probe calls).
