"""
ICS / power-substation environment modeled on the Purdue reference architecture.

This is a *simulation* only. Nodes, services, vulnerabilities and exploits are
abstract models with probabilities and difficulty scores. No real hosts are
scanned and no real exploit code exists anywhere in this project. The purpose
is agent-based modeling of attacker/defender swarm dynamics on a critical asset.

Purdue levels used here:
  L5/L4  Enterprise IT        (corporate workstations, email, internet edge)
  L3.5   Industrial DMZ       (jump host, patch server, remote access)
  L3     Operations           (historian, SCADA app server, domain controller)
  L2     Supervisory control  (HMI, engineering workstation)
  L1     Basic control        (PLC / RTU / protection relay)
  L0     Physical process     (breaker, transformer -- the critical asset)
"""
from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from typing import Optional


# Kill-chain stages an attacker progresses a node/campaign through.
STAGES = [
    "recon",
    "initial_access",
    "execution",
    "privilege_escalation",
    "lateral_movement",
    "persistence",
    "impact",
]


@dataclass
class Vulnerability:
    """An abstract, modeled weakness on a service. Not a real CVE."""
    name: str
    difficulty: float          # 0..1  higher = harder to exploit
    noise: float               # 0..1  how much exploiting it raises detection
    patched: bool = False


@dataclass
class Node:
    id: str
    name: str
    level: float               # Purdue level (5,4,3.5,3,2,1,0)
    zone: str
    criticality: float         # 0..1 contribution to overall asset health
    vulns: list[Vulnerability] = field(default_factory=list)

    # Attacker-visible / internal state ----------------------------------
    discovered: bool = False          # attacker has seen this node exists
    enumerated: bool = False          # attacker has mapped services/vulns
    compromised: bool = False         # attacker has a foothold
    privilege: str = "none"           # none | user | root
    persistence: bool = False         # survives eviction attempts
    impacted: bool = False            # process manipulation applied

    # Defender-visible / process state -----------------------------------
    detection: float = 0.0            # 0..1 accumulated suspicious signal
    flagged: bool = False             # defender has confirmed compromise
    isolated: bool = False            # defender cut this node off the network
    isolation_timer: int = 0          # ticks remaining before return to service
    hardened: float = 0.0             # 0..1 extra exploit resistance from patching
    integrity: float = 1.0            # 1.0 healthy; <1 means process degraded

    # Layout hint for the dashboard (filled in by the scenario builder)
    x: float = 0.0
    y: float = 0.0

    def open_vulns(self) -> list[Vulnerability]:
        return [v for v in self.vulns if not v.patched]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "level": self.level,
            "zone": self.zone,
            "criticality": round(self.criticality, 3),
            "discovered": self.discovered,
            "enumerated": self.enumerated,
            "compromised": self.compromised,
            "privilege": self.privilege,
            "persistence": self.persistence,
            "impacted": self.impacted,
            "detection": round(self.detection, 3),
            "flagged": self.flagged,
            "isolated": self.isolated,
            "isolation_timer": self.isolation_timer,
            "hardened": round(self.hardened, 3),
            "integrity": round(self.integrity, 3),
            "vulns": [
                {"name": v.name, "difficulty": v.difficulty, "patched": v.patched}
                for v in self.vulns
            ],
            "x": self.x,
            "y": self.y,
        }


@dataclass
class Edge:
    src: str
    dst: str
    # Segmentation strength of the firewall between the two zones. Higher means
    # the attacker is less likely to even reach/exploit across this boundary and
    # more likely to be noticed doing so.
    segmentation: float = 0.0

    def key(self) -> tuple[str, str]:
        return (self.src, self.dst)


