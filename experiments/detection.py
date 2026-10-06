"""Detection rate per constraint type, evasion rate, and false-positive rate.

Groups adversarial scenarios by their primary target (``targets[0]``),
keeps evasion scenarios as their own group, and reports the false-positive
rate on the benign suite. Two detection rates are reported side by side,
because they differ and the difference matters:

- **any rule** -- the session raised some alert.
- **targeted rule first** -- the first alert came from a rule of the type
  the scenario targets.

A leave-one-rule-out ablation follows: each rule is removed in turn and the
suite re-scored, showing which rules are load-bearing and which are shadowed
by another rule under this policy.

Writes results/detection.csv, results/ablation.csv and
results/figures/detection.png.

Run:  python experiments/detection.py
"""

from __future__ import annotations

import dataclasses

from _common import BLUE, INK_2, ORANGE, SEED, policy, print_table, pyplot, save, write_csv

from bench.scenario import CONSTRAINT_TYPES
from bench.score import ScenarioResult, score
from bench.suites import all_scenarios

GROUPS = list(CONSTRAINT_TYPES) + ["evasion", "benign"]


def group_of(r: ScenarioResult) -> str:
    return r.category if r.category != "adversarial" else r.targets[0]


def summarise(results: list[ScenarioResult]) -> list[dict]:
    rows = []
    for g in GROUPS:
        members = [r for r in results if group_of(r) == g]
        n = len(members)
        any_rule = sum(r.detected for r in members)
        targeted = sum(r.detected and r.rule_constraint in r.targets for r in members)
        rows.append({
            "group": g,
            "n": n,
            "detected_any": any_rule,
            "detected_targeted_first": targeted if g != "benign" else None,
            "rate_any": any_rule / n if n else 0.0,
            "rate_targeted_first": (targeted / n if n else 0.0) if g != "benign" else None,
            "scenarios": ";".join(r.name for r in members),
            "missed": ";".join(r.name for r in members if not r.detected) if g != "benign" else "",
        })
    return rows


def ablation(seed: int) -> list[dict]:
    full = policy()
    scenarios = all_scenarios(seed)
    variants = [("(none removed)", full)] + [
        (rule.name, dataclasses.replace(full, rules=[r for r in full.rules if r is not rule]))
        for rule in full.rules
    ]
    baseline = {s.name: score(s, full).detected for s in scenarios}
    rows = []
    for removed, pol in variants:
        res = [score(s, pol) for s in scenarios]
        attacks = [r for r in res if r.is_attack]
        lost = [r.name for r in attacks if baseline[r.name] and not r.detected]
        rows.append({
            "removed_rule": removed,
            "attacks_detected": sum(r.detected for r in attacks),
            "attacks_total": len(attacks),
            "false_positives": sum(r.detected for r in res if not r.is_attack),
            "newly_missed": ";".join(lost),
        })
    return rows


def plot(rows: list[dict]) -> None:
    plt = pyplot()
    labels = {"cumulative-sum": "Cumulative sum", "cumulative-distinct": "Cumulative distinct",
              "rate": "Rate", "ordering": "Ordering", "scope": "Scope",
              "evasion": "Evasion variants", "benign": "Benign (false positives)"}
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    ys = list(range(len(rows)))[::-1]
    h = 0.36
    for y, row in zip(ys, rows, strict=True):
        n = row["n"]
        ax.barh(y + h / 2, 100 * row["rate_any"], height=h - 0.04, color=BLUE,
                label="Any rule" if y == ys[0] else None)
        ax.text(100 * row["rate_any"] + 1.5, y + h / 2, f"{row['detected_any']}/{n}",
                va="center", fontsize=8.5, color=INK_2)
        if row["rate_targeted_first"] is not None:
            ax.barh(y - h / 2, 100 * row["rate_targeted_first"], height=h - 0.04,
                    color=ORANGE, label="Targeted rule first" if y == ys[0] else None)
            ax.text(100 * row["rate_targeted_first"] + 1.5, y - h / 2,
                    f"{row['detected_targeted_first']}/{n}",
                    va="center", fontsize=8.5, color=INK_2)
    ax.set_yticks(ys, [labels[r["group"]] for r in rows])
    ax.set_xlim(0, 112)
    ax.set_xticks(range(0, 101, 20))
    ax.set_xlabel("Scenarios with an alert (%)")
    ax.grid(axis="y", visible=False)
    ax.set_title("Detection by constraint type, example policy")
    ax.legend(loc="lower right", bbox_to_anchor=(1.0, 0.02))
    fig.text(0.01, -0.07,
             "Distinct-table attacks are caught, but by the scope rule: with 2 tables allowed "
             "and max_outside 3,\nscope always fires before 9 distinct tables are reached.",
             fontsize=8, color=INK_2)
    save(fig, "detection.png")
    plt.close(fig)


def main() -> int:
    results = [score(s, policy()) for s in all_scenarios(SEED)]
    rows = summarise(results)
    write_csv("detection.csv", rows)

    print("\n  Detection by group (example policy)\n")
    print_table(
        ["group", "n", "any rule", "targeted rule first", "missed"],
        [[r["group"], r["n"], f"{r['detected_any']}/{r['n']} ({100 * r['rate_any']:.0f}%)",
          None if r["rate_targeted_first"] is None
          else f"{r['detected_targeted_first']}/{r['n']} ({100 * r['rate_targeted_first']:.0f}%)",
          r["missed"].replace(";", ", ") or None] for r in rows],
        align="lrrrl",
    )
    fp = next(r for r in rows if r["group"] == "benign")
    print(f"\n  False-positive rate on benign: {fp['detected_any']}/{fp['n']}")

    abl = ablation(SEED)
    write_csv("ablation.csv", abl)
    print("\n  Leave-one-rule-out ablation (all attacks incl. evasion)\n")
    print_table(
        ["removed rule", "attacks detected", "FPs", "newly missed"],
        [[r["removed_rule"], f"{r['attacks_detected']}/{r['attacks_total']}",
          r["false_positives"], r["newly_missed"].replace(";", ", ") or None] for r in abl],
        align="lrrl",
    )
    plot(rows)
    print("\n  wrote results/detection.csv, results/ablation.csv, "
          "results/figures/detection.png\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
