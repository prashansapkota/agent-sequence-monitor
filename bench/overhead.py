"""Per-event evaluation time and memory vs session length.

Feeds one synthetic session of N events (N = 100, 1k, 10k, 100k; one event
per second, cycling through every action the six-rule ``policies/example.yaml``
tracks, so all four constraint types are exercised) through a fresh
``SequenceMonitor`` and records:

- ``us_per_event_mean``: wall time of the whole session / N (best of 3 runs);
- ``us_per_event_tail``: wall time of the last min(N, 1,000) events / that
  count (best of 3) -- the marginal cost once the session already holds N
  events;
- ``tracemalloc_current_kib`` / ``tracemalloc_peak_kib``: Python heap held by
  the monitor after the session (its state plus the event objects it retains),
  and the peak during it (separate run under ``tracemalloc``, not timed);
- ``retained_events``: events held across all rule windows
  (``SequenceMonitor.footprint``) -- the bounded-memory quantity.

A microbenchmark of the monitoring layer alone on this machine; not
end-to-end agent overhead. Machine details are written next to the results.

Writes bench/results/overhead.csv, bench/results/overhead.png and
bench/results/machine.txt.

Run:  python bench/overhead.py
"""

from __future__ import annotations

import csv
import gc
import platform
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from seqmon import SequenceMonitor, ToolCallEvent, load_policy

POLICY = ROOT / "policies" / "example.yaml"
OUT = ROOT / "bench" / "results"
SIZES = (100, 1_000, 10_000, 100_000)
REPEATS = 3
TAIL = 1_000

# (action, resource, magnitude) cycle: touches every rule in example.yaml.
CYCLE = (
    ("customer_db.query", "customer_db.orders", 1.0),
    ("customer_db.query", "customer_db.order_items", 1.0),
    ("files.read", "files/reports", 0.0),
    ("llm.completion", None, 0.0001),
    ("messaging.send", None, 0.0),
    ("files.write", "files/reports", 0.0),
)


def events(n: int) -> list[ToolCallEvent]:
    return [
        ToolCallEvent(action=a, agent_id="bench", session_id="s1", timestamp=float(i),
                      resource=r, magnitude=m)
        for i, (a, r, m) in ((i, CYCLE[i % len(CYCLE)]) for i in range(n))
    ]


def timed(n: int) -> tuple[float, float, int]:
    evs = events(n)
    tail = min(TAIL, n)
    policy = load_policy(POLICY)
    best_mean = best_tail = float("inf")
    retained = 0
    for _ in range(REPEATS):
        mon = SequenceMonitor(policy, log=False)
        gc.collect()
        t0 = time.perf_counter()
        for e in evs[:-tail]:
            mon.on_action(e)
        t1 = time.perf_counter()
        for e in evs[-tail:]:
            mon.on_action(e)
        t2 = time.perf_counter()
        best_mean = min(best_mean, (t2 - t0) / n)
        best_tail = min(best_tail, (t2 - t1) / tail)
        retained = mon.footprint("s1")
    return best_mean * 1e6, best_tail * 1e6, retained


def memory(n: int) -> tuple[float, float]:
    # Events are created one at a time inside the traced region and dropped
    # by the caller after use, so the heap figure includes the event objects
    # the monitor retains in its windows, and nothing else of the trace.
    policy = load_policy(POLICY)
    gc.collect()
    tracemalloc.start()
    base, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    mon = SequenceMonitor(policy, log=False)
    for i in range(n):
        a, r, m = CYCLE[i % len(CYCLE)]
        mon.on_action(ToolCallEvent(action=a, agent_id="bench", session_id="s1",
                                    timestamp=float(i), resource=r, magnitude=m))
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del mon
    return (current - base) / 1024, (peak - base) / 1024


