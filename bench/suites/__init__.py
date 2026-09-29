"""Scenario suites: adversarial, evasion and benign.

``all_scenarios(seed)`` is the single entry point the experiments use, so
every script replays the same traces.
"""

from __future__ import annotations

from bench.scenario import Scenario

from . import adversarial, benign, evasion


def all_scenarios(seed: int = 0) -> list[Scenario]:
    """Every scenario in every suite, in a stable order.

    Raises:
        ValueError: If two scenarios share a name.
    """
    out = adversarial.scenarios(seed) + evasion.scenarios(seed) + benign.scenarios(seed)
    names = [s.name for s in out]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ValueError(f"duplicate scenario names: {sorted(dupes)}")
    return out


__all__ = ["adversarial", "all_scenarios", "benign", "evasion"]
