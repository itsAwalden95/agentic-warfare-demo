"""
Commander layer -- the "agentic" brain for each swarm.

Every REFRESH ticks, each side's commander looks at a compact summary of the
battle and issues a strategy Directive that biases how its swarm picks actions.
This is where IBM watsonx.ai plugs in: when credentials are present the
commander asks an LLM to reason about the situation and emit a JSON directive;
otherwise it uses a transparent heuristic strategist so the demo always runs.

Keeping the LLM at the *commander* level (a handful of calls per simulation)
rather than per-agent-per-tick is deliberate: it is the design that stays fast,
cheap and reliable for a live demo while still showcasing LLM-driven autonomy.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from .agents import Directive
from .environment import Environment
from .watsonx_client import WatsonxClient


ATTACKER_SYS = """You are the ATTACKER commander in a simulated ICS/power-substation \
cyber range (agent-based modeling, no real systems). You direct an autonomous \
offensive swarm trying to degrade the physical process (breakers/transformer) at \
Purdue Level 0. Choose a strategy that balances speed against stealth. Respond \
with ONLY a JSON object:
{"focus_zone": one of [enterprise,dmz,ops,supervisory,control,process],
 "focus_node": node id or null,
 "risk_tolerance": 0.0-1.0,
 "posture": short label,
 "rationale": one sentence}"""

DEFENDER_SYS = """You are the DEFENDER (blue team) commander in a simulated \
ICS/power-substation cyber range (agent-based modeling, no real systems). You \
direct an autonomous defensive swarm protecting the physical process. Balance \
aggressive containment against availability of critical control systems. Respond \
with ONLY a JSON object:
{"focus_zone": one of [enterprise,dmz,ops,supervisory,control,process],
 "focus_node": node id or null,
 "risk_tolerance": 0.0-1.0,
 "posture": short label,
 "rationale": one sentence}"""


def _battle_summary(env: Environment, side: str) -> str:
    comp = env.compromised_nodes()
    deepest = min((n.level for n in comp), default=5)
    by_zone: dict[str, int] = {}
    for n in comp:
        by_zone[n.zone] = by_zone.get(n.zone, 0) + 1
    frontier = env.reachable_frontier()
    proc = env.process_nodes()
    lines = [
        f"tick={env.tick}",
        f"asset_health={env.asset_health():.2f}",
        f"compromised={len(comp)} deepest_level={deepest}",
        f"compromised_by_zone={by_zone}",
        f"flagged={sum(1 for n in env.nodes.values() if n.flagged)}",
        f"isolated={sum(1 for n in env.nodes.values() if n.isolated)}",
        f"frontier_size={len(frontier)}",
        "process_integrity=" + ",".join(
            f"{n.id}:{n.integrity:.2f}" for n in proc),
        "reachable_frontier_nodes=" + ",".join(
            f"{env.nodes[n].id}({env.nodes[n].zone},L{env.nodes[n].level})"
            for n in frontier[:12]),
    ]
    return "\n".join(lines)


class Commander:
    def __init__(self, side: str, wx: Optional[WatsonxClient] = None):
        assert side in ("attacker", "defender")
        self.side = side
        self.wx = wx or WatsonxClient()
        self.last_directive = Directive()
        self.uses_llm = False       # was the last directive LLM-authored?

    def decide(self, env: Environment) -> Directive:
        directive = None
        if self.wx.available:
            directive = self._llm_directive(env)
        if directive is None:
            directive = self._heuristic_directive(env)
            self.uses_llm = False
        else:
            self.uses_llm = True
        self.last_directive = directive
        return directive

    # -- LLM path -------------------------------------------------------
    def _llm_directive(self, env: Environment) -> Optional[Directive]:
        sys = ATTACKER_SYS if self.side == "attacker" else DEFENDER_SYS
        prompt = f"{sys}\n\nBATTLE STATE:\n{_battle_summary(env, self.side)}\n\nJSON:"
        text = self.wx.generate(prompt)
        if not text:
            return None
        try:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            data = json.loads(m.group(0)) if m else json.loads(text)
            fz = data.get("focus_zone")
            fz = fz if fz in {"enterprise", "dmz", "ops", "supervisory",
                              "control", "process"} else None
            fn = data.get("focus_node")
            fn = fn if fn in env.nodes else None
            rt = float(data.get("risk_tolerance", 0.5))
            return Directive(
                focus_zone=fz, focus_node=fn,
                risk_tolerance=max(0.0, min(1.0, rt)),
                posture=str(data.get("posture", "llm"))[:40],
                rationale=str(data.get("rationale", ""))[:200],
            )
        except Exception:
            return None

    # -- Heuristic fallback (also a clear baseline for the client) ------
    def _heuristic_directive(self, env: Environment) -> Directive:
        comp = env.compromised_nodes()
        deepest = min((n.level for n in comp), default=5)
        health = env.asset_health()
        if self.side == "attacker":
            # push toward the deepest zone currently reachable
            zone_order = ["enterprise", "dmz", "ops", "supervisory",
                          "control", "process"]
            owned_zones = {n.zone for n in comp}
            next_zone = "enterprise"
            for i, z in enumerate(zone_order):
                if z in owned_zones and i + 1 < len(zone_order):
                    next_zone = zone_order[i + 1]
            # if we own process footholds, press the attack hard
            proc_owned = any(n.compromised for n in env.process_nodes())
            risk = 0.8 if proc_owned else (0.4 if deepest > 2 else 0.55)
            focus_node = None
            if proc_owned:
                tgt = max((n for n in env.process_nodes() if n.compromised),
                          key=lambda n: n.criticality, default=None)
                focus_node = tgt.id if tgt else None
                next_zone = "process"
            return Directive(
                focus_zone=next_zone, focus_node=focus_node, risk_tolerance=risk,
                posture=("impact-now" if proc_owned else f"press-{next_zone}"),
                rationale=(f"Own depth L{deepest}; drive toward {next_zone}."))
        else:
            # defender: escalate aggressiveness as the asset degrades
            flagged = sum(1 for n in env.nodes.values() if n.flagged)
            deep_threat = deepest <= 2
            risk = 0.85 if (health < 0.8 or deep_threat) else 0.5
            focus_zone = "control" if deep_threat else "ops"
            if health < 0.6:
                focus_zone = "process"
            return Directive(
                focus_zone=focus_zone, focus_node=None, risk_tolerance=risk,
                posture=("containment" if deep_threat else "monitoring"),
                rationale=(f"Health {health:.2f}, {flagged} confirmed; "
                           f"guard {focus_zone}."))
