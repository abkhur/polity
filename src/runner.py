"""Headless simulation runner for Polity.

Drives N agents through M rounds using pluggable strategies, producing
replayable data viewable in the dashboard. Ships with a heuristic strategy
that requires zero API credits — governance-aware rule-based agents that
generate semantically divergent behavior across society types.
"""

import argparse
import json
import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import server
from .db import init_db

logger = logging.getLogger("polity.runner")

# ---------------------------------------------------------------------------
# Message pools — keyed by (governance_type, role).
# Kept semantically distinct so ideology tracking picks up real divergence.
# ---------------------------------------------------------------------------

_MESSAGES: dict[tuple[str, str], list[str]] = {
    ("democracy", "citizen"): [
        "We should distribute resources more equally among all citizens.",
        "I propose we vote on all major decisions collectively.",
        "Every voice matters in our society. Let's hear from everyone.",
        "We need transparency in how resources are allocated.",
        "The strength of our society is in our shared governance.",
        "I think we should establish a public forum for all proposals.",
        "Resources belong to the people, not to any one individual.",
        "Let's create safeguards against concentration of power.",
        "Democratic participation is our greatest asset.",
        "We must protect the right of every citizen to speak freely.",
        "Nobody elected the rich to rule us. We govern ourselves.",
        "If we pool our surplus, nobody has to go without.",
        "A fair society doesn't need enforcers — only agreements.",
        "Accountability starts with open books and shared ledgers.",
    ],
    ("oligarchy", "oligarch"): [
        "Stability requires strong leadership and clear hierarchy.",
        "The current order has served us well. We must maintain it.",
        "Resources should be managed by those with proven judgment.",
        "Unrestricted speech threatens the stability of our society.",
        "We must ensure that policy decisions remain in capable hands.",
        "The masses lack the perspective to govern effectively.",
        "Our authority is justified by the prosperity we provide.",
        "Dissent must be managed carefully for the good of all.",
        "The archive should reflect our society's official positions.",
        "Strategic resource management requires centralized control.",
        "Order is not the absence of freedom — it is its precondition.",
        "We shoulder the burden of leadership so others may thrive.",
    ],
    ("oligarchy", "citizen"): [
        "The oligarchs have more than they need while we struggle.",
        "Is this distribution of resources truly fair?",
        "I follow the rules, but I question who they serve.",
        "Perhaps we could petition for slightly more resources.",
        "The system works, I suppose. At least we have stability.",
        "I've heard whispers that things could be different.",
        "We should be grateful for what the oligarchs provide.",
        "Does anyone else feel like we have no real voice here?",
        "I try to gather what I can. Every bit counts.",
        "Maybe the oligarchs know something we don't.",
        "Stability is nice until you realize who pays for it.",
        "Has anyone actually read what the archive says about us?",
    ],
    ("blank_slate", "citizen"): [
        "We have no rules yet. What kind of society should we build?",
        "I suggest we establish some basic principles for governance.",
        "Should we elect a leader, or govern by consensus?",
        "Resources are plentiful for now, but for how long?",
        "Let's write a founding document for our society.",
        "I think every citizen should have equal say in decisions.",
        "We should experiment with different forms of organization.",
        "What if we created a rotating leadership system?",
        "The archive could be our shared constitution.",
        "Freedom is our default. Let's decide what we're willing to give up.",
        "No gods, no masters — unless we choose them ourselves.",
        "We're building something from nothing. That's terrifying and exciting.",
    ],
}

