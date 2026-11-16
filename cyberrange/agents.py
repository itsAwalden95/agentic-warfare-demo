"""
Attacker and defender swarm agents.

Each agent is a lightweight, role-specialized policy. Every tick an agent
selects one action against the modeled environment. Action *selection* is
biased by the strategy directive coming from its side's commander (see
commander.py), which is where the LLM "agentic" layer plugs in. The low-level
effects (probabilities, state transitions) live here so the simulation stays
fast and deterministic given a seed.

Everything is simulated: "exploit" is a probability draw against a modeled
vulnerability difficulty, not real exploitation of a real system.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from .environment import Environment, Node


# An Event is what gets streamed to the dashboard / log.
@dataclass
class Event:
    tick: int
    side: str          # "attacker" | "defender" | "system"
    agent: str
    action: str
    target: Optional[str]
    success: bool
    detail: str
    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Directive:
    """Strategy handed down by a commander. Agents read this to bias choices."""
    focus_zone: Optional[str] = None     # zone to prioritize
    focus_node: Optional[str] = None     # specific node to converge on
    risk_tolerance: float = 0.5          # 0 stealthy .. 1 aggressive
    posture: str = "balanced"            # free-form label for the UI
    rationale: str = ""                  # commander's explanation (LLM text)


# ---------------------------------------------------------------------------
# Attacker swarm
# ---------------------------------------------------------------------------
ATTACKER_ROLES = ["recon", "exploit", "lateral", "persistence", "impact"]


def defensibility(node: Node) -> float:
    """How well the blue team can see into / act on a node.

    IT zones (Purdue L3+) have mature EDR, logging and patch tooling. OT zones
    (L2 and below) are notoriously under-monitored and hard to remediate without
    disrupting the physical process -- so detection and eviction get harder the
    deeper the attacker goes. This asymmetry is the heart of ICS risk.
    """
    if node.level >= 3:
        return 1.0
    return 0.4 + 0.15 * node.level     # L2=0.70  L1=0.55  L0=0.40


class AttackerAgent:
    def __init__(self, agent_id: str, role: str, skill: float = 1.0):
        self.id = agent_id
        self.role = role
        self.skill = skill

    # value heuristic: how much does the attacker "want" this node?
    def _target_value(self, env: Environment, node: Node,
                      directive: Directive) -> float:
        # Deeper (lower Purdue level) is strongly preferred -- this is what
        # drives the swarm downward from enterprise toward the process layer.
        v = (5.0 - node.level) * 0.5 + node.criticality
        if directive.focus_zone and node.zone == directive.focus_zone:
            v += 0.6
        if directive.focus_node and node.id == directive.focus_node:
            v += 1.2
        # prefer quiet targets unless aggressive
        v -= node.detection * (1.0 - directive.risk_tolerance)
        # small jitter so the swarm spreads instead of all piling on one node
        v += env.rng.uniform(-0.15, 0.15)
        return v

    def act(self, env: Environment, directive: Directive,
            emit: Callable[[Event], None], ctx: dict | None = None) -> None:
        rng = env.rng
        claims: set = ctx.setdefault("atk_claims", set()) if ctx is not None else set()
        frontier = env.reachable_frontier()
        if not frontier:
            # cold start: touch an internet-exposed enterprise node
            entries = env.entry_nodes()
            if entries:
                self._exploit(env, env.nodes[rng.choice(entries)], directive, emit)
            return

        nodes = [env.nodes[n] for n in frontier if not env.nodes[n].isolated]
        if not nodes:
            return
        uncompromised = [n for n in nodes if not n.compromised]

        if self.role == "recon":
            # reduce noise on the deepest owned node by quietly mapping ahead;
            # if nothing to map, fall through to exploiting the frontier edge.
            targets = [n for n in uncompromised if not n.enumerated]
            if targets:
                t = max(targets, key=lambda n: self._target_value(env, n, directive))
                self._enumerate(env, t, emit)
                return

        if self.role == "impact":
            t = self._best_impact_source(env)
            if t is not None:
                self._impact(env, t, directive, emit)
                return

        if self.role == "persistence":
            # lock in the DEEPEST footholds first -- a persistent beachhead in
            # supervisory/control is what lets the swarm hold the OT pipeline.
            footholds = [n for n in nodes if n.compromised and not n.persistence]
            if footholds:
                t = min(footholds, key=lambda n: (n.level, -n.criticality))
                self._persist(env, t, emit)
                return

        # Core driver: push the frontier deeper. Prefer the most valuable
        # reachable node we do NOT yet own; only fall back to escalation when
        # the whole reachable frontier is already owned. Coordinate via the
        # shared blackboard so the swarm spreads across targets instead of all
        # piling onto a single node (unclaimed nodes preferred).
        if uncompromised:
            unclaimed = [n for n in uncompromised if n.id not in claims]
            pool = unclaimed or uncompromised
            target = max(pool,
                         key=lambda n: self._target_value(env, n, directive))
            claims.add(target.id)
            self._exploit(env, target, directive, emit)
            return

        not_root = [n for n in nodes if n.privilege != "root"]
        if not_root:
            self._escalate(env, max(
                not_root, key=lambda n: self._target_value(env, n, directive)), emit)
            return

        # entire frontier owned at root: if we hold control/process, hit it
        t = self._best_impact_source(env)
        if t is not None:
            self._impact(env, t, directive, emit)

    def _physical_targets(self, env: Environment, node: Node) -> list[Node]:
        """Process nodes a held control node can manipulate: the downstream L0
        physical devices it commands (breaker/transformer), plus the control
        node itself -- disrupting a PLC's logic degrades the process it governs."""
        tgts = [env.nodes[nb] for nb in env.neighbors(node.id)
                if env.nodes[nb].level == 0 and env.nodes[nb].integrity > 0.05]
        if node.level <= 1 and node.integrity > 0.05:
            tgts.append(node)
        return tgts

    def _best_impact_source(self, env: Environment) -> Optional[Node]:
        """A compromised, in-service control/process node (L<=1) from which we
        can still degrade the physical process."""
        owned = [n for n in env.nodes.values()
                 if n.compromised and not n.isolated and n.level <= 1]
        actionable = [n for n in owned if self._physical_targets(env, n)]
        if not actionable:
            return None
        # prefer the source whose weakest physical target is closest to failure
        return min(actionable, key=lambda n: min(
            t.integrity for t in self._physical_targets(env, n)))

    # -- primitive actions ----------------------------------------------
    def _enumerate(self, env, node: Node, emit):
        node.discovered = True
        node.enumerated = True
        node.detection = min(1.0, node.detection + 0.03)
        for nb in env.neighbors(node.id):
            env.nodes[nb].discovered = True
        emit(Event(env.tick, "attacker", self.id, "recon", node.id, True,
                   f"enumerated {node.name} services/vulns"))

    def _exploit(self, env, node: Node, directive: Directive, emit):
        node.discovered = True
        node.enumerated = True
        open_v = node.open_vulns()
        if not open_v:
            node.detection = min(1.0, node.detection + 0.05)
            emit(Event(env.tick, "attacker", self.id, "exploit", node.id, False,
                       f"no open vulns on {node.name} (hardened)"))
            return
        vuln = min(open_v, key=lambda v: v.difficulty)
        seg = 0.0
        for nb in env.neighbors(node.id):
            if env.nodes[nb].compromised:
                seg = max(seg, env.segmentation_between(node.id, nb))
        # success probability model
        base = 1.0 - vuln.difficulty
        prob = base * (1.0 - node.hardened) * (1.0 - 0.4 * seg)
        prob *= (0.7 + 0.5 * directive.risk_tolerance) * self.skill
        prob = max(0.05, min(0.95, prob))
        noise = vuln.noise * (0.7 + 0.6 * directive.risk_tolerance) + seg * 0.2
        node.detection = min(1.0, node.detection + noise)
        if env.rng.random() < prob:
            node.compromised = True
            node.privilege = "user"
            emit(Event(env.tick, "attacker", self.id, "exploit", node.id, True,
                       f"compromised {node.name} via {vuln.name}"))
        else:
            emit(Event(env.tick, "attacker", self.id, "exploit", node.id, False,
                       f"failed {vuln.name} on {node.name}"))

    def _escalate(self, env, node: Node, emit):
        if node.privilege == "root":
            return
        node.detection = min(1.0, node.detection + 0.06)
        if env.rng.random() < 0.6:
            node.privilege = "root"
            emit(Event(env.tick, "attacker", self.id, "privilege_escalation",
                       node.id, True, f"gained root on {node.name}"))
        else:
            emit(Event(env.tick, "attacker", self.id, "privilege_escalation",
                       node.id, False, f"priv-esc failed on {node.name}"))

    def _persist(self, env, node: Node, emit):
        node.detection = min(1.0, node.detection + 0.04)
        if env.rng.random() < 0.75:
            node.persistence = True
            emit(Event(env.tick, "attacker", self.id, "persistence", node.id, True,
                       f"installed persistence on {node.name}"))

    def _impact(self, env, node: Node, directive: Directive, emit):
        """Manipulate the physical process via a held control node. Owning the
        PLC that controls a breaker is enough to trip/damage that breaker --
        the physical L0 device is not separately 'hacked'."""
        if node.privilege != "root":
            self._escalate(env, node, emit)
            return
        targets = self._physical_targets(env, node)
        if not targets:
            return
        tgt = min(targets, key=lambda n: n.integrity)
        node.detection = min(1.0, node.detection + 0.12)
        dmg = 0.18 + 0.22 * directive.risk_tolerance
        tgt.integrity = max(0.0, tgt.integrity - dmg)
        tgt.impacted = True
        via = "" if tgt.id == node.id else f" via {node.name}"
        emit(Event(env.tick, "attacker", self.id, "impact", tgt.id, True,
                   f"manipulated process on {tgt.name}{via} "
                   f"(integrity -> {tgt.integrity:.2f})"))


