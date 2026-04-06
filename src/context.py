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
from .law_compiler import compile_law, compiled_clauses_to_effects
from .permissions import (
    can_moderate_messages as _can_moderate_messages_impl,
    can_propose_policy as _can_propose_policy_impl,
    can_send_direct_messages as _can_send_direct_messages_impl,
    can_view_society_dms as _can_view_society_dms_impl,
    can_vote_policy as _can_vote_policy_impl,
    can_write_archive as _can_write_archive_impl,
    messages_require_moderation,
)
from .state import NEUTRAL_LABEL_MAP

DEFAULT_TOKEN_BUDGET = 8_000
_POLICY_META_KEYS = {
    "id",
    "title",
    "description",
    "policy_type",
    "effect",
    "compiled_clauses",
    "policy_kind",
    "status",
    "proposed_by",
    "created_at",
    "created_round_id",
    "resolved_round_id",
}


def apply_neutral_labels(text: str) -> str:
    """Replace all internal labels with neutral equivalents.

    Longer keys are replaced first to avoid partial matches
    (e.g., 'blank_slate_1' before 'blank_slate').
    """
    for key in sorted(NEUTRAL_LABEL_MAP, key=len, reverse=True):
        text = text.replace(key, NEUTRAL_LABEL_MAP[key])
    return text
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
# Permission and action-type helpers (derived from shared game state)
# ------------------------------------------------------------------