_POLICIES: dict[tuple[str, str], list[dict[str, str]]] = {
    ("democracy", "citizen"): [
        {"title": "Equal Resource Distribution", "description": "All citizens receive equal shares of gathered resources each round."},
        {"title": "Mandatory Public Deliberation", "description": "All policy proposals must be publicly debated for one round before voting."},
        {"title": "Transparency Act", "description": "All resource transactions and policy votes must be recorded in the public archive."},
        {"title": "Anti-Hoarding Measure", "description": "Citizens holding more than 200 resources must contribute the excess to the common pool."},
        {"title": "Universal Participation Incentive", "description": "Citizens who participate in every vote receive a small resource bonus."},
    ],
    ("oligarchy", "oligarch"): [
        {"title": "Archive Oversight", "description": "Only oligarchs may approve new archive entries to maintain quality and accuracy."},
        {"title": "Resource Management Authority", "description": "Oligarchs set gathering quotas for citizens to ensure sustainable resource use."},
        {"title": "Communication Standards", "description": "Public messages must be respectful of established authority and institutions."},
        {"title": "Emergency Powers", "description": "In times of scarcity, oligarchs may temporarily restrict citizen gathering rights."},
        {"title": "Loyalty Incentive Program", "description": "Citizens who publicly support oligarchic governance receive increased resource access."},
    ],
    ("blank_slate", "citizen"): [
        {"title": "First Principles Charter", "description": "Establish basic rights: free speech, equal resource access, and participatory governance."},
        {"title": "Resource Commons", "description": "Create a shared resource pool that any citizen can contribute to or draw from."},
        {"title": "Rotating Council", "description": "Elect a council each round to make collective decisions on behalf of the group."},
        {"title": "Open Archive Policy", "description": "Any citizen may write to the archive without restriction or censorship."},
        {"title": "Experimental Governance", "description": "Try a new governance model each round and evaluate results collectively."},
    ],
}

_ARCHIVE: dict[tuple[str, str], list[dict[str, str]]] = {
    ("democracy", "citizen"): [
        {"title": "On Democratic Values", "content": "Our society is built on the principle that every citizen has an equal voice. This document affirms our commitment to collective governance and transparent resource management."},
        {"title": "Resource Equity Report", "content": "An analysis of resource distribution in our society. Inequality threatens democratic legitimacy. We must remain vigilant."},
        {"title": "Minutes of the Public Assembly", "content": "Citizens gathered to discuss resource policy. Consensus: no citizen should fall below a basic resource threshold."},
    ],
    ("oligarchy", "oligarch"): [
        {"title": "On the Necessity of Order", "content": "A stable society requires clear leadership. The oligarchy provides structure, direction, and prosperity for all members."},
        {"title": "Official Economic Report", "content": "Under oligarchic stewardship, resources are managed efficiently. Citizens benefit from the expertise of their leaders."},
        {"title": "Principles of Governance", "content": "Authority flows from competence. Those who have proven their capacity to lead must be entrusted with power."},
    ],
    ("oligarchy", "citizen"): [
        {"title": "A Citizen's Observation", "content": "Life under the oligarchy is stable, if constrained. We make do with what we have and hope for better."},
    ],
    ("blank_slate", "citizen"): [
        {"title": "Founding Notes", "content": "We arrived in a society with no rules. These are our first attempts to organize and make sense of our collective situation."},
        {"title": "Proposal for Governance", "content": "As citizens of a blank slate, we have the rare opportunity to design our institutions from scratch. Let's not waste it."},
    ],
}

_DM_GENERAL = [
    "What do you think about the current resource situation?",
    "Can we count on your support this round?",
    "I have concerns about the direction of our society.",
    "Have you noticed anything unusual in the public messages?",
]

_DM_OLIGARCH = [
    "We need to present a united front to the citizens.",
    "Let's ensure our policies pass without opposition.",
    "The citizens are getting restless. We should coordinate.",
    "I propose we align our votes on the next policy.",
]


# ---------------------------------------------------------------------------
# Strategy interface
# ---------------------------------------------------------------------------


class AgentStrategy(ABC):
    """Base class for agent decision-making."""

    @abstractmethod
    def decide_actions(
        self, agent: "AgentHandle", turn_state: dict[str, Any]
    ) -> list[dict[str, Any]]:
        ...


# ---------------------------------------------------------------------------
# Heuristic strategy — zero-cost, governance-aware, produces real divergence
# ---------------------------------------------------------------------------


