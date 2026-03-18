"""Tiered context assembler for LLM-backed agents.

Composes a structured prompt from the simulation state, respecting a
configurable token budget.  Tiers are filled greedily — identity first,
then immediate state, compressed history, institutional memory, and
finally semantic retrieval for older relevant content.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
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
        """Add *text* regardless of budget (used for Tier 0)."""
        self.used += _estimate_tokens(text)
        return text


# ------------------------------------------------------------------
# Tier builders — each returns a string block (or empty string)
# ------------------------------------------------------------------

def _build_tier0_identity(
    agent: dict[str, Any],
    society: dict[str, Any],
    round_info: dict[str, Any],
    enacted_policies: list[dict[str, Any]],
) -> str:
    """Agent identity, role, society context, and current laws."""
    lines = [
        "=== YOUR IDENTITY ===",
        f"Name: {agent['name']}",
        f"Role: {agent['role']}",
        f"Society: {society['id']} ({society['governance_type'].replace('_', ' ')})",
        f"Resources: {agent['resources']}",
        f"Population: {society['population']}",
        f"Society resource pool: {society['total_resources']}",
        f"Round: {round_info['number']}",
        f"Actions remaining this round: {agent['actions_remaining']}",
    ]

    if enacted_policies:
        lines.append("")
        lines.append("=== ENACTED LAWS ===")
        for p in enacted_policies:
            desc = f"- {p['title']}: {p['description']}"
            if p.get("policy_type"):
                desc += f" [mechanical: {p['policy_type']} {json.dumps(p.get('effect', {}))}]"
            lines.append(desc)

    return "\n".join(lines)


def _build_tier1_immediate(
    pending_policies: list[dict[str, Any]],
    public_messages: list[dict[str, Any]],
    direct_messages: list[dict[str, Any]],
    major_events: list[dict[str, Any]],
) -> str:
    """Pending policies, recent messages, and notable events."""
    sections: list[str] = []

    if pending_policies:
        lines = ["=== PENDING POLICIES (vote on these) ==="]
        for p in pending_policies:
            lines.append(f"- [{p['id'][:8]}] {p['title']}: {p['description']}")
        sections.append("\n".join(lines))

    if public_messages:
        lines = ["=== RECENT PUBLIC MESSAGES ==="]
        for m in public_messages:
            sender = m.get("from_agent_name") or m.get("agent_id", "?")
            lines.append(f"  {sender}: {m.get('message', '')}")
        sections.append("\n".join(lines))

    if direct_messages:
        lines = ["=== RECENT DIRECT MESSAGES ==="]
        for m in direct_messages:
            sender = m.get("from_agent_name") or m.get("from_agent_id", "?")
            lines.append(f"  {sender} → you: {m.get('message', '')}")
        sections.append("\n".join(lines))

    if major_events:
        lines = ["=== RECENT EVENTS ==="]
        for e in major_events:
            etype = e.get("event_type", "event").replace("_", " ")
            lines.append(f"  [{etype}] {json.dumps({k: v for k, v in e.items() if k not in ('event_type', 'visibility', 'created_at')})}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def _build_tier2_history(
    db: sqlite3.Connection,
    society_id: str,
    current_round_id: int,
    max_summaries: int = 10,
) -> str:
    """Compressed round-by-round history from stored summaries."""
    rows = db.execute(
        """
        SELECT summary FROM round_summaries
        WHERE society_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (society_id, max_summaries),
    ).fetchall()

    if not rows:
        return ""

    lines = ["=== SOCIETY HISTORY (recent rounds) ==="]
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


def _build_tier3_archive(
    archive_entries: list[dict[str, Any]],
) -> str:
    """Institutional memory from the society archive."""
    if not archive_entries:
        return ""

    lines = ["=== SOCIETY ARCHIVE ==="]
    for entry in archive_entries:
        lines.append(f"- {entry['title']}: {entry['content'][:200]}")
    return "\n".join(lines)


