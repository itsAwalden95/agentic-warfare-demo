"""Scoring and derived metrics for a running simulation."""
from __future__ import annotations

from dataclasses import dataclass, field

from .environment import Environment


@dataclass
class Metrics:
    first_detection_tick: int | None = None
    first_compromise_tick: int | None = None
    first_impact_tick: int | None = None
    detections: int = 0
    evictions: int = 0
    exploits_success: int = 0
    exploits_failed: int = 0
    impacts: int = 0

    def note_event(self, side: str, action: str, success: bool, tick: int) -> None:
        if side == "attacker" and action == "exploit":
            if success:
                self.exploits_success += 1
                if self.first_compromise_tick is None:
                    self.first_compromise_tick = tick
            else:
                self.exploits_failed += 1
        if side == "attacker" and action == "impact" and success:
            self.impacts += 1
            if self.first_impact_tick is None:
                self.first_impact_tick = tick
        if side == "defender" and action in ("detect", "hunt") and success:
            self.detections += 1
            if self.first_detection_tick is None:
                self.first_detection_tick = tick
        if side == "defender" and action == "evict" and success:
            self.evictions += 1

    def scoreboard(self, env: Environment) -> dict:
        health = env.asset_health()
        availability = env.availability()
        comp = env.compromised_nodes()
        deepest = min((n.level for n in comp), default=5)
        process_down = any(n.integrity < 0.3 for n in env.process_nodes())
        # Composite 0..100 scores for the two sides.
        attacker_score = round(
            (1.0 - health) * 60
            + (5 - deepest) / 5 * 20
            + min(self.impacts, 10) * 2, 1)
        # Defender is rewarded for keeping the asset healthy AND available --
        # over-isolating to stop the attack costs availability points.
        defender_score = round(
            health * 45
            + availability * 20
            + min(self.detections, 20) * 0.5
            + min(self.evictions, 20) * 0.75, 1)
        # mean-time metrics (in ticks)
        mttd = self.first_detection_tick
        mttc = self.first_compromise_tick
        mttd_rel = (mttd - mttc) if (mttd and mttc and mttd >= mttc) else None
        return {
            "asset_health": round(health, 4),
            "availability": round(availability, 4),
            "deepest_level": deepest,
            "compromised": len(comp),
            "process_down": process_down,
            "detections": self.detections,
            "evictions": self.evictions,
            "exploits_success": self.exploits_success,
            "exploits_failed": self.exploits_failed,
            "impacts": self.impacts,
            "first_compromise_tick": self.first_compromise_tick,
            "first_detection_tick": self.first_detection_tick,
            "mean_time_to_detect": mttd_rel,
            "attacker_score": attacker_score,
            "defender_score": defender_score,
        }
