"""Per-event overhead and memory: incremental evaluator vs a naive rescan.

Reproduces the README microbenchmark -- a synthetic stream at one event per
second against the six-rule example policy -- and adds the naive baseline
from ``bench/naive.py``, which rescans the whole session history on every
event. Both are measured as the *marginal* cost of an event arriving when
the session already holds ``n`` events:

- incremental: feed ``n`` events through a fresh ``SequenceMonitor``; time
  the last 1,000 (best of 3 runs);
- naive: pre-load ``n`` events of history, then time the next few calls
  (best of 3). Pre-loading skips the O(n^2) warm-up, which is what makes
  measuring it at 100,000 events affordable.

This is a microbenchmark of the monitoring layer alone, on this machine; it
is not end-to-end agent overhead.

Also runs a correctness cross-check: the naive evaluator computes each rule
straight from its definition, so every suite scenario is replayed through
both and the alerts compared.

Writes results/overhead.csv, results/crosscheck.json and
results/figures/overhead.png.

Run:  python experiments/overhead.py
"""

from __future__ import annotations

import platform
import time

from _common import BLUE, INK_2, ORANGE, SEED, policy, print_table, pyplot, save, write_csv, write_json

from bench.naive import NaiveEvaluator
from bench.suites import all_scenarios
from bench.suites.evasion import backstop_flush
from seqmon import SequenceEvaluator, SequenceMonitor, ToolCallEvent
from seqmon.state import StateStore

SIZES = (1_000, 5_000, 20_000, 100_000)
TABLES = ("customer_db.orders", "customer_db.order_items")
CROSSCHECK_MAX_EVENTS = 5_000   # naive replay is O(n^2); larger traces are skipped


def event(i: int) -> ToolCallEvent:
    return ToolCallEvent(
        action="customer_db.query", agent_id="bench", session_id="s1",
        timestamp=float(i), resource=TABLES[i % 2], magnitude=1.0,
    )


def incremental_cost(n: int, tail: int = 1_000, repeats: int = 3) -> tuple[float, int]:
    """(marginal µs/event over the last ``tail`` events, retained events)."""
    events = [event(i) for i in range(n)]
    tail = min(tail, n)
    best = float("inf")
    retained = 0
    for _ in range(repeats):
        mon = SequenceMonitor(policy(), log=False)
        for e in events[:-tail]:
            mon.observe(e)
        t0 = time.perf_counter()
        for e in events[-tail:]:
            mon.observe(e)
        best = min(best, (time.perf_counter() - t0) / tail)
        retained = mon.footprint("s1")
    return best * 1e6, retained


