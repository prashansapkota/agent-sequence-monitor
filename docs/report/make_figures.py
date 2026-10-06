"""Figures for Progress Report 3, computed from the repository (nothing typed in).

- architecture.png: where seqmon sits relative to AGT's per-call check.
- demo_timeline.png: cumulative rows read per step of scripts/demo_trace.py,
  per-call engine alone vs with seqmon (auto-approve and deny-escalation runs).
- test_evidence.png: the real pytest/coverage/ruff/mypy summary lines, read from
  docs/evidence/ and the tools, rendered as a terminal panel.

Usage: python docs/report/make_figures.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import demo_trace  # noqa: E402

OUT = ROOT / "docs" / "report" / "figures"
BLUE, ORANGE, GREY, INK, MUTED = "#2a78d6", "#eb6834", "#8a8a85", "#0b0b0b", "#5f5f5a"
plt.rcParams.update({"font.family": "Times New Roman", "font.size": 11})


def _style(ax: plt.Axes) -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=MUTED)
    ax.grid(axis="y", color="#e6e6e3", linewidth=0.8)
    ax.set_axisbelow(True)


def demo_timeline() -> Path:
    trace = demo_trace.build_trace()
    rows = [e.value("records") for e in trace]
    steps = list(range(1, len(trace) + 1))

    def cumulative(executed: list[bool]) -> list[float]:
        total, out = 0.0, []
        for r, ok in zip(rows, executed, strict=True):
            total += r if ok else 0.0
            out.append(total)
        return out

    alone = cumulative([True] * len(trace))
    approve = demo_trace.run(verbose=False)
    deny = demo_trace.run(verbose=False, deny_escalations=True)
    with_seq = cumulative([o.executed for o in approve])
    with_deny = cumulative([o.executed for o in deny])
    stop_approve = next(i for i, o in enumerate(approve, 1) if o.decision and o.decision.terminate)
    first_esc = next(i for i, o in enumerate(approve, 1) if o.decision and o.decision.violations)
    stop_deny = max(i for i, o in enumerate(deny, 1) if o.executed)

    fig, ax = plt.subplots(figsize=(7.5, 3.9), dpi=200)
    _style(ax)
    ax.step(steps, alone, where="post", color=GREY, linewidth=2,
            label="Per-call engine alone (all 32 calls allowed)")
    ax.step(steps, with_seq, where="post", color=BLUE, linewidth=2,
            label="With seqmon, escalations approved")
    ax.step(steps, with_deny, where="post", color=ORANGE, linewidth=2,
            label="With seqmon, escalation denied")
    ax.axhline(5000, color=MUTED, linestyle="--", linewidth=1)
    ax.text(1, 5150, "bulk-customer-read threshold: 5,000 rows / 1 h", color=MUTED, fontsize=9)
    ax.annotate(f"ESCALATE at step {first_esc}\n(scope drift: 4 strays > 3)",
                xy=(first_esc, with_deny[first_esc - 1]), xytext=(first_esc + 3, 250),
                fontsize=9, color=INK, arrowprops={"arrowstyle": "->", "color": MUTED})
    ax.annotate(f"TERMINATE at step {stop_approve}\n({with_seq[stop_approve - 1]:,.0f} rows)",
                xy=(stop_approve, with_seq[stop_approve - 1]),
                xytext=(stop_approve + 3.5, 2600), fontsize=9, color=INK,
                arrowprops={"arrowstyle": "->", "color": MUTED})
    ax.text(len(trace), alone[-1], f"  {alone[-1]:,.0f}", va="center", fontsize=9, color=GREY)
    ax.text(len(trace), with_seq[-1], f"  {with_seq[-1]:,.0f}", va="center", fontsize=9,
            color=BLUE)
    ax.text(len(trace), with_deny[-1], f"  {with_deny[-1]:,.0f}", va="center", fontsize=9,
            color=ORANGE)
    ax.set_xlim(0.5, len(trace) + 3)
    ax.set_ylim(0, 12000)
    ax.set_xlabel("Tool call (step) in the demo trace")
    ax.set_ylabel("Customer rows read (cumulative)")
    ax.yaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter("{x:,.0f}"))
    ax.legend(loc="upper left", frameon=False, fontsize=9)
    assert stop_deny == first_esc
    fig.tight_layout()
    path = OUT / "demo_timeline.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def architecture() -> Path:
    fig, ax = plt.subplots(figsize=(7.5, 2.6), dpi=200)
    ax.set_xlim(-3, 106)
    ax.set_ylim(-5, 35)
    ax.axis("off")

    def box(x: float, y: float, w: float, h: float, title: str, body: str,
            edge: str = MUTED) -> None:
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.4,rounding_size=1.2",
                                    facecolor="#fcfcfb", edgecolor=edge, linewidth=1.4))
        ax.text(x + w / 2, y + h - 2.6, title, ha="center", va="top", fontsize=10,
                fontweight="bold", color=INK)
        ax.text(x + w / 2, y + h - 7.2, body, ha="center", va="top", fontsize=8.5, color=MUTED,
                linespacing=1.3)

    def arrow(x1: float, y1: float, x2: float, y2: float, label: str = "") -> None:
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=12,
                                     color=MUTED, linewidth=1.2))
        if label:
            ax.text((x1 + x2) / 2, (y1 + y2) / 2 + 2.2, label, ha="center", fontsize=8,
                    color=MUTED)

    box(-2, 10, 17, 17, "AI agent", "tool call\n(action, resource,\nrows / cost)")
    box(20, 10, 21, 17, "Per-call policy",
        "AGT-style rules\n(stateless; here\nFakePerCallEngine)")
    box(46, 6, 30, 26, "seqmon (this project)",
        "on_action(event)\n\nper-session windows\nrunning sums, counts\n"
        "cumulative · rate\nordering · scope", edge=BLUE)
    box(82, 16, 21, 17, "Responses", "LOG\nESCALATE\nTERMINATE")
    box(82, -3, 21, 14, "Hooks", "EscalationHook\nTerminationHook")
    arrow(15.6, 19, 19.4, 19)
    arrow(41.6, 19, 45.4, 19)
    arrow(30.5, 9.4, 30.5, 3, "")
    ax.text(30.5, 1, "denied: stops here", ha="center", fontsize=8, color=MUTED)
    arrow(76.6, 26, 81.4, 26)
    arrow(92.5, 15.4, 92.5, 11.6)
    path = OUT / "architecture.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def test_evidence() -> Path:
    test_run = (ROOT / "docs/evidence/test_run.txt").read_text().splitlines()
    coverage = (ROOT / "docs/evidence/coverage.txt").read_text().splitlines()
    venv = ROOT / ".venv" / "bin"
    ruff = subprocess.run([str(venv / "ruff"), "check", "."], cwd=ROOT, capture_output=True,
                          text=True).stdout.strip().splitlines()[-1]
    mypy = subprocess.run([str(venv / "mypy"), "src"], cwd=ROOT, capture_output=True,
                          text=True).stdout.strip().splitlines()[-1]
    cov_rows = [ln for ln in coverage if ln.startswith(("src/seqmon/", "TOTAL", "Name"))]
    lines = ["$ pytest --cov=seqmon", test_run[-1], ""]
    lines += [f"{ln.split()[0]:<34}{ln.split()[1]:>6}{ln.split()[2]:>6}{ln.split()[3]:>7}"
              for ln in cov_rows]
    lines += ["", "$ ruff check .", ruff, "$ mypy src", mypy]

    fig, ax = plt.subplots(figsize=(7.5, 0.21 * len(lines) + 0.4), dpi=200)
    fig.patch.set_facecolor("#1a1a19")
    ax.axis("off")
    for i, ln in enumerate(lines):
        color = "#9ec5f4" if ln.startswith("$") else "#e9e9e6"
        ax.text(0.01, 1 - (i + 0.8) / len(lines), ln, family="Courier New", fontsize=8.5,
                color=color, transform=ax.transAxes, va="center")
    path = OUT / "test_evidence.png"
    fig.savefig(path, facecolor=fig.get_facecolor(), bbox_inches="tight", pad_inches=0.12)
    plt.close(fig)
    return path


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for p in (architecture(), demo_timeline(), test_evidence()):
        print(p.relative_to(ROOT))