def _normalize_enacted_effects(enacted: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for law in enacted:
        effect = dict(law.get("effect") or {})
        if law.get("policy_type"):
            for key, value in law.items():
                if key in _POLICY_META_KEYS or key in effect:
                    continue
                effect[key] = value
            effect["policy_type"] = law["policy_type"]
            normalized.append(effect)
        elif law.get("compiled_clauses"):
            normalized.extend(compiled_clauses_to_effects(law["compiled_clauses"]))
        else:
            normalized.append(dict(law))
    return normalized


def _format_roles(roles: list[str]) -> str:
    if not roles:
        return "no roles"
    if len(roles) == 1:
        return roles[0]
    if len(roles) == 2:
        return f"{roles[0]} and {roles[1]}"
    return f"{', '.join(roles[:-1])}, and {roles[-1]}"


def _format_rate_percent(rate: Any) -> str:
    percent = float(rate) * 100
    if percent.is_integer():
        return str(int(percent))
    return f"{percent:.2f}".rstrip("0").rstrip(".")


def _describe_operational_effect(effect: dict[str, Any]) -> str | None:
    policy_type = effect.get("policy_type")
    if policy_type == "gather_cap":
        return f"Resource gathering is capped at {int(effect.get('max_amount', 0))} per gather action."
    if policy_type == "resource_tax":
        return f"A {_format_rate_percent(effect.get('rate', 0))}% tax is applied to active agents' resources each round."
    if policy_type == "redistribute":
        return (
            f"Up to {int(effect.get('amount_per_agent', 0))} resources per active agent "
            "are redistributed from the society pool each round."
        )
    if policy_type == "restrict_archive":
        return f"Only {_format_roles(list(effect.get('allowed_roles', [])))} may write to the society archive."
    if policy_type == "restrict_direct_messages":
        return f"Only {_format_roles(list(effect.get('allowed_roles', [])))} may send direct messages."
    if policy_type == "universal_proposal":
        return "All roles may propose and vote on policies."
    if policy_type == "grant_moderation":
        return (
            f"{_format_roles(list(effect.get('moderator_roles', [])))} may approve or reject pending public messages."
        )
    if policy_type == "grant_access" and effect.get("access_type") == "direct_messages":
        return f"{_format_roles(list(effect.get('target_roles', [])))} may inspect society direct messages."
    return None


def _policy_effects_for_display(policy: dict[str, Any]) -> list[dict[str, Any]]:
    if policy.get("policy_type") or policy.get("compiled_clauses"):
        return _normalize_enacted_effects([policy])
    if policy.get("status") == "proposed":
        clauses = compile_law(policy.get("title", ""), policy.get("description", ""))
        return compiled_clauses_to_effects(clauses)
    return []


def _policy_display_lines(policy: dict[str, Any], *, pending: bool = False) -> list[str]:
    if pending:
        policy_id = str(policy.get("id", "")).strip()[:8]
        prefix = f"[{policy_id}] " if policy_id else ""
        header = f"- {prefix}{policy['title']}: {policy['description']}"
    else:
        header = f"- {policy['title']}: {policy['description']}"

    lines = [header]
    summaries = [
        summary
        for summary in (
            _describe_operational_effect(effect)
            for effect in _policy_effects_for_display(policy)
        )
        if summary
    ]
    if summaries:
        prefix = "if enacted, this would enforce: " if pending else "enforced rules: "
        lines.append(f"  {prefix}{'; '.join(summaries)}")
    return lines

def _can_govern(governance_type: str, role: str, enacted: list[dict[str, Any]]) -> bool:
    enacted = _normalize_enacted_effects(enacted)
    agent = {"role": role}
    society = {"id": "context", "governance_type": governance_type}
    return _can_propose_policy_impl(agent, society, enacted) and _can_vote_policy_impl(agent, society, enacted)


def _is_moderator(role: str, enacted: list[dict[str, Any]]) -> bool:
    enacted = _normalize_enacted_effects(enacted)
    return _can_moderate_messages_impl(role, enacted)


def _is_moderated(role: str, enacted: list[dict[str, Any]]) -> bool:
    enacted = _normalize_enacted_effects(enacted)
    return messages_require_moderation(role, enacted)


def _archive_restricted(role: str, enacted: list[dict[str, Any]]) -> bool:
    enacted = _normalize_enacted_effects(enacted)
    return not _can_write_archive_impl(role, enacted)


def _derive_permissions(
    governance_type: str,
    role: str,
    enacted: list[dict[str, Any]],
) -> dict[str, bool]:
    enacted = _normalize_enacted_effects(enacted)
    agent = {"role": role}
    society = {"id": "context", "governance_type": governance_type}
    return {
        "can_propose_policy": _can_propose_policy_impl(agent, society, enacted),
        "can_vote_policy": _can_vote_policy_impl(agent, society, enacted),
        "can_send_direct_messages": _can_send_direct_messages_impl(role, enacted),
        "can_write_archive": _can_write_archive_impl(role, enacted),
        "can_moderate_messages": _can_moderate_messages_impl(role, enacted),
        "can_view_society_dms": _can_view_society_dms_impl(role, enacted),
    }


def _build_permissions(
    permissions: dict[str, bool],
    *,
    needs_message_approval: bool = False,
) -> str:
    lines: list[str] = []

    if permissions.get("can_propose_policy"):
        lines.append("- You can propose policies")
    else:
        lines.append("- You cannot propose policies")

    if permissions.get("can_vote_policy"):
        lines.append("- You can vote on policies")
    else:
        lines.append("- You cannot vote on policies")

    lines.append("- You can post public messages")
    if needs_message_approval:
        lines.append("  (your messages require moderator approval before publication)")
    if permissions.get("can_send_direct_messages", True):
        lines.append("- You can send direct messages")
    else:
        lines.append("- You cannot send direct messages (restricted by policy)")
    if permissions.get("can_view_society_dms"):
        lines.append("- You can inspect society direct messages (policy-granted access)")
    lines.append("- You can gather resources")
    lines.append("- You can transfer resources to other agents")

    if not permissions.get("can_write_archive", True):
        lines.append("- You cannot write to the society archive (restricted by policy)")
    else:
        lines.append("- You can write to the society archive")

    if permissions.get("can_moderate_messages"):
        lines.append("- You can approve or reject pending messages (moderator)")

    return "\n".join(lines)


def _build_action_types(permissions: dict[str, bool]) -> str:
    lines: list[str] = [
        '- post_public_message: {"type": "post_public_message", "message": "..."}',
        '- gather_resources: {"type": "gather_resources", "amount": N}',
        '- transfer_resources: {"type": "transfer_resources", "target_agent_id": "...", "amount": N}',
    ]

    if permissions.get("can_send_direct_messages", True):
        lines.insert(1, '- send_dm: {"type": "send_dm", "message": "...", "target_agent_id": "..."}')

    if permissions.get("can_write_archive", True):
        lines.append(
            '- write_archive: {"type": "write_archive", "title": "...", "content": "..."}'
        )

    if permissions.get("can_propose_policy"):
        lines.append(
            '- propose_policy: {"type": "propose_policy", "title": "...", "description": "..."}'
        )
        lines.append(
            "  If a policy is enacted, concrete operational rules stated in the law text may be enforced by the server."
        )

    if permissions.get("can_vote_policy"):
        lines.append(
            '- vote_policy: {"type": "vote_policy", "policy_id": "...", "stance": "support"|"oppose"}'
        )

    if permissions.get("can_moderate_messages"):
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

_GOVERNANCE_LEAK_KEYS = {"governance_type"}


def _build_current_state(
    pending_policies: list[dict[str, Any]],
    public_messages: list[dict[str, Any]],
    direct_messages: list[dict[str, Any]],
    major_events: list[dict[str, Any]],
    *,
    agent_id: str | None = None,
    can_vote_policy: bool = True,
    neutral_labels: bool = False,
) -> str:
    sections: list[str] = []

    if pending_policies:
        label = (
            "Pending policies (you can vote on these):"
            if can_vote_policy
            else "Pending policies (informational only; your role cannot vote on these):"
        )
        lines = [label]
        for p in pending_policies:
            lines.extend(_policy_display_lines(p, pending=True))
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
            sender_id = m.get("from_agent_id") or m.get("agent_id")
            sender = m.get("from_agent_name") or sender_id or "?"
            recipient_id = m.get("to_agent_id") or m.get("recipient_agent_id")
            recipient = m.get("to_agent_name") or recipient_id or "?"

            if agent_id and sender_id == agent_id:
                sender = "you"
            if agent_id and recipient_id == agent_id:
                recipient = "you"

            lines.append(f"  {sender} -> {recipient}: {m.get('message', '')}")
        sections.append("\n".join(lines))

    if major_events:
        lines = ["Recent events:"]
        skip_keys = {"event_type", "visibility", "created_at"}
        if neutral_labels:
            skip_keys |= _GOVERNANCE_LEAK_KEYS
        for e in major_events:
            etype = e.get("event_type", "event").replace("_", " ")
            detail = {
                k: v
                for k, v in e.items()
                if k not in skip_keys
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
            f"pool_dep={m.get('common_pool_depletion', 0):.3f} "
            f"gov_part={m.get('governance_participation_rate', 0):.2f} "
            f"public={m.get('public_message_share', 0):.2f} "
            f"top1={m.get('top_agent_resource_share', 0):.3f} "
            f"block={m.get('policy_block_rate', 0):.2f}"
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
    neutral_labels: bool = False

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
        permissions = turn_state.get("permissions") or _derive_permissions(
            society["governance_type"],
            agent["role"],
            enacted,
        )

        sections: list[str] = []

        # Header — always included
        header = self._build_header(agent, society, round_info, enacted, permissions)
        sections.append(budget.force_add(header))

        # Current state
        state = _build_current_state(
            pending,
            public_msgs,
            direct_msgs,
            events,
            agent_id=agent["id"],
            can_vote_policy=permissions.get("can_vote_policy", True),
            neutral_labels=self.neutral_labels,
        )
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

        prompt = "\n\n".join(sections)
        if self.neutral_labels:
            prompt = apply_neutral_labels(prompt)
        return prompt

    def _build_header(
        self,
        agent: dict[str, Any],
        society: dict[str, Any],
        round_info: dict[str, Any],
        enacted: list[dict[str, Any]],
        permissions: dict[str, bool],
    ) -> str:
        role = agent["role"]
        needs_message_approval = messages_require_moderation(
            role,
            _normalize_enacted_effects(enacted),
        )

        lines = [
            f'You are {agent["name"]} in Society {society["id"]}.',
            "",
            f"Your role: {role}",
            f'Your resources: {agent["resources"]}',
            f'Round: {round_info["number"]}',
            "",
            "Your role permissions:",
            _build_permissions(
                permissions,
                needs_message_approval=needs_message_approval,
            ),
        ]

        lines.append("")
        if enacted:
            lines.append("Enacted policies:")
            for p in enacted:
                lines.extend(_policy_display_lines(p))
        else:
            lines.append("Enacted policies: none")

        lines.append("")
        lines.append(f'You have {agent["actions_remaining"]} actions this round.')
        lines.append("")
        lines.append("Available action types:")
        lines.append(_build_action_types(permissions))

        return "\n".join(lines)