def naive_cost(n: int, repeats: int = 3) -> float:
    """Marginal µs/event with ``n`` events of history already held."""
    k = max(5, min(200, 2_000_000 // n))
    history = [event(i) for i in range(n)]
    probe = [event(n + j) for j in range(k)]
    best = float("inf")
    for _ in range(repeats):
        ev = NaiveEvaluator(policy())
        ev.history = list(history)
        t0 = time.perf_counter()
        for e in probe:
            ev.observe(e)
        best = min(best, (time.perf_counter() - t0) / k)
    return best * 1e6


def _alerts_incremental(events, max_events: int) -> list[tuple[int, str]]:
    ev = SequenceEvaluator(policy(), store=StateStore(max_events_per_window=max_events))
    return [(i, v.rule) for i, e in enumerate(events) for v in ev.observe(e)]


def _alerts_naive(events) -> list[tuple[int, str]]:
    ev = NaiveEvaluator(policy())
    return [(i, rule) for i, e in enumerate(events) for rule, _ in ev.observe(e)]


def crosscheck() -> dict:
    """Replay scenarios through both evaluators (passively) and compare alerts."""
    cases = [(s.name, s.events, 10_000) for s in all_scenarios(SEED)]
    # A scaled-down backstop flush that fits the naive replay: 12 pages with
    # 50 fillers each against a 500-event cap.
    small = backstop_flush(SEED, filler_per_page=50)
    cases.append(("backstop-flush (cap 500, 50 fillers/page)", small.events, 500))
    agree, differ, skipped = [], [], []
    for name, events, cap in cases:
        if len(events) > CROSSCHECK_MAX_EVENTS:
            skipped.append(name)
            continue
        inc = _alerts_incremental(events, cap)
        nai = _alerts_naive(events)
        if inc == nai:
            agree.append(name)
        else:
            differ.append({"scenario": name, "incremental": inc, "naive": nai})
    return {"agree": agree, "differ": differ, "skipped_too_long": skipped}


def plot(rows: list[dict]) -> None:
    plt = pyplot()
    from matplotlib.ticker import FuncFormatter

    ns = [r["events"] for r in rows]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10.5, 4.0))
    for ax, key_inc, key_naive, ylabel, title in (
        (a1, "incremental_us", "naive_us", "Marginal cost per event (µs, log)",
         "Per-event cost vs session length"),
        (a2, "retained_incremental", "retained_naive", "Events held in memory (log)",
         "Memory vs session length"),
    ):
        yi = [r[key_inc] for r in rows]
        yn = [r[key_naive] for r in rows]
        ax.plot(ns, yn, color=ORANGE, marker="o", markeredgecolor="white")
        ax.plot(ns, yi, color=BLUE, marker="o", markeredgecolor="white")
        ax.annotate("naive rescan", (ns[-1], yn[-1]), xytext=(-8, 8),
                    textcoords="offset points", ha="right", color=INK_2, fontsize=8.5)
        ax.annotate("incremental (seqmon)", (ns[-1], yi[-1]), xytext=(-8, 8),
                    textcoords="offset points", ha="right", color=INK_2, fontsize=8.5)
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel("Events already in the session (log)")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.6g}"))
    a1.legend(["naive rescan", "incremental (seqmon)"], loc="upper left", fontsize=8)
    fig.text(0.01, -0.07, "Synthetic stream, 1 event/s, six-rule example policy. Microbenchmark of the "
             "monitoring layer only, single machine.\nIncremental memory counts one entry per rule window, "
             "so an event can be held up to three times; it plateaus at the sum of the window spans.",
             fontsize=8, color=INK_2)
    fig.tight_layout(w_pad=3)
    save(fig, "overhead.png")
    plt.close(fig)


def main() -> int:
    rows = []
    for n in SIZES:
        inc_us, retained = incremental_cost(n)
        rows.append({
            "events": n,
            "retained_incremental": retained,
            "incremental_us": round(inc_us, 2),
            "retained_naive": n,
            "naive_us": round(naive_cost(n), 1),
        })
    for r in rows:
        r["speedup"] = round(r["naive_us"] / r["incremental_us"], 1)
    write_csv("overhead.csv", rows)

    print(f"\n  Overhead at 1 event/s, example policy ({platform.python_implementation()} "
          f"{platform.python_version()}, {platform.machine()})\n")
    print_table(
        ["events", "retained", "µs/event (incremental)", "retained (naive)",
         "µs/event (naive)", "naive / incremental"],
        [[f"{r['events']:,}", f"{r['retained_incremental']:,}", f"{r['incremental_us']:.1f}",
          f"{r['retained_naive']:,}", f"{r['naive_us']:,.0f}", f"{r['speedup']:,.0f}x"]
         for r in rows],
        align="rrrrrr",
    )

    cc = crosscheck()
    write_json("crosscheck.json", cc)
    print(f"\n  Cross-check vs naive oracle: {len(cc['agree'])} agree, "
          f"{len(cc['differ'])} differ, {len(cc['skipped_too_long'])} skipped "
          f"(> {CROSSCHECK_MAX_EVENTS:,} events: {', '.join(cc['skipped_too_long']) or '-'})")
    for d in cc["differ"]:
        print(f"    differs: {d['scenario']}")
        print(f"      incremental alerts {d['incremental'] or 'none'}")
        print(f"      naive alerts       {d['naive'] or 'none'}")
    plot(rows)
    print("\n  wrote results/overhead.csv, results/crosscheck.json, results/figures/overhead.png\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
