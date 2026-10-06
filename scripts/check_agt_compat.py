"""Check the combined policy files against the REAL AGT source (optional).

Not part of the test suite: it needs a local checkout of
https://github.com/microsoft/agent-governance-toolkit and an environment
with ``pydantic>=2.4,<3`` (AGT's own pin) and ``jsonschema``. It loads
AGT's ``agent_os/policies/schema.py`` and ``evaluator.py`` straight from
the checkout (without importing the rest of ``agent_os``) and checks:

1. ``PolicyDocument.from_yaml`` loads every example file that has AGT
   ``rules``, and silently drops the ``sequence_*`` keys.
2. What AGT's JSON schema (``policy_schema.json``, used by
   ``agentos validate``) says about those files.
3. ``FakePerCallEngine`` returns the same allow/deny verdict and matched
   rule as AGT's ``PolicyEvaluator`` for every call of the demo trace plus
   a few probe calls that should be denied.

Usage:
    python scripts/check_agt_compat.py [--agt-src ../agt-src]
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import yaml

from seqmon import ToolCallEvent
from seqmon.adapters import FakePerCallEngine
from seqmon.adapters.fake_agt import event_context

POLICIES = sorted((ROOT / "policies" / "examples").glob("*.yaml"))


def load_agt(agt_src: Path) -> types.ModuleType:
    """Import AGT's policies/schema.py + evaluator.py as a bare package."""
    pol_dir = agt_src / "agent-governance-python/agent-os/src/agent_os/policies"
    if not pol_dir.is_dir():
        raise SystemExit(f"AGT policies package not found under {agt_src}")
    pkg = types.ModuleType("agt_policies")
    pkg.__path__ = [str(pol_dir)]
    sys.modules["agt_policies"] = pkg
    importlib.import_module("agt_policies.schema")
    importlib.import_module("agt_policies.evaluator")
    return pkg


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agt-src", type=Path, default=ROOT.parent / "agt-src")
    args = parser.parse_args()
    agt_src = args.agt_src.resolve()
    load_agt(agt_src)
    schema_mod = sys.modules["agt_policies.schema"]
    eval_mod = sys.modules["agt_policies.evaluator"]
    import jsonschema

    json_schema = json.loads(
        (agt_src / "agent-governance-python/agent-os/src/agent_os/policies/"
         "policy_schema.json").read_text()
    )
    validator = jsonschema.Draft7Validator(json_schema)
    failures = 0

    print("1-2. AGT loaders on policies/examples/*.yaml")
    for path in POLICIES:
        raw = yaml.safe_load(path.read_text())
        doc = schema_mod.PolicyDocument.from_yaml(path)
        dumped = doc.model_dump()
        dropped = sorted(k for k in raw if k not in dumped)
        errors = [e.message for e in validator.iter_errors(raw)]
        print(f"  {path.name}: PolicyDocument OK, {len(doc.rules)} per-call rule(s); "
              f"keys ignored by pydantic: {dropped}; JSON-schema errors: {len(errors)}")
        for msg in errors:
            print(f"      schema: {msg}")

    print("3. FakePerCallEngine vs AGT PolicyEvaluator (agt_combined.yaml)")
    combined = ROOT / "policies/examples/agt_combined.yaml"
    fake = FakePerCallEngine.from_mapping(yaml.safe_load(combined.read_text()))
    real = eval_mod.PolicyEvaluator(policies=[schema_mod.PolicyDocument.from_yaml(combined)])

    from demo_trace import build_trace

    probes = [
        dataclasses.replace(build_trace()[0], action="run_shell", attributes={}),
        dataclasses.replace(build_trace()[0], attributes={"records": 1500.0}),
        dataclasses.replace(build_trace()[0], attributes={"records": 1000.0}),
        ToolCallEvent("execute_code", "a", "s", 0.0),
    ]
    events = build_trace() + probes
    for e in events:
        f = fake.check(e)
        r = real.evaluate(event_context(e))
        same = (f.allowed, f.action, f.matched_rule) == (r.allowed, r.action, r.matched_rule)
        failures += not same
        if not same or e in probes:
            print(f"  {e.action:<24} records={e.value('records'):>6.0f}  "
                  f"fake=({f.allowed}, {f.action}, {f.matched_rule})  "
                  f"agt=({r.allowed}, {r.action}, {r.matched_rule})  {'OK' if same else 'DIFF'}")
    print(f"  {len(events)} calls compared, {failures} disagreement(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
