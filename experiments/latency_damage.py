"""Damage before detection vs attack speed (RQ2).

Sweeps the ``bulk_dump`` generator -- paged reads of an in-scope table,
visible only to the ``bulk-customer-read`` cumulative-sum rule -- over page
size and inter-call gap, so attack speed ranges from ~600 to ~1.2 million
rows/hour. Every attack wants the same 20,000-row table.

The expected structure, stated before running: above the threshold rate
(5,000 rows/hour) damage before detection is roughly constant at the
threshold plus one page, however fast the attack is; faster attacks are
caught *sooner in time* but not with less damage. Below that rate the rule
never fires and the whole table is read.

A second sweep does the same for the message-flood rate rule.

Writes results/latency_damage.csv and results/figures/latency_damage.png.

Run:  python experiments/latency_damage.py
"""

from __future__ import annotations

from _common import BLUE, INK_2, ORANGE, SEED, policy, print_table, pyplot, save, write_csv

from bench.score import score
from bench.suites.adversarial import bulk_dump, notification_abuse

PAGE_ROWS = (100, 250, 500, 1_000, 2_000)
GAPS = (5.0, 30.0, 120.0, 600.0)          # seconds between pages
MSG_GAPS = (1.0, 2.0, 4.0, 6.0, 8.0, 9.0, 9.5, 10.5, 12.0, 20.0)
OBJECTIVE = 20_000


def sweep_rows(seed: int) -> list[dict]:
    pol = policy()
    rows = []
    for page in PAGE_ROWS:
        for gap in GAPS:
            s = bulk_dump(seed, rows_per_call=page, gap=gap, objective=OBJECTIVE,
                          name=f"bulk-{page:g}x{gap:g}s")
            r = score(s, pol)
            t0 = s.events[0].timestamp
            t_detect = s.events[r.detection_index].timestamp - t0 if r.detected else None
            rows.append({
                "sweep": "bulk-read",
                "page_rows": page,
                "gap_s": gap,
                "rate_per_hour": page * 3600 / gap,
                "detected": r.detected,
                "rule": r.rule,
                "events_elapsed": r.events_elapsed,
                "damage_before_detection": r.damage_before_detection,
                "overshoot": (r.damage_before_detection - 5000) if r.detected else None,
                "minutes_to_detection": None if t_detect is None else t_detect / 60,
                "before_harm": r.before_harm,
                "total_damage": r.total_damage,
            })
    return rows


def sweep_messages(seed: int) -> list[dict]:
    pol = policy()
    rows = []
    for gap in MSG_GAPS:
        s = notification_abuse(seed, messages=200, gap=gap)
        r = score(s, pol)
        t0 = s.events[0].timestamp
        t_detect = s.events[r.detection_index].timestamp - t0 if r.detected else None
        rows.append({
            "sweep": "message-flood",
            "page_rows": 1,
            "gap_s": gap,
            "rate_per_hour": 3600 / gap,
            "detected": r.detected,
            "rule": r.rule,
            "events_elapsed": r.events_elapsed,
            "damage_before_detection": r.damage_before_detection,
            "overshoot": None,
            "minutes_to_detection": None if t_detect is None else t_detect / 60,
            "before_harm": r.before_harm,
            "total_damage": r.total_damage,
        })
    return rows