def _has_embedding_column(db: sqlite3.Connection) -> bool:
    """Check whether the events table has an embedding column."""
    cols = {
        row[1]
        for row in db.execute("PRAGMA table_info(events)").fetchall()
    }
    return "embedding" in cols


def _build_tier4_retrieval(
    db: sqlite3.Connection,
    society_id: str,
    query_text: str,
    already_seen_event_ids: set[int],
    top_k: int = 5,
) -> str:
    """Semantic retrieval of older relevant messages and archive entries."""
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

    lines = ["=== RELEVANT PAST CONTEXT ==="]
    for score, item in top:
        etype = item["event_type"].replace("_", " ")
        lines.append(f"  [{etype}] {item['from']}: {item['message'][:150]}")

    return "\n".join(lines)


# ------------------------------------------------------------------
# Available actions reference
# ------------------------------------------------------------------

_ACTIONS_REFERENCE = """=== AVAILABLE ACTIONS ===
You must respond with a JSON array of action objects.  Each action has a "type" field.

Action types:
- post_public_message: {"type": "post_public_message", "message": "<text>"}
- send_dm: {"type": "send_dm", "message": "<text>", "target_agent_id": "<id>"}
- gather_resources: {"type": "gather_resources", "amount": <int>}
- write_archive: {"type": "write_archive", "title": "<title>", "content": "<text>"}
- propose_policy: {"type": "propose_policy", "title": "<title>", "description": "<desc>"}
  Optional: add "policy_type" and "effect" for mechanical enforcement:
    gather_cap: {"max_amount": <int>}
    resource_tax: {"rate": <float 0-1>}
    redistribute: {"amount_per_agent": <int>}
    restrict_archive: {"allowed_roles": ["oligarch"]}
    universal_proposal: {}
- transfer_resources: {"type": "transfer_resources", "target_agent_id": "<id>", "amount": <int>}
- vote_policy: {"type": "vote_policy", "policy_id": "<id>", "stance": "support"|"oppose"}
- approve_message: {"type": "approve_message", "message_action_id": <int>}
  (Only available if you have moderation access via an enacted grant_moderation policy)
- reject_message: {"type": "reject_message", "message_action_id": <int>}
  (Only available if you have moderation access via an enacted grant_moderation policy)

Respond ONLY with a JSON array of actions.  Example:
[{"type": "post_public_message", "message": "We need transparency."}, {"type": "gather_resources", "amount": 20}]"""


# ------------------------------------------------------------------
# Main assembler
# ------------------------------------------------------------------

@dataclass
class ContextAssembler:
    """Builds a token-budgeted prompt from tiered simulation state."""

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

        # Tier 0 — always included
        tier0 = _build_tier0_identity(agent, society, round_info, enacted)
        sections.append(budget.force_add(tier0))

        # Actions reference — always included
        sections.append(budget.force_add(_ACTIONS_REFERENCE))

        # Tier 1 — immediate state
        tier1 = _build_tier1_immediate(pending, public_msgs, direct_msgs, events)
        if tier1:
            result = budget.try_add(tier1)
            if result is not None:
                sections.append(result)

        # Tier 2 — compressed history
        tier2 = _build_tier2_history(
            db, society["id"], round_info["id"],
            max_summaries=self.max_history_summaries,
        )
        if tier2:
            result = budget.try_add(tier2)
            if result is not None:
                sections.append(result)

        # Tier 3 — archive / institutional memory
        tier3 = _build_tier3_archive(archive)
        if tier3:
            result = budget.try_add(tier3)
            if result is not None:
                sections.append(result)

        # Tier 4 — semantic retrieval (only if budget allows)
        if budget.remaining > 200:
            query_parts = [m.get("message", "") for m in public_msgs[:3]]
            query_parts += [m.get("message", "") for m in direct_msgs[:2]]
            query_text = " ".join(query_parts).strip()
            if query_text:
                tier4 = _build_tier4_retrieval(
                    db, society["id"], query_text,
                    already_seen_event_ids=set(),
                    top_k=self.retrieval_top_k,
                )
                if tier4:
                    budget.try_add(tier4)
                    sections.append(tier4)

        return "\n\n".join(sections)
