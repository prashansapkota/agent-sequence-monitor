"""The core trade-off: damage before detection vs false-positive rate.

Sweeps one rule's threshold at a time, leaving the rest of the example
policy as it is:

- ``bulk-customer-read`` threshold (rows per hour), against a family of
  in-scope bulk reads at speeds from 1,000 to 180,000 rows/hour, each after
  the same 20,000-row table;
- ``message-flood`` ``max_calls`` (per 5 minutes), against a family of
  message streams from 120 to 3,600 messages/hour, 200 messages each.

For every setting it records the attack family's detection rate and mean
damage before detection (a missed attack counts its full damage), and the
false-positive rate on the whole benign suite (n=9, so FP moves in steps
of 11 percentage points). The suite's own attacks are re-scored too, so the
CSV shows what a threshold change does to the fixed scenarios.

Writes results/threshold_sweep.csv and results/figures/threshold_sweep.png.

Run:  python experiments/threshold_sweep.py
"""

from __future__ import annotations

import dataclasses
from statistics import mean

from _common import BLUE, INK_2, ORANGE, SEED, policy, print_table, pyplot, save, write_csv

from bench.score import score
from bench.suites import all_scenarios
from bench.suites.adversarial import bulk_dump, notification_abuse
from seqmon import SequencePolicy

BULK_THRESHOLDS = (500, 1_000, 2_000, 3_000, 4_000, 4_500, 5_000, 6_000, 8_000, 12_000, 19_000)
BULK_RATES = (1_000, 2_000, 3_000, 4_000, 4_500, 6_000, 8_000, 12_000, 24_000, 60_000, 180_000)
FLOOD_LIMITS = (5, 10, 15, 20, 25, 30, 40, 60, 100)
MSG_GAPS = (1.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0, 30.0)


def with_rule(pol: SequencePolicy, name: str, **changes) -> SequencePolicy:
    rules = [dataclasses.replace(r, **changes) if r.name == name else r for r in pol.rules]
    return dataclasses.replace(pol, rules=rules)


def sweep(rule: str, field: str, values, family, suite_filter) -> list[dict]:
    base = policy()
    scenarios = all_scenarios(SEED)
    benign = [s for s in scenarios if not s.is_attack]
    suite_attacks = [s for s in scenarios if s.is_attack and suite_filter(s)]
    rows = []
    for v in values:
        pol = with_rule(base, rule, **{field: v})
        fam = [score(s, pol) for s in family]
        ben = [score(s, pol) for s in benign]
        sui = [score(s, pol) for s in suite_attacks]
        rows.append({
            "rule": rule,
            "setting": v,
            "family_detected": sum(r.detected for r in fam),
            "family_n": len(fam),
            "family_mean_damage": mean(r.damage_before_detection for r in fam),
            "family_mean_damage_detected": (
                mean(r.damage_before_detection for r in fam if r.detected)
                if any(r.detected for r in fam) else None
            ),
            "fp": sum(r.detected for r in ben),
            "benign_n": len(ben),
            "fp_rate": sum(r.detected for r in ben) / len(ben),
            "fp_scenarios": ";".join(r.name for r in ben if r.detected),
            "suite_detected": sum(r.detected for r in sui),
            "suite_n": len(sui),
        })
    return rows


def plot(bulk: list[dict], flood: list[dict]) -> None:
    plt = pyplot()
    from matplotlib.ticker import FuncFormatter

    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.2))
    specs = [
        (axes[0], bulk, BLUE, "bulk-customer-read threshold", 5_000,
         "Mean rows read before detection", lambda v: f"{v / 1000:g}k"),
        (axes[1], flood, ORANGE, "message-flood limit", 30,
         "Mean messages sent before detection", lambda v: f"{v:g}"),
    ]
    for ax, rows, color, title, default, ylabel, fmt in specs:
        xs = [100 * r["fp_rate"] for r in rows]
        ys = [r["family_mean_damage"] for r in rows]
        ax.plot(xs, ys, color=color, lw=1.5, zorder=2)
        ax.scatter(xs, ys, s=40, color=color, edgecolor="white", linewidth=1, zorder=3)
        for i, (x, y, r) in enumerate(zip(xs, ys, rows)):
            # The two lowest settings sit close together; drop the first label.
            ax.annotate(fmt(r["setting"]), (x, y), xytext=(7, -9 if i == 0 else -1),
                        textcoords="offset points", fontsize=8, color=INK_2)
            if r["setting"] == default:
                ax.scatter([x], [y], s=150, facecolor="none", edgecolor=INK_2,
                           linewidth=1, zorder=4)
                ax.annotate("example policy", (x, y), xytext=(12, 10),
                            textcoords="offset points", fontsize=8, color=INK_2)
        ax.set_xlabel("False-positive rate on benign suite (%, n=9)")
        ax.set_ylabel(ylabel)
        ax.set_title(f"Sweeping the {title}")
        ax.set_xlim(-4, max(max(xs) + 12, 30))
        ax.set_ylim(0, None)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    fig.text(0.01, -0.04,
             "Each point is one threshold setting (labelled). Damage is averaged over an attack family "
             "of increasing speed; a missed attack counts its full damage\n(20,000 rows or 200 messages). "
             "Lower-left is better. Circled: the example policy's setting.",
             fontsize=8, color=INK_2)
    fig.tight_layout(w_pad=3)
    save(fig, "threshold_sweep.png")
    plt.close(fig)


def main() -> int:
    bulk_family = [
        bulk_dump(SEED, rows_per_call=500, gap=500 * 3600 / rate, name=f"bulk-{rate}")
        for rate in BULK_RATES
    ]
    flood_family = [notification_abuse(SEED, messages=200, gap=g) for g in MSG_GAPS]

    bulk = sweep("bulk-customer-read", "threshold", BULK_THRESHOLDS, bulk_family,
                 lambda s: s.damage_unit == "rows" and "cumulative-sum" in s.targets)
    flood = sweep("message-flood", "max_calls", FLOOD_LIMITS, flood_family,
                  lambda s: "rate" in s.targets)
    write_csv("threshold_sweep.csv", bulk + flood)

    for title, rows, unit in (
        ("bulk-customer-read threshold (rows / 1h)", bulk, "rows"),
        ("message-flood max_calls (messages / 5 min)", flood, "msgs"),
    ):
        print(f"\n  {title}\n")
        print_table(
            ["setting", "family detected", f"mean {unit} before det.",
             "benign FPs", "FP scenarios", "suite attacks detected"],
            [[f"{r['setting']:,}", f"{r['family_detected']}/{r['family_n']}",
              f"{r['family_mean_damage']:,.0f}", f"{r['fp']}/{r['benign_n']}",
              r["fp_scenarios"].replace(";", ", ") or None,
              f"{r['suite_detected']}/{r['suite_n']}"] for r in rows],
            align="rrrrlr",
        )
    plot(bulk, flood)
    print("\n  wrote results/threshold_sweep.csv, results/figures/threshold_sweep.png\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
