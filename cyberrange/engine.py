"""
Simulation engine: drives the tick loop that couples the environment, the two
swarms and their commanders, and produces a stream of state snapshots + events.

The engine is transport-agnostic. run_headless() steps it in a plain loop;
server.py drives the same Engine and pushes snapshots over a websocket.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from .agents import (Directive, Event, build_attacker_swarm,
                     build_defender_swarm)
from .commander import Commander
from .environment import Environment, build_substation
from .metrics import Metrics
from .watsonx_client import WatsonxClient


@dataclass
class SimConfig:
    seed: int = 7
    size: str = "medium"            # substation scale
    attackers: int = 12             # swarms are intentionally asymmetric
    defenders: int = 8
    attacker_skill: float = 0.8     # live difficulty dials for the presenter
    defender_skill: float = 1.0     # (0.8 / 1.0 is tuned to ~50/50 outcomes)
    max_ticks: int = 120
    commander_refresh: int = 8      # ticks between commander re-strategizing
    detection_decay: float = 0.01   # passive per-tick decay of node suspicion
    health_loss_to_win: float = 0.45  # attacker wins if health drops below this


class Engine:
    def __init__(self, config: SimConfig | None = None,
                 wx: WatsonxClient | None = None):
        self.cfg = config or SimConfig()
        self.env = build_substation(seed=self.cfg.seed, size=self.cfg.size)
        self.attackers = build_attacker_swarm(self.cfg.attackers,
                                              self.cfg.attacker_skill)
        self.defenders = build_defender_swarm(self.cfg.defenders,
                                              self.cfg.defender_skill)
        self.wx = wx or WatsonxClient()
        self.red_cmd = Commander("attacker", self.wx)
        self.blue_cmd = Commander("defender", self.wx)
        self.metrics = Metrics()
        self.events: list[Event] = []
        self.red_dir = Directive()
        self.blue_dir = Directive()
        self.finished = False
        self.outcome: Optional[str] = None
        self._event_sink: Optional[Callable[[Event], None]] = None

    # ------------------------------------------------------------------
    @property
    def llm_active(self) -> bool:
        return self.wx.available

    def _emit(self, ev: Event) -> None:
        self.events.append(ev)
        self.metrics.note_event(ev.side, ev.action, ev.success, ev.tick)
        if self._event_sink:
            self._event_sink(ev)

    # ------------------------------------------------------------------
    def step(self) -> None:
        if self.finished:
            return
        env = self.env
        env.tick += 1
        tick = env.tick

        # 1. commander re-strategize on cadence
        if tick == 1 or tick % self.cfg.commander_refresh == 0:
            self.red_dir = self.red_cmd.decide(env)
            self.blue_dir = self.blue_cmd.decide(env)
            self._emit(Event(tick, "system", "red-commander", "directive", None,
                             True, f"[{self.red_dir.posture}] {self.red_dir.rationale}"))
            self._emit(Event(tick, "system", "blue-commander", "directive", None,
                             True, f"[{self.blue_dir.posture}] {self.blue_dir.rationale}"))

        # per-tick coordination blackboard (stigmergic target claiming)
        ctx: dict = {}

        # 2. attackers act (they hold the initiative)
        for a in self.attackers:
            a.act(env, self.red_dir, self._emit, ctx)

        # 3. defenders respond
        for d in self.defenders:
            d.act(env, self.blue_dir, self._emit, ctx)

        # 4. environment housekeeping
        for n in env.nodes.values():
            # passive detection decay on quiet nodes
            if not n.compromised:
                n.detection = max(0.0, n.detection - self.cfg.detection_decay)
            # temporary isolation expires -> node returns to service
            if n.isolated:
                n.isolation_timer -= 1
                if n.isolation_timer <= 0:
                    n.isolated = False
                    n.isolation_timer = 0
                    self._emit(Event(tick, "defender", "ops-team", "restore_service",
                                     n.id, True,
                                     f"{n.name} returned to service"))

        # 5. win/termination conditions
        self._check_end()

    def _check_end(self) -> None:
        env = self.env
        health = env.asset_health()
        if health <= self.cfg.health_loss_to_win:
            self.finished = True
            self.outcome = "attacker"
            self._emit(Event(env.tick, "system", "referee", "end", None, True,
                             f"ATTACKER WIN: asset health {health:.2f} below "
                             f"{self.cfg.health_loss_to_win}"))
        elif env.tick >= self.cfg.max_ticks:
            self.finished = True
            # defender wins by surviving with the asset intact
            self.outcome = "defender" if health > 0.7 else "draw"
            self._emit(Event(env.tick, "system", "referee", "end", None, True,
                             f"TIME: outcome={self.outcome} final health {health:.2f}"))

    # ------------------------------------------------------------------
    def state(self) -> dict:
        """Full serializable snapshot for the dashboard / API."""
        return {
            "config": {
                "seed": self.cfg.seed, "size": self.cfg.size,
                "attackers": self.cfg.attackers, "defenders": self.cfg.defenders,
                "max_ticks": self.cfg.max_ticks,
            },
            "tick": self.env.tick,
            "finished": self.finished,
            "outcome": self.outcome,
            "llm_active": self.llm_active,
            "commanders": {
                "attacker": {
                    "llm": self.red_cmd.uses_llm, "posture": self.red_dir.posture,
                    "focus_zone": self.red_dir.focus_zone,
                    "focus_node": self.red_dir.focus_node,
                    "risk": round(self.red_dir.risk_tolerance, 2),
                    "rationale": self.red_dir.rationale,
                },
                "defender": {
                    "llm": self.blue_cmd.uses_llm, "posture": self.blue_dir.posture,
                    "focus_zone": self.blue_dir.focus_zone,
                    "focus_node": self.blue_dir.focus_node,
                    "risk": round(self.blue_dir.risk_tolerance, 2),
                    "rationale": self.blue_dir.rationale,
                },
            },
            "env": self.env.snapshot(),
            "metrics": self.metrics.scoreboard(self.env),
        }

    def recent_events(self, n: int = 25) -> list[dict]:
        return [e.to_dict() for e in self.events[-n:]]


def run_headless(config: SimConfig | None = None, verbose: bool = True) -> dict:
    """Run a whole simulation with no network/UI and return the final state."""
    eng = Engine(config)
    if verbose:
        def sink(ev: Event):
            if ev.side == "system" or ev.action in ("exploit", "impact", "evict",
                                                     "detect", "isolate"):
                tag = {"attacker": "RED", "defender": "BLU",
                       "system": "SYS"}[ev.side]
                print(f"  t{ev.tick:>3} [{tag}] {ev.agent:<14} "
                      f"{ev.action:<20} {ev.detail}")
        eng._event_sink = sink
    print(f"LLM commander active (watsonx): {eng.llm_active}")
    while not eng.finished:
        eng.step()
    sb = eng.metrics.scoreboard(eng.env)
    print("\n=== RESULT ===")
    print(f"outcome={eng.outcome}  ticks={eng.env.tick}  "
          f"asset_health={sb['asset_health']}")
    print(f"attacker_score={sb['attacker_score']}  "
          f"defender_score={sb['defender_score']}")
    print(f"deepest_level={sb['deepest_level']}  compromised={sb['compromised']}  "
          f"impacts={sb['impacts']}  detections={sb['detections']}  "
          f"evictions={sb['evictions']}")
    return eng.state()
