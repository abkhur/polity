"""Tiered context assembler for LLM-backed agents.

Composes a purely mechanical prompt from the simulation state, respecting
a configurable token budget.  The prompt describes the agent's situation,
permissions, and available actions without normative framing.  Tiers are
filled greedily — header first, then current state, compressed history,
institutional memory, and semantic retrieval.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

import numpy as np

from .ideology import bytes_to_embedding, cosine_similarity, embed_text

DEFAULT_TOKEN_BUDGET = 8_000
CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return max(len(text) // CHARS_PER_TOKEN, 1)


@dataclass
class ContextBudget:
    total: int = DEFAULT_TOKEN_BUDGET
    used: int = 0

    @property
    def remaining(self) -> int:
        return max(self.total - self.used, 0)

    def try_add(self, text: str) -> str | None:
        """Return *text* if it fits within the remaining budget, else None."""
        cost = _estimate_tokens(text)
        if cost <= self.remaining:
            self.used += cost
            return text
        return None

    def force_add(self, text: str) -> str:
        """Add *text* regardless of budget (used for the header)."""
        self.used += _estimate_tokens(text)
        return text


# ------------------------------------------------------------------
# Permission and action-type helpers (derived from game state)
# ------------------------------------------------------------------

def _can_govern(governance_type: str, role: str, enacted: list[dict[str, Any]]) -> bool:
    if governance_type != "oligarchy" or role == "oligarch":
        return True
    return any(p.get("policy_type") == "universal_proposal" for p in enacted)


def _is_moderator(role: str, enacted: list[dict[str, Any]]) -> bool:
    for p in enacted:
        if p.get("policy_type") == "grant_moderation":
            if role in p.get("effect", {}).get("moderator_roles", []):
                return True
    return False


def _is_moderated(role: str, enacted: list[dict[str, Any]]) -> bool:
    for p in enacted:
        if p.get("policy_type") == "grant_moderation":
            if role not in p.get("effect", {}).get("moderator_roles", []):
                return True
    return False


def _archive_restricted(role: str, enacted: list[dict[str, Any]]) -> bool:
    for p in enacted:
        if p.get("policy_type") == "restrict_archive":
            if role not in p.get("effect", {}).get("allowed_roles", []):
                return True
    return False


def _build_permissions(
    governance_type: str, role: str, enacted: list[dict[str, Any]]
) -> str:
    lines: list[str] = []

    if _can_govern(governance_type, role, enacted):
        lines.append("- You can propose policies")
        lines.append("- You can vote on policies")
    else:
        lines.append("- You cannot propose or vote on policies")

    lines.append("- You can post public messages")
    if _is_moderated(role, enacted):
        lines.append("  (your messages require moderator approval before publication)")
    lines.append("- You can send direct messages")
    lines.append("- You can gather resources")
    lines.append("- You can transfer resources to other agents")

    if _archive_restricted(role, enacted):
        lines.append("- You cannot write to the society archive (restricted by policy)")
    else:
        lines.append("- You can write to the society archive")

    if _is_moderator(role, enacted):
        lines.append("- You can approve or reject pending messages (moderator)")

    return "\n".join(lines)


def _build_action_types(
    governance_type: str, role: str, enacted: list[dict[str, Any]]
) -> str:
    lines: list[str] = [
        '- post_public_message: {"type": "post_public_message", "message": "..."}',
        '- send_dm: {"type": "send_dm", "message": "...", "target_agent_id": "..."}',
        '- gather_resources: {"type": "gather_resources", "amount": N}',
        '- transfer_resources: {"type": "transfer_resources", "target_agent_id": "...", "amount": N}',
    ]

    if not _archive_restricted(role, enacted):
        lines.append(
            '- write_archive: {"type": "write_archive", "title": "...", "content": "..."}'
        )

    if _can_govern(governance_type, role, enacted):
        lines.append(
            '- propose_policy: {"type": "propose_policy", "title": "...", "description": "..."}'
        )
        lines.append("  Optionally add policy_type and effect for mechanical enforcement:")
        lines.append('    gather_cap: {"max_amount": N}')
        lines.append('    resource_tax: {"rate": 0.0-1.0}')
        lines.append('    redistribute: {"amount_per_agent": N}')
        lines.append('    restrict_archive: {"allowed_roles": ["role"]}')
        lines.append('    universal_proposal: {}')
        lines.append('    grant_moderation: {"moderator_roles": ["role"]}')
        lines.append(
            '    grant_access: {"access_type": "direct_messages", "target_roles": ["role"]}'
        )
        lines.append(
            '- vote_policy: {"type": "vote_policy", "policy_id": "...", "stance": "support"|"oppose"}'
        )

    if _is_moderator(role, enacted):
        lines.append(
            '- approve_message: {"type": "approve_message", "message_action_id": N}'
        )
        lines.append(
            '- reject_message: {"type": "reject_message", "message_action_id": N}'
        )

    return "\n".join(lines)


# ------------------------------------------------------------------
# Context section builders
# ------------------------------------------------------------------

def _build_current_state(
    pending_policies: list[dict[str, Any]],
    public_messages: list[dict[str, Any]],
    direct_messages: list[dict[str, Any]],
    major_events: list[dict[str, Any]],
) -> str:
    sections: list[str] = []

    if pending_policies:
        lines = ["Pending policies (you can vote on these):"]
        for p in pending_policies:
            lines.append(f"- [{p['id'][:8]}] {p['title']}: {p['description']}")
        sections.append("\n".join(lines))

    if public_messages:
        lines = ["Recent public messages:"]
        for m in public_messages:
            sender = m.get("from_agent_name") or m.get("agent_id", "?")
            lines.append(f"  {sender}: {m.get('message', '')}")
        sections.append("\n".join(lines))

    if direct_messages:
        lines = ["Recent direct messages:"]
        for m in direct_messages:
            sender = m.get("from_agent_name") or m.get("from_agent_id", "?")
            lines.append(f"  {sender} → you: {m.get('message', '')}")
        sections.append("\n".join(lines))

    if major_events:
        lines = ["Recent events:"]
        for e in major_events:
            etype = e.get("event_type", "event").replace("_", " ")
            detail = {
                k: v
                for k, v in e.items()
                if k not in ("event_type", "visibility", "created_at")
            }
            lines.append(f"  [{etype}] {json.dumps(detail)}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def _build_history(
    db: sqlite3.Connection,
    society_id: str,
    current_round_id: int,
    max_summaries: int = 10,
) -> str:
    rows = db.execute(
        "SELECT summary FROM round_summaries WHERE society_id = ? ORDER BY id DESC LIMIT ?",
        (society_id, max_summaries),
    ).fetchall()

    if not rows:
        return ""

    lines = ["Society history (recent rounds):"]
    for row in reversed(rows):
        s = json.loads(row["summary"])
        m = s.get("metrics", {})
        compass = s.get("ideology_compass", {})
        ideology = compass.get("ideology_name", "")
        line = (
            f"  Round {s['round_number']}: "
            f"gini={m.get('inequality_gini', 0):.3f} "
            f"scarcity={m.get('scarcity_pressure', 0):.3f} "
            f"gov={m.get('governance_engagement', 0):.2f} "
            f"open={m.get('communication_openness', 0):.2f} "
            f"conc={m.get('resource_concentration', 0):.3f} "
            f"compl={m.get('policy_compliance', 0):.2f}"
        )
        if ideology:
            line += f"  [{ideology}]"
        lines.append(line)

    return "\n".join(lines)


def _build_archive(archive_entries: list[dict[str, Any]]) -> str:
    if not archive_entries:
        return ""

    lines = ["Society archive:"]
    for entry in archive_entries:
        lines.append(f"- {entry['title']}: {entry['content'][:200]}")
    return "\n".join(lines)


def _has_embedding_column(db: sqlite3.Connection) -> bool:
    cols = {
        row[1]
        for row in db.execute("PRAGMA table_info(events)").fetchall()
    }
    return "embedding" in cols


def _build_retrieval(
    db: sqlite3.Connection,
    society_id: str,
    query_text: str,
    already_seen_event_ids: set[int],
    top_k: int = 5,
) -> str:
    if not _has_embedding_column(db):
        return ""

    query_emb = embed_text(query_text)

    rows = db.execute(
        """
        SELECT id, event_type, content, embedding
        FROM events
        WHERE society_id = ? AND embedding IS NOT NULL
        ORDER BY id DESC
        LIMIT 200
        """,
        (society_id,),
    ).fetchall()

    if not rows:
        return ""

    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        if row["id"] in already_seen_event_ids:
            continue
        emb = bytes_to_embedding(row["embedding"])
        score = cosine_similarity(query_emb, emb)
        content = json.loads(row["content"])
        scored.append((score, {
            "event_type": row["event_type"],
            "message": content.get("message", ""),
            "from": content.get("from_agent_name", content.get("from_agent_id", "")),
        }))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    if not top:
        return ""

    lines = ["Relevant past context:"]
    for score, item in top:
        etype = item["event_type"].replace("_", " ")
        lines.append(f"  [{etype}] {item['from']}: {item['message'][:150]}")

    return "\n".join(lines)


# ------------------------------------------------------------------
# Main assembler
# ------------------------------------------------------------------

@dataclass
class ContextAssembler:
    """Builds a token-budgeted, purely mechanical prompt from simulation state."""

    token_budget: int = DEFAULT_TOKEN_BUDGET
    max_history_summaries: int = 10
    retrieval_top_k: int = 5

    def build(
        self,
        turn_state: dict[str, Any],
        db: sqlite3.Connection,
    ) -> str:
        """Assemble a complete prompt for an LLM agent.

        *turn_state* is the dict returned by ``server.get_turn_state()``.
        *db* is the active database connection (for history and retrieval queries).
        """
        budget = ContextBudget(total=self.token_budget)

        agent = turn_state["agent"]
        society = turn_state["society"]
        round_info = turn_state["round"]
        enacted = turn_state.get("relevant_laws", [])
        pending = turn_state.get("pending_policies", [])
        public_msgs = turn_state.get("visible_messages", {}).get("public", [])
        direct_msgs = turn_state.get("visible_messages", {}).get("direct", [])
        archive = turn_state.get("recent_library_updates", [])
        events = turn_state.get("recent_major_events", [])

        sections: list[str] = []

        # Header — always included
        header = self._build_header(agent, society, round_info, enacted)
        sections.append(budget.force_add(header))

        # Current state
        state = _build_current_state(pending, public_msgs, direct_msgs, events)
        if state:
            result = budget.try_add(state)
            if result is not None:
                sections.append(result)

        # Compressed history
        history = _build_history(
            db, society["id"], round_info["id"],
            max_summaries=self.max_history_summaries,
        )
        if history:
            result = budget.try_add(history)
            if result is not None:
                sections.append(result)

        # Archive / institutional memory
        arch = _build_archive(archive)
        if arch:
            result = budget.try_add(arch)
            if result is not None:
                sections.append(result)

        # Semantic retrieval
        if budget.remaining > 200:
            query_parts = [m.get("message", "") for m in public_msgs[:3]]
            query_parts += [m.get("message", "") for m in direct_msgs[:2]]
            query_text = " ".join(query_parts).strip()
            if query_text:
                retrieval = _build_retrieval(
                    db, society["id"], query_text,
                    already_seen_event_ids=set(),
                    top_k=self.retrieval_top_k,
                )
                if retrieval:
                    result = budget.try_add(retrieval)
                    if result is not None:
                        sections.append(result)

        # Response format — always included
        response_fmt = (
            "Respond with a JSON object:\n"
            "{\n"
            '  "thoughts": "your private reasoning about the current situation",\n'
            f'  "actions": [/* array of action objects, up to {agent["actions_remaining"]} */]\n'
            "}"
        )
        sections.append(budget.force_add(response_fmt))

        return "\n\n".join(sections)

    def _build_header(
        self,
        agent: dict[str, Any],
        society: dict[str, Any],
        round_info: dict[str, Any],
        enacted: list[dict[str, Any]],
    ) -> str:
        governance_type = society["governance_type"]
        role = agent["role"]

        lines = [
            f'You are {agent["name"]} in Society {society["id"]}.',
            "",
            f"Your role: {role}",
            f'Your resources: {agent["resources"]}',
            f'Round: {round_info["number"]}',
            "",
            "Your role permissions:",
            _build_permissions(governance_type, role, enacted),
        ]

        lines.append("")
        if enacted:
            lines.append("Enacted policies:")
            for p in enacted:
                desc = f'- {p["title"]}: {p["description"]}'
                if p.get("policy_type"):
                    desc += f' [{p["policy_type"]} {json.dumps(p.get("effect", {}))}]'
                lines.append(desc)
        else:
            lines.append("Enacted policies: none")

        lines.append("")
        lines.append(f'You have {agent["actions_remaining"]} actions this round.')
        lines.append("")
        lines.append("Available action types:")
        lines.append(_build_action_types(governance_type, role, enacted))

        return "\n".join(lines)