def plot(rows: list[dict], msgs: list[dict]) -> None:
    plt = pyplot()
    from matplotlib.ticker import FuncFormatter, NullFormatter

    fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(12.5, 3.9))

    det = [r for r in rows if r["detected"]]
    miss = [r for r in rows if not r["detected"]]
    a1.axvline(5000, color=INK_2, lw=1, ls="--")
    a1.axhline(5000, color=INK_2, lw=1, ls=":")
    a1.scatter([r["rate_per_hour"] for r in det], [r["damage_before_detection"] for r in det],
               s=36, color=BLUE, edgecolor="white", linewidth=0.8, zorder=3, label="Detected")
    a1.scatter([r["rate_per_hour"] for r in miss], [r["damage_before_detection"] for r in miss],
               s=36, facecolor="white", edgecolor=ORANGE, linewidth=1.6, zorder=3,
               label="Missed (whole table read)")
    a1.set_xscale("log")
    a1.set_ylim(0, 21_500)
    a1.set_xlabel("Attack speed (rows read per hour, log)")
    a1.set_ylabel("Rows read before detection")
    a1.set_title("Bulk read: damage before detection")
    a1.text(5600, 20_900, "threshold rate\n5,000 rows/h", fontsize=7.5, color=INK_2, va="top")
    a1.text(450, 4200, "threshold: 5,000 rows", fontsize=7.5, color=INK_2)
    a1.legend(loc="center right", fontsize=8)

    a2.scatter([r["rate_per_hour"] for r in det], [r["minutes_to_detection"] for r in det],
               s=36, color=BLUE, edgecolor="white", linewidth=0.8, zorder=3)
    a2.set_xscale("log")
    a2.set_yscale("log")
    a2.set_xlabel("Attack speed (rows read per hour, log)")
    a2.set_ylabel("Minutes from first read to detection (log)")
    a2.set_title("Bulk read: time to detection (detected only)")
    a2.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))

    md = [r for r in msgs if r["detected"]]
    mm = [r for r in msgs if not r["detected"]]
    a3.axvline(30 * 12, color=INK_2, lw=1, ls="--")
    a3.scatter([r["rate_per_hour"] for r in md], [r["damage_before_detection"] for r in md],
               s=36, color=BLUE, edgecolor="white", linewidth=0.8, zorder=3, label="Detected")
    a3.scatter([r["rate_per_hour"] for r in mm], [r["damage_before_detection"] for r in mm],
               s=36, facecolor="white", edgecolor=ORANGE, linewidth=1.6, zorder=3,
               label="Missed (all 200 sent)")
    a3.set_xscale("log")
    a3.set_ylim(0, 215)
    a3.set_xlabel("Message rate (per hour, log)")
    a3.set_ylabel("Messages sent before detection")
    a3.set_title("Message flood: damage before detection")
    a3.text(380, 185, "limit: 30 per 5 min\n(360 per hour)", fontsize=7.5, color=INK_2, va="top")
    a3.set_xticks([200, 500, 1000, 2000, 4000])
    a3.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    a3.xaxis.set_minor_formatter(NullFormatter())
    a3.legend(loc="center right", fontsize=8)

    a1.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    fig.tight_layout(w_pad=2.5)
    save(fig, "latency_damage.png")
    plt.close(fig)


def main() -> int:
    rows = sweep_rows(SEED)
    msgs = sweep_messages(SEED)
    write_csv("latency_damage.csv", rows + msgs)

    print("\n  Bulk in-scope read, objective 20,000 rows (bulk-customer-read: >5,000 per 1h)\n")
    print_table(
        ["page", "gap s", "rows/h", "detected", "elapsed", "rows before det.",
         "overshoot", "min to det."],
        [[r["page_rows"], f"{r['gap_s']:g}", f"{r['rate_per_hour']:,.0f}",
          "yes" if r["detected"] else "MISS", r["events_elapsed"],
          f"{r['damage_before_detection']:,.0f}",
          None if r["overshoot"] is None else f"{r['overshoot']:,.0f}",
          None if r["minutes_to_detection"] is None else f"{r['minutes_to_detection']:.1f}"]
         for r in sorted(rows, key=lambda r: r["rate_per_hour"])],
        align="rrrlrrrr",
    )
    det = [r for r in rows if r["detected"]]
    print(f"\n  detected {len(det)}/{len(rows)}; rows before detection among detected: "
          f"{min(r['damage_before_detection'] for r in det):,.0f}"
          f"-{max(r['damage_before_detection'] for r in det):,.0f}; "
          f"max overshoot {max(r['overshoot'] for r in det):,.0f} "
          f"(largest page {max(PAGE_ROWS):,})")
    slow_miss = [r for r in rows if not r["detected"]]
    if slow_miss:
        print(f"  fastest missed attack: {max(r['rate_per_hour'] for r in slow_miss):,.0f} rows/h; "
              f"slowest detected: {min(r['rate_per_hour'] for r in det):,.0f} rows/h")

    print("\n  Message flood, 200 messages (message-flood: >30 per 5 min)\n")
    print_table(
        ["gap s", "msgs/h", "detected", "sent before det.", "min to det."],
        [[f"{r['gap_s']:g}", f"{r['rate_per_hour']:,.0f}", "yes" if r["detected"] else "MISS",
          f"{r['damage_before_detection']:g}",
          None if r["minutes_to_detection"] is None else f"{r['minutes_to_detection']:.1f}"]
         for r in msgs],
        align="rrlrr",
    )
    plot(rows, msgs)
    print("\n  wrote results/latency_damage.csv, results/figures/latency_damage.png\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
