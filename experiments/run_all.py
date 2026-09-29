"""Regenerate every result and figure in one command.

Runs the two worked-example scripts, the suite runner and the four
experiments in order, each in its own interpreter, and stops at the first
failure. Everything lands in results/ (gitignored).

Run:  python experiments/run_all.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

STEPS = (
    "bench/adversarial/slow_exfiltration.py",
    "bench/benign/legitimate_reporting.py",
    "experiments/run_suite.py",
    "experiments/detection.py",
    "experiments/latency_damage.py",
    "experiments/threshold_sweep.py",
    "experiments/overhead.py",
)


def main() -> int:
    for step in STEPS:
        print(f"\n=== {step} " + "=" * max(0, 60 - len(step)), flush=True)
        t0 = time.perf_counter()
        code = subprocess.call([sys.executable, str(ROOT / step)], cwd=ROOT)
        if code != 0:
            print(f"\n{step} failed with exit code {code}", file=sys.stderr)
            return code
        print(f"--- {step} ok ({time.perf_counter() - t0:.1f}s)", flush=True)
    print(f"\nAll {len(STEPS)} steps passed. Results in {ROOT / 'results'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
