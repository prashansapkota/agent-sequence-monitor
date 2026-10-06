"""Shared setup for the experiment scripts.

Paths are resolved from this file, so every script runs from any working
directory. ``src/`` and the repo root are put on ``sys.path`` so the
scripts work whether or not ``seqmon`` is pip-installed.
"""

from __future__ import annotations

import csv
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from seqmon import SequencePolicy, load_policy  # noqa: E402

POLICY_PATH = ROOT / "policies" / "example.yaml"
RESULTS = ROOT / "results"
FIGURES = RESULTS / "figures"

SEED = 0

# Colourblind-safe categorical order (validated reference palette; the
# first three slots pass all-pairs CVD checks). Assigned in fixed order.
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK_2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def policy() -> SequencePolicy:
    return load_policy(POLICY_PATH)


def ensure_dirs() -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)


def write_csv(name: str, rows: list[dict[str, Any]]) -> Path:
    ensure_dirs()
    path = RESULTS / name
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_json(name: str, data: Any) -> Path:
    ensure_dirs()
    path = RESULTS / name
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def print_table(headers: list[str], rows: Iterable[list[Any]], align: str | None = None) -> None:
    """Plain fixed-width table; ``align`` is a string of 'l'/'r' per column."""
    rows = [[("-" if c is None else str(c)) for c in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h)
              for i, h in enumerate(headers)]
    align = align or "l" * len(headers)

    def fmt(cells: list[str]) -> str:
        return "  ".join(
            c.rjust(w) if a == "r" else c.ljust(w)
            for c, w, a in zip(cells, widths, align, strict=False)
        )

    print("  " + fmt(headers))
    print("  " + "  ".join("-" * w for w in widths))
    for r in rows:
        print("  " + fmt(r))


def pyplot():
    """matplotlib.pyplot with the house style applied (Agg backend)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "figure.dpi": 150,
        "savefig.dpi": 150,
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "font.size": 9.5,
        "axes.titlesize": 11,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
        "axes.labelcolor": INK_2,
        "axes.edgecolor": INK_2,
        "axes.linewidth": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.6,
        "axes.axisbelow": True,
        "xtick.color": INK_2,
        "ytick.color": INK_2,
        "text.color": INK,
        "legend.frameon": False,
        "lines.linewidth": 2,
        "lines.markersize": 6,
    })
    return plt


def save(fig, name: str) -> Path:
    ensure_dirs()
    path = FIGURES / name
    fig.savefig(path, dpi=150, bbox_inches="tight")
    return path
