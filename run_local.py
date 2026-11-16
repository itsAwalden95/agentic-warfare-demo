#!/usr/bin/env python3
"""Run a full simulation headless in the terminal (no server needed).

Usage:
    python run_local.py                 # default medium scenario
    python run_local.py --size large --attackers 16 --defenders 12
    python run_local.py --seed 42 --quiet
"""
import argparse

from cyberrange.engine import SimConfig, run_headless


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--size", choices=["small", "medium", "large"], default="medium")
    p.add_argument("--attackers", type=int, default=8)
    p.add_argument("--defenders", type=int, default=8)
    p.add_argument("--max-ticks", type=int, default=120)
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    cfg = SimConfig(seed=args.seed, size=args.size, attackers=args.attackers,
                    defenders=args.defenders, max_ticks=args.max_ticks)
    run_headless(cfg, verbose=not args.quiet)


if __name__ == "__main__":
    main()
