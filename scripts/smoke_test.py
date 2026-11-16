#!/usr/bin/env python3
"""Fast self-check of the simulation: determinism, invariants, and that both
outcomes are reachable. Run:  python scripts/smoke_test.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cyberrange.engine import Engine, SimConfig  # noqa: E402


def run(seed, **kw):
    eng = Engine(SimConfig(seed=seed, **kw))
    while not eng.finished:
        eng.step()
    return eng


def main() -> int:
    fails = []

    # 1. determinism: same seed -> identical outcome & health
    a, b = run(7), run(7)
    if (a.outcome, round(a.env.asset_health(), 6)) != \
       (b.outcome, round(b.env.asset_health(), 6)):
        fails.append("non-deterministic for fixed seed")

    # 2. invariants across many seeds
    for s in range(25):
        eng = run(s)
        for n in eng.env.nodes.values():
            if not (0.0 <= n.integrity <= 1.0):
                fails.append(f"seed {s}: integrity out of range on {n.id}")
            if not (0.0 <= n.detection <= 1.0):
                fails.append(f"seed {s}: detection out of range on {n.id}")
        h = eng.env.asset_health()
        if not (0.0 <= h <= 1.0):
            fails.append(f"seed {s}: asset_health out of range: {h}")
        if eng.env.tick > eng.cfg.max_ticks:
            fails.append(f"seed {s}: exceeded max_ticks")
        if eng.outcome not in ("attacker", "defender", "draw"):
            fails.append(f"seed {s}: bad outcome {eng.outcome}")

    # 3. both sides can win across the seed range (balance sanity)
    outs = [run(s).outcome for s in range(40)]
    if outs.count("attacker") == 0:
        fails.append("attacker never wins across 40 seeds (unbalanced)")
    if outs.count("defender") == 0:
        fails.append("defender never wins across 40 seeds (unbalanced)")

    # 4. difficulty dials actually move the needle
    low = [run(s, attacker_skill=0.5).outcome for s in range(30)].count("attacker")
    high = [run(s, attacker_skill=1.4).outcome for s in range(30)].count("attacker")
    if not high > low:
        fails.append(f"attacker_skill dial ineffective (low={low} high={high})")

    # 5. state snapshot is JSON-serializable
    import json
    json.dumps(run(1).state())

    if fails:
        print("SMOKE TEST FAILED:")
        for f in fails:
            print("  -", f)
        return 1
    aw = outs.count("attacker")
    print(f"SMOKE TEST PASSED  (over 40 seeds: attacker {aw}, "
          f"defender {outs.count('defender')}, draw {outs.count('draw')}; "
          f"skill dial low={low} high={high})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