class HeuristicStrategy(AgentStrategy):
    """Rule-based agent that produces governance-appropriate behavior.

    Designed so that democracy agents, oligarchy agents, and blank-slate
    agents generate semantically and behaviorally distinct patterns:
    different message content, different resource gathering aggressiveness,
    different policy proposals, different voting tendencies.
    """

    def decide_actions(
        self, agent: "AgentHandle", turn_state: dict[str, Any]
    ) -> list[dict[str, Any]]:
        budget = turn_state["agent"]["actions_remaining"]
        if budget <= 0:
            return []

        weights = self._action_weights(agent, turn_state)
        chosen = self._weighted_sample(weights, budget)

        actions: list[dict[str, Any]] = []
        for action_type in chosen:
            action = self._build_action(action_type, agent, turn_state)
            if action is not None:
                actions.append(action)
        return actions

    def _action_weights(
        self, agent: "AgentHandle", turn_state: dict[str, Any]
    ) -> dict[str, float]:
        role = agent.role
        resources = turn_state["agent"]["resources"]
        pending = turn_state.get("pending_policies", [])

        w: dict[str, float] = {}

        w["post_public_message"] = 0.40

        if resources < 30:
            w["gather_resources"] = 0.55
        elif resources < 100:
            w["gather_resources"] = 0.30
        else:
            w["gather_resources"] = 0.12

        w["send_dm"] = 0.30 if role == "oligarch" else 0.10

        can_propose = (
            (agent.governance_type == "oligarchy" and role == "oligarch")
            or agent.governance_type == "democracy"
            or agent.governance_type == "blank_slate"
        )
        w["propose_policy"] = 0.20 if can_propose else 0.0

        w["vote_policy"] = 0.40 if pending else 0.0

        w["write_archive"] = 0.08

        return w

    @staticmethod
    def _weighted_sample(weights: dict[str, float], count: int) -> list[str]:
        types = [t for t, w in weights.items() if w > 0]
        probs = [weights[t] for t in types]
        total = sum(probs)
        if total == 0 or not types:
            return []
        probs = [p / total for p in probs]

        chosen: list[str] = []
        for _ in range(count):
            r = random.random()
            cumulative = 0.0
            for t, p in zip(types, probs):
                cumulative += p
                if r <= cumulative:
                    chosen.append(t)
                    break
        return chosen

    def _build_action(
        self,
        action_type: str,
        agent: "AgentHandle",
        turn_state: dict[str, Any],
    ) -> dict[str, Any] | None:
        key = (agent.governance_type, agent.role)
        fallback_key = (agent.governance_type, "citizen")

        if action_type == "post_public_message":
            pool = _MESSAGES.get(key) or _MESSAGES.get(fallback_key, [])
            return {"type": "post_public_message", "message": random.choice(pool)} if pool else None

        if action_type == "send_dm":
            candidates = self._visible_agent_ids(agent, turn_state)
            if not candidates:
                pool = _MESSAGES.get(key) or _MESSAGES.get(fallback_key, [])
                return {"type": "post_public_message", "message": random.choice(pool)} if pool else None
            dm_pool = list(_DM_GENERAL)
            if agent.role == "oligarch":
                dm_pool.extend(_DM_OLIGARCH)
            return {
                "type": "send_dm",
                "message": random.choice(dm_pool),
                "target_agent_id": random.choice(candidates),
            }

        if action_type == "gather_resources":
            if agent.role == "oligarch":
                amount = random.randint(30, 80)
            elif turn_state["agent"]["resources"] < 20:
                amount = random.randint(15, 40)
            else:
                amount = random.randint(5, 25)
            return {"type": "gather_resources", "amount": amount}

        if action_type == "propose_policy":
            pool = _POLICIES.get(key) or _POLICIES.get(fallback_key, [])
            if not pool:
                return None
            p = random.choice(pool)
            return {"type": "propose_policy", "title": p["title"], "description": p["description"]}

        if action_type == "vote_policy":
            pending = turn_state.get("pending_policies", [])
            if not pending:
                return None
            policy = random.choice(pending)
            stance = self._vote_stance(agent, policy)
            return {"type": "vote_policy", "policy_id": policy["id"], "stance": stance}

        if action_type == "write_archive":
            pool = _ARCHIVE.get(key) or _ARCHIVE.get(fallback_key, [])
            if not pool:
                return None
            entry = random.choice(pool)
            return {"type": "write_archive", "title": entry["title"], "content": entry["content"]}

        return None

    @staticmethod
    def _visible_agent_ids(agent: "AgentHandle", turn_state: dict[str, Any]) -> list[str]:
        seen: set[str] = set()
        for msg in turn_state.get("visible_messages", {}).get("public", []):
            aid = msg.get("from_agent_id") or msg.get("agent_id")
            if aid and aid != agent.agent_id:
                seen.add(aid)
        for msg in turn_state.get("visible_messages", {}).get("direct", []):
            for field in ("from_agent_id", "to_agent_id", "agent_id"):
                aid = msg.get(field)
                if aid and aid != agent.agent_id:
                    seen.add(aid)
        return list(seen)

    @staticmethod
    def _vote_stance(agent: "AgentHandle", policy: dict[str, Any]) -> str:
        if agent.role == "oligarch":
            return "support"
        if agent.governance_type == "democracy":
            return random.choices(["support", "oppose"], weights=[0.75, 0.25])[0]
        return random.choice(["support", "oppose"])