class Environment:
    """Holds the substation topology and the primitive state transitions.

    Agents never mutate Environment directly; they call these methods so every
    change is centralized, bounded, and loggable.
    """

    def __init__(self, seed: int = 0):
        self.rng = random.Random(seed)
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self._adj: dict[str, set[str]] = {}
        self.tick = 0

    # -- topology construction ------------------------------------------
    def add_node(self, node: Node) -> Node:
        self.nodes[node.id] = node
        self._adj.setdefault(node.id, set())
        return node

    def add_edge(self, src: str, dst: str, segmentation: float = 0.0) -> None:
        self.edges.append(Edge(src, dst, segmentation))
        self._adj[src].add(dst)
        self._adj[dst].add(src)

    def neighbors(self, node_id: str) -> list[str]:
        return list(self._adj.get(node_id, set()))

    def segmentation_between(self, a: str, b: str) -> float:
        for e in self.edges:
            if {e.src, e.dst} == {a, b}:
                return e.segmentation
        return 0.5

    # -- attacker-facing primitives -------------------------------------
    def reachable_frontier(self) -> list[str]:
        """Nodes the attacker can currently act against: neighbors of any
        compromised (non-isolated) node, plus already-compromised nodes."""
        frontier: set[str] = set()
        for nid, node in self.nodes.items():
            if node.compromised and not node.isolated:
                frontier.add(nid)
                for nb in self.neighbors(nid):
                    nbn = self.nodes[nb]
                    if not nbn.isolated:
                        frontier.add(nb)
        return list(frontier)

    def entry_nodes(self) -> list[str]:
        """Internet-exposed enterprise nodes the attacker can touch at t=0."""
        return [n.id for n in self.nodes.values() if n.zone == "enterprise"]

    # -- scoring helpers ------------------------------------------------
    def asset_health(self) -> float:
        """Criticality-weighted integrity of the whole substation (0..1)."""
        total_w = sum(n.criticality for n in self.nodes.values()) or 1.0
        return sum(n.integrity * n.criticality for n in self.nodes.values()) / total_w

    def availability(self) -> float:
        """Criticality-weighted fraction of the asset currently in service.

        Isolating nodes to contain an attack removes them from service, so
        over-aggressive containment shows up here as lost availability -- the
        operational cost the defender must trade against security.
        """
        total_w = sum(n.criticality for n in self.nodes.values()) or 1.0
        in_service = sum(n.criticality for n in self.nodes.values()
                         if not n.isolated)
        return in_service / total_w

    def compromised_nodes(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.compromised]

    def process_nodes(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.level <= 1]

    def snapshot(self) -> dict:
        return {
            "tick": self.tick,
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": [
                {"src": e.src, "dst": e.dst, "segmentation": e.segmentation}
                for e in self.edges
            ],
            "asset_health": round(self.asset_health(), 4),
            "availability": round(self.availability(), 4),
        }