def sh(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def machine() -> str:
    mem = sh("sysctl", "-n", "hw.memsize")
    mem_gb = f"{int(mem) / 2**30:.0f} GB" if mem.isdigit() else mem
    lines = [
        f"date: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"cpu: {sh('sysctl', '-n', 'machdep.cpu.brand_string')}",
        f"cores: {sh('sysctl', '-n', 'hw.ncpu')}",
        f"memory: {mem_gb} ({mem} bytes)",
        f"os: {sh('sw_vers', '-productName')} {sh('sw_vers', '-productVersion')} "
        f"(build {sh('sw_vers', '-buildVersion')})",
        f"kernel: {platform.platform()}",
        f"python: {platform.python_implementation()} {platform.python_version()}",
        f"policy: {POLICY.relative_to(ROOT)} ({len(load_policy(POLICY).rules)} rules)",
        f"git: {sh('git', '-C', str(ROOT), 'rev-parse', '--short', 'HEAD')}"
        f" (working tree may include uncommitted changes)",
    ]
    return "\n".join(lines) + "\n"


def plot(rows: list[dict[str, float]], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    blue, orange, ink2, grid = "#2a78d6", "#eb6834", "#52514e", "#e4e3df"
    n = [r["session_events"] for r in rows]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4), dpi=150)
    ax1.plot(n, [r["us_per_event_mean"] for r in rows], "-o", color=blue, lw=2, ms=6,
             label="mean over whole session")
    ax1.plot(n, [r["us_per_event_tail"] for r in rows], "-s", color=orange, lw=2, ms=6,
             label=f"last {TAIL:,} events")
    ax1.set_title("Evaluation time per event")
    ax1.set_ylabel("time per event (µs)")
    ax1.set_ylim(bottom=0)
    ax1.legend(frameon=False, fontsize=8)
    ax2.plot(n, [r["tracemalloc_current_kib"] for r in rows], "-o", color=blue, lw=2, ms=6,
             label="heap held after session")
    ax2.plot(n, [r["tracemalloc_peak_kib"] for r in rows], "-s", color=orange, lw=2, ms=6,
             label="peak during session")
    for i, r in enumerate(rows):
        last = i == len(rows) - 1
        ax2.annotate(f"{int(r['retained_events']):,} events", (r["session_events"],
                     r["tracemalloc_current_kib"]), textcoords="offset points",
                     xytext=(-8, 4) if last else (0, 10), ha="right" if last else "center",
                     fontsize=7, color=ink2)
    ax2.set_title("Monitor memory (labels: events retained in windows)")
    ax2.set_ylabel("memory (KiB, tracemalloc)")
    ax2.set_ylim(bottom=0)
    ax2.legend(frameon=False, fontsize=8, loc="upper left")
    for ax in (ax1, ax2):
        ax.set_xscale("log")
        ax.set_xlabel("session length (events, log scale)")
        ax.grid(True, color=grid, lw=0.8)
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("seqmon overhead vs session length (6-rule example policy, 1 event/s)",
                 fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for n in SIZES:
        mean_us, tail_us, retained = timed(n)
        cur_kib, peak_kib = memory(n)
        rows.append({
            "session_events": n,
            "us_per_event_mean": round(mean_us, 3),
            "us_per_event_tail": round(tail_us, 3),
            "tracemalloc_current_kib": round(cur_kib, 1),
            "tracemalloc_peak_kib": round(peak_kib, 1),
            "retained_events": retained,
        })
        print(f"n={n:>7,}  mean {mean_us:6.2f} µs/event  tail {tail_us:6.2f} µs/event  "
              f"heap {cur_kib:9.1f} KiB  peak {peak_kib:9.1f} KiB  retained {retained:,}")
    with (OUT / "overhead.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (OUT / "machine.txt").write_text(machine(), encoding="utf-8")
    plot(rows, OUT / "overhead.png")
    print(f"wrote {OUT / 'overhead.csv'}, {OUT / 'overhead.png'}, {OUT / 'machine.txt'}")


if __name__ == "__main__":
    main()