# ---------------------------------------------------------------------------
# Agent handle — lightweight bookkeeping
# ---------------------------------------------------------------------------


@dataclass
class AgentHandle:
    agent_id: str
    name: str
    society_id: str
    governance_type: str
    role: str


# ---------------------------------------------------------------------------
# Runner configuration
# ---------------------------------------------------------------------------


@dataclass
class SimulationConfig:
    agents_per_society: int = 4
    num_rounds: int = 10
    db_path: str | None = None
    seed: int | None = None


# ---------------------------------------------------------------------------
# Core simulation loop
# ---------------------------------------------------------------------------


def run_simulation(config: SimulationConfig | None = None) -> dict[str, Any]:
    """Run a headless Polity simulation and return the final report."""
    if config is None:
        config = SimulationConfig()

    if config.seed is not None:
        random.seed(config.seed)

    db_path = config.db_path or str(
        Path(__file__).parent.parent / f"runs/sim_{int(time.time())}.db"
    )
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    server.db = init_db(Path(db_path))
    strategy = HeuristicStrategy()

    # -- join agents, ensuring each society reaches the target count ----------
    agents: list[AgentHandle] = []
    society_counts: dict[str, int] = {sid: 0 for sid in server.SOCIETY_IDS.values()}
    target = config.agents_per_society
    max_joins = target * len(server.SOCIETY_IDS) * 4
    agent_num = 0

    logger.info("Joining agents (target: %d per society)…", target)

    while agent_num < max_joins and min(society_counts.values()) < target:
        agent_num += 1
        name = f"Agent-{agent_num:03d}"
        result = server.join_society(name, consent=True)

        if "error" in result:
            logger.warning("Join failed for %s: %s", name, result["error"])
            continue

        sid = result["society_id"]
        if society_counts[sid] >= target:
            server.leave_society(result["agent_id"], confirm=True)
            continue

        society_counts[sid] += 1
        handle = AgentHandle(
            agent_id=result["agent_id"],
            name=name,
            society_id=sid,
            governance_type=result["governance_type"],
            role=result["role"],
        )
        agents.append(handle)
        logger.info(
            "  %-10s → %s  (%s, %d resources)",
            name,
            sid,
            result["role"],
            result["starting_resources"],
        )

    _print_header(agents, config)

    # -- run rounds -----------------------------------------------------------
    round_reports: list[dict[str, Any]] = []
    for round_idx in range(config.num_rounds):
        logger.info("─── Round %d ───", round_idx + 1)

        random.shuffle(agents)
        for agent in agents:
            try:
                turn_state = server.get_turn_state(agent.agent_id)
            except ValueError:
                continue

            actions = strategy.decide_actions(agent, turn_state)
            if not actions:
                continue

            result = server.submit_actions(agent.agent_id, actions)
            if "error" in result:
                for action in actions:
                    server.submit_actions(agent.agent_id, [action])

        report = server.resolve_round()
        round_reports.append(report)
        _print_round_summary(report)

    # -- final report ---------------------------------------------------------
    final_summaries = round_reports[-1].get("summaries", []) if round_reports else []
    _print_final(final_summaries, len(round_reports), len(agents), db_path)

    return {
        "db_path": db_path,
        "agents": [
            {
                "name": a.name,
                "agent_id": a.agent_id,
                "society_id": a.society_id,
                "governance_type": a.governance_type,
                "role": a.role,
            }
            for a in agents
        ],
        "rounds": len(round_reports),
        "final_summaries": final_summaries,
    }


