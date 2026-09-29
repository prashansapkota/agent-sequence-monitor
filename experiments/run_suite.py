"""Replay every scenario through the example policy and score it.

Writes ``results/suite_results.jsonl`` (one scored scenario per line) and
prints a summary table. Metric definitions live in ``bench/score.py``.

Run:  python experiments/run_suite.py
"""

from __future__ import annotations

import json

from _common import POLICY_PATH, RESULTS, SEED, ensure_dirs, policy, print_table

from bench.score import ScenarioResult, score
from bench.suites import all_scenarios


def run(seed: int = SEED) -> list[ScenarioResult]:
    pol = policy()
    return [score(s, pol) for s in all_scenarios(seed)]


def num(x: float) -> str:
    """Compact number: thousands separators, no scientific notation."""
    return f"{x:,.0f}" if abs(x) >= 100 else f"{x:g}"


def _yn(value: bool | None) -> str | None:
    return None if value is None else ("yes" if value else "NO")


def main() -> int:
    results = run()
    ensure_dirs()
    out = RESULTS / "suite_results.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r.to_json(), default=str) + "\n")

    print(f"\n  Scenario suite vs {POLICY_PATH.name} (seed {SEED})\n")
    rows = []
    for r in results:
        rows.append([
            r.name, r.category, r.n_events,
            "yes" if r.detected else ("no" if not r.is_attack else "MISS"),
            r.rule, r.events_elapsed,
            f"{num(r.damage_before_detection)} / {num(r.total_damage)} {r.damage_unit}"
            if r.is_attack else None,
            _yn(r.before_harm), _yn(r.at_or_before_harm),
            r.calls_prevented if r.is_attack else None,
            r.prevented_if_denied if r.is_attack else None,
            "ok" if r.hypothesis_held else "DIFFERS",
        ])
    print_table(
        ["scenario", "category", "calls", "detected", "first rule", "elapsed",
         "damage before detection / total", "<harm", "<=harm",
         "prevented", "if paused", "hypothesis"],
        rows, align="llrllrlllrrl",
    )

    attacks = [r for r in results if r.category == "adversarial"]
    evasion = [r for r in results if r.category == "evasion"]
    benign = [r for r in results if r.category == "benign"]
    shadowed = [r.name for r in attacks if not r.targeted_rule_fired]
    first_other = [r.name for r in attacks if r.detected and r.rule_constraint not in r.targets]
    print()
    print(f"  adversarial detected   {sum(r.detected for r in attacks)}/{len(attacks)}")
    print(f"  evasion detected       {sum(r.detected for r in evasion)}/{len(evasion)}"
          f"   missed: {', '.join(r.name for r in evasion if not r.detected) or '-'}")
    print(f"  benign false positives {sum(r.detected for r in benign)}/{len(benign)}")
    print(f"  detected first by a rule other than the target: {', '.join(first_other) or '-'}")
    print(f"  targeted rule never fired:                      {', '.join(shadowed) or '-'}")
    differs = [r.name for r in results if r.hypothesis_held is False]
    print(f"  outcome differs from pre-run hypothesis:        {', '.join(differs) or '-'}")
    print()
    print("  prevented = later calls in a session stopped by `break` (pauses approved);")
    print("  if paused = later calls in any alerted session, if every pause were denied.")
    print(f"\n  wrote {out.relative_to(RESULTS.parent)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