# ---------------------------------------------------------------------------
# Scenario builder: a representative distribution substation.
# ---------------------------------------------------------------------------
def build_substation(seed: int = 0, size: str = "medium") -> Environment:
    """Construct the Purdue-model substation topology.

    size: 'small' | 'medium' | 'large' scales how many devices sit at each
    level, which in turn controls how big the swarms need to be.
    """
    env = Environment(seed=seed)
    rng = env.rng

    counts = {
        "small": {"ent": 3, "hmi": 1, "ews": 1, "plc": 3},
        "medium": {"ent": 5, "hmi": 2, "ews": 2, "plc": 6},
        "large": {"ent": 9, "hmi": 3, "ews": 3, "plc": 12},
    }[size]

    # Vulnerability templates per device class (modeled difficulty/noise).
    def vulns_for(klass: str) -> list[Vulnerability]:
        catalog = {
            "enterprise": [("phishing_foothold", 0.25, 0.3),
                           ("unpatched_browser", 0.4, 0.35)],
            "dmz": [("exposed_rdp", 0.45, 0.45), ("weak_jump_creds", 0.4, 0.4)],
            "ops": [("historian_sqli", 0.55, 0.5), ("default_svc_account", 0.5, 0.45)],
            "supervisory": [("hmi_web_cve", 0.6, 0.55), ("eng_ws_macro", 0.5, 0.5)],
            "control": [("plc_no_auth", 0.62, 0.6), ("insecure_protocol", 0.6, 0.55)],
            "process": [("relay_logic_tamper", 0.75, 0.75)],
        }
        return [Vulnerability(n, d, no) for (n, d, no) in catalog.get(klass, [])]

    # column x per level for the dashboard layout
    level_x = {5: 0, 4: 0, 3.5: 1, 3: 2, 2: 3, 1: 4, 0: 5}

    def place(node: Node, idx: int, total: int) -> Node:
        node.x = level_x[node.level]
        node.y = (idx - (total - 1) / 2.0)
        return node

    ids = itertools.count()

    # L5/L4 enterprise
    ent_nodes = []
    for i in range(counts["ent"]):
        n = env.add_node(place(Node(
            id=f"ent{i}", name=f"Corp-WS-{i}", level=4, zone="enterprise",
            criticality=0.02, vulns=vulns_for("enterprise")), i, counts["ent"]))
        ent_nodes.append(n)
    mail = env.add_node(place(Node(id="mail", name="Mail/Edge GW", level=5,
            zone="enterprise", criticality=0.03, vulns=vulns_for("enterprise")),
            counts["ent"], counts["ent"] + 1))
    ent_nodes.append(mail)

    # L3.5 DMZ
    jump = env.add_node(place(Node(id="jump", name="Jump Host", level=3.5,
            zone="dmz", criticality=0.05, vulns=vulns_for("dmz")), 0, 2))
    patch = env.add_node(place(Node(id="patch", name="Patch/Update Srv", level=3.5,
            zone="dmz", criticality=0.05, vulns=vulns_for("dmz")), 1, 2))

    # L3 operations
    hist = env.add_node(place(Node(id="hist", name="Historian", level=3,
            zone="ops", criticality=0.08, vulns=vulns_for("ops")), 0, 3))
    scada = env.add_node(place(Node(id="scada", name="SCADA App Srv", level=3,
            zone="ops", criticality=0.12, vulns=vulns_for("ops")), 1, 3))
    dc = env.add_node(place(Node(id="dc", name="Domain Controller", level=3,
            zone="ops", criticality=0.10, vulns=vulns_for("ops")), 2, 3))

    # L2 supervisory
    hmis = []
    for i in range(counts["hmi"]):
        n = env.add_node(place(Node(id=f"hmi{i}", name=f"HMI-{i}", level=2,
            zone="supervisory", criticality=0.12, vulns=vulns_for("supervisory")),
            i, counts["hmi"] + counts["ews"]))
        hmis.append(n)
    ews = []
    for i in range(counts["ews"]):
        n = env.add_node(place(Node(id=f"ews{i}", name=f"Eng-WS-{i}", level=2,
            zone="supervisory", criticality=0.10, vulns=vulns_for("supervisory")),
            counts["hmi"] + i, counts["hmi"] + counts["ews"]))
        ews.append(n)

    # L1 control (PLC/RTU/relay)
    plcs = []
    for i in range(counts["plc"]):
        n = env.add_node(place(Node(id=f"plc{i}", name=f"PLC/Relay-{i}", level=1,
            zone="control", criticality=0.35, vulns=vulns_for("control")),
            i, counts["plc"]))
        plcs.append(n)

    # L0 physical process (the crown jewels)
    breaker = env.add_node(place(Node(id="breaker", name="Feeder Breaker", level=0,
            zone="process", criticality=0.9, vulns=vulns_for("process")), 0, 2))
    xfmr = env.add_node(place(Node(id="xfmr", name="Power Transformer", level=0,
            zone="process", criticality=1.0, vulns=vulns_for("process")), 1, 2))

    # -- segmentation-respecting edges ----------------------------------
    # Enterprise mesh
    for n in ent_nodes:
        env.add_edge(mail.id, n.id, segmentation=0.1)
    for a, b in zip(ent_nodes, ent_nodes[1:]):
        env.add_edge(a.id, b.id, segmentation=0.1)

    # Enterprise -> DMZ (firewall boundary)
    env.add_edge(mail.id, jump.id, segmentation=0.6)
    env.add_edge(ent_nodes[0].id, jump.id, segmentation=0.6)
    env.add_edge(jump.id, patch.id, segmentation=0.3)

    # DMZ -> Operations (industrial firewall)
    for opsn in (hist, scada, dc):
        env.add_edge(jump.id, opsn.id, segmentation=0.65)
    env.add_edge(patch.id, dc.id, segmentation=0.6)
    env.add_edge(hist.id, scada.id, segmentation=0.2)
    env.add_edge(dc.id, scada.id, segmentation=0.2)

    # Operations -> Supervisory
    for h in hmis + ews:
        env.add_edge(scada.id, h.id, segmentation=0.5)
        env.add_edge(dc.id, h.id, segmentation=0.5)
    env.add_edge(hist.id, hmis[0].id, segmentation=0.5)

    # Supervisory -> Control (PLCs)
    for i, p in enumerate(plcs):
        src = hmis[i % len(hmis)]
        env.add_edge(src.id, p.id, segmentation=0.55)
        env.add_edge(ews[i % len(ews)].id, p.id, segmentation=0.5)

    # Control -> Physical process
    half = max(1, len(plcs) // 2)
    for p in plcs[:half]:
        env.add_edge(p.id, breaker.id, segmentation=0.7)
    for p in plcs[half:]:
        env.add_edge(p.id, xfmr.id, segmentation=0.7)

    return env