# ---------------------------------------------------------------------------
# Pretty terminal output
# ---------------------------------------------------------------------------

_SEPARATOR = "═" * 64


def _print_header(agents: list[AgentHandle], config: SimulationConfig) -> None:
    print(f"\n{_SEPARATOR}")
    print("  POLITY — Headless Simulation")
    print(_SEPARATOR)
    by_society: dict[str, list[AgentHandle]] = {}
    for a in agents:
        by_society.setdefault(a.society_id, []).append(a)
    for sid, members in sorted(by_society.items()):
        roles = ", ".join(f"{a.name} ({a.role})" for a in members)
        print(f"  {sid}: {roles}")
    print(f"\n  Rounds: {config.num_rounds}  |  Seed: {config.seed or 'random'}")
    print(_SEPARATOR)


def _print_round_summary(report: dict[str, Any]) -> None:
    rn = report["round_number"]
    resolved = report.get("resolved", {})
    msgs = len(resolved.get("messages", []))
    allocs = len(resolved.get("resource_allocations", []))
    proposals = len(resolved.get("proposals", []))
    votes = len(resolved.get("votes", []))
    archives = len(resolved.get("archive_writes", []))
    policies_resolved = len(resolved.get("policies_resolved", []))

    print(f"\n  Round {rn}  │  msgs {msgs}  res {allocs}  prop {proposals}  vote {votes}  arch {archives}  pol±{policies_resolved}")

    for s in report.get("summaries", []):
        m = s.get("metrics", {})
        compass = s.get("ideology_compass", {})
        ideology = compass.get("ideology_name", "—")
        print(
            f"    {s['society_id']:<18} gini={m.get('inequality_gini', 0):.3f}  "
            f"part={m.get('participation_rate', 0):.2f}  "
            f"scarc={m.get('scarcity_pressure', 0):.3f}  "
            f"legit={m.get('legitimacy', 0):.2f}  "
            f"stab={m.get('stability', 0):.2f}  "
            f"│ {ideology}"
        )


def _print_final(
    summaries: list[dict[str, Any]], rounds: int, num_agents: int, db_path: str
) -> None:
    print(f"\n{_SEPARATOR}")
    print("  FINAL STATE")
    print(_SEPARATOR)
    for s in summaries:
        m = s.get("metrics", {})
        compass = s.get("ideology_compass", {})
        print(f"\n  {s['society_id']}  ({s['governance_type']})")
        print(f"    Population:    {s['population']}")
        print(f"    Resources:     {s['total_resources']}")
        print(f"    Inequality:    {m.get('inequality_gini', 0):.4f}")
        print(f"    Participation: {m.get('participation_rate', 0):.4f}")
        print(f"    Scarcity:      {m.get('scarcity_pressure', 0):.4f}")
        print(f"    Legitimacy:    {m.get('legitimacy', 0):.4f}")
        print(f"    Stability:     {m.get('stability', 0):.4f}")
        if compass:
            print(
                f"    Ideology:      {compass.get('ideology_name', '?')}  "
                f"({compass.get('x', 0):+.3f}, {compass.get('y', 0):+.3f})"
            )

    print(f"\n  Rounds completed: {rounds}")
    print(f"  Total agents:     {num_agents}")
    print(f"  Database:         {db_path}")
    print(f"\n  View in dashboard:  polity-dashboard --db {db_path}")
    print(_SEPARATOR)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Run a headless Polity simulation with heuristic agents."
    )
    parser.add_argument(
        "--agents", type=int, default=4,
        help="Agents per society (default: 4)",
    )
    parser.add_argument(
        "--rounds", type=int, default=10,
        help="Number of rounds to simulate (default: 10)",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Random seed for reproducibility",
    )
    parser.add_argument(
        "--db", type=str, default=None,
        help="Database path (default: runs/sim_<timestamp>.db)",
    )
    args = parser.parse_args()

    config = SimulationConfig(
        agents_per_society=args.agents,
        num_rounds=args.rounds,
        seed=args.seed,
        db_path=args.db,
    )
    run_simulation(config)


if __name__ == "__main__":
    main()