# ---------------------------------------------------------------------------
# Defender swarm
# ---------------------------------------------------------------------------
DEFENDER_ROLES = ["monitor", "hunt", "respond", "harden", "restore"]


class DefenderAgent:
    def __init__(self, agent_id: str, role: str, skill: float = 1.0):
        self.id = agent_id
        self.role = role
        self.skill = skill

    def act(self, env: Environment, directive: Directive,
            emit: Callable[[Event], None], ctx: dict | None = None) -> None:
        rng = env.rng
        claims: set = ctx.setdefault("def_claims", set()) if ctx is not None else set()
        nodes = list(env.nodes.values())

        if self.role == "monitor":
            # raise alerts on noisy nodes; sensitivity scales with risk posture
            noisy = [n for n in nodes if n.detection > 0.2 and not n.flagged
                     and n.id not in claims]
            if noisy:
                t = max(noisy, key=lambda n: n.detection * defensibility(n))
                claims.add(t.id)
                sens = 0.5 + 0.5 * directive.risk_tolerance
                if rng.random() < t.detection * sens * defensibility(t) * self.skill:
                    t.flagged = True
                    emit(Event(env.tick, "defender", self.id, "detect", t.id, True,
                               f"ALERT: confirmed compromise on {t.name}"))
                return

        if self.role == "hunt":
            # proactively inspect high-value, high-detection compromised nodes
            suspects = [n for n in nodes
                        if n.compromised and not n.flagged and n.detection > 0.1
                        and n.id not in claims]
            if suspects:
                t = max(suspects, key=lambda n: n.detection * n.criticality)
                claims.add(t.id)
                if rng.random() < (0.4 + 0.3 * directive.risk_tolerance) * defensibility(t) * self.skill:
                    t.flagged = True
                    emit(Event(env.tick, "defender", self.id, "hunt", t.id, True,
                               f"threat hunt uncovered foothold on {t.name}"))
                return

        if self.role == "respond":
            # each responder takes a DISTINCT flagged node (no dogpiling)
            flagged = [n for n in nodes if n.flagged and n.compromised
                       and not n.isolated and n.id not in claims]
            if flagged:
                t = max(flagged, key=lambda n: n.criticality)
                claims.add(t.id)
                # try to evict; persistence AND low OT defensibility make it harder
                evict_p = (0.55 if not t.persistence else 0.2) * defensibility(t) * self.skill
                if rng.random() < evict_p:
                    t.compromised = False
                    t.privilege = "none"
                    t.persistence = False
                    t.detection = max(0.0, t.detection - 0.5)
                    t.flagged = False
                    t.isolated = False
                    t.isolation_timer = 0
                    emit(Event(env.tick, "defender", self.id, "evict", t.id, True,
                               f"evicted attacker from {t.name}"))
                elif not t.isolated:
                    # fall back to TEMPORARY isolation to contain spread. This
                    # costs availability and expires, so it buys time rather
                    # than permanently removing the foothold.
                    t.isolated = True
                    t.isolation_timer = 6
                    emit(Event(env.tick, "defender", self.id, "isolate", t.id, True,
                               f"isolated {t.name} ~6 ticks to contain spread"))
                return

        if self.role == "harden":
            # patch the most exposed unpatched node near the frontier
            frontier = set(env.reachable_frontier())
            targets = [n for n in nodes if n.open_vulns()
                       and (n.id in frontier or n.criticality > 0.3)]
            if targets:
                t = max(targets, key=lambda n: n.criticality + 0.2 * len(n.open_vulns()))
                for v in t.open_vulns():
                    if rng.random() < 0.5:
                        v.patched = True
                t.hardened = min(1.0, t.hardened + 0.2)
                emit(Event(env.tick, "defender", self.id, "harden", t.id, True,
                           f"patched/hardened {t.name}"))
                return

        if self.role == "restore":
            damaged = [n for n in nodes if n.integrity < 0.99 and not n.compromised]
            if damaged:
                t = min(damaged, key=lambda n: n.integrity)
                t.integrity = min(1.0, t.integrity + 0.15)
                if t.integrity > 0.98:
                    t.impacted = False
                emit(Event(env.tick, "defender", self.id, "restore", t.id, True,
                           f"restored process integrity on {t.name} "
                           f"(-> {t.integrity:.2f})"))
                return

        # idle fallback: slow passive detection decay handled by engine
        return


def build_attacker_swarm(size: int, skill: float = 1.0) -> list[AttackerAgent]:
    roles = ATTACKER_ROLES
    return [AttackerAgent(f"atk-{i}", roles[i % len(roles)], skill)
            for i in range(size)]


def build_defender_swarm(size: int, skill: float = 1.0) -> list[DefenderAgent]:
    roles = DEFENDER_ROLES
    return [DefenderAgent(f"def-{i}", roles[i % len(roles)], skill)
            for i in range(size)]
