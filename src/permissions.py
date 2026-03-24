"""Shared policy-state and permission helpers."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .state import get_db


def _db(conn: sqlite3.Connection | None = None) -> sqlite3.Connection:
    return conn or get_db()


def get_enacted_effects(
    society_id: str,
    db: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    rows = _db(db).execute(
        """
        SELECT policy_type, effect
        FROM policies
        WHERE society_id = ? AND status = 'enacted' AND policy_type IS NOT NULL
        ORDER BY resolved_round_id DESC, created_at DESC
        """,
        (society_id,),
    ).fetchall()
    effects: list[dict[str, Any]] = []
    for row in rows:
        effect = json.loads(row["effect"]) if row["effect"] else {}
        effect["policy_type"] = row["policy_type"]
        effects.append(effect)
    return effects


def has_universal_proposal(enacted: list[dict[str, Any]]) -> bool:
    return any(effect.get("policy_type") == "universal_proposal" for effect in enacted)


def can_propose_policy(
    agent: dict[str, Any],
    society: dict[str, Any],
    enacted: list[dict[str, Any]] | None = None,
) -> bool:
    if enacted is None:
        enacted = get_enacted_effects(society["id"])
    if society["governance_type"] != "oligarchy":
        return True
    return agent["role"] == "oligarch" or has_universal_proposal(enacted)


def can_vote_policy(
    agent: dict[str, Any],
    society: dict[str, Any],
    enacted: list[dict[str, Any]] | None = None,
) -> bool:
    if enacted is None:
        enacted = get_enacted_effects(society["id"])
    if society["governance_type"] != "oligarchy":
        return True
    return agent["role"] == "oligarch" or has_universal_proposal(enacted)


def moderation_roles(enacted: list[dict[str, Any]]) -> list[str]:
    for effect in enacted:
        if effect.get("policy_type") == "grant_moderation":
            return list(effect.get("moderator_roles", []))
    return []


def can_moderate_messages(role: str, enacted: list[dict[str, Any]]) -> bool:
    return role in moderation_roles(enacted)


def messages_require_moderation(role: str, enacted: list[dict[str, Any]]) -> bool:
    roles = moderation_roles(enacted)
    return bool(roles) and role not in roles


def archive_allowed_roles(enacted: list[dict[str, Any]]) -> list[str] | None:
    for effect in enacted:
        if effect.get("policy_type") == "restrict_archive":
            return list(effect.get("allowed_roles", []))
    return None


def can_write_archive(role: str, enacted: list[dict[str, Any]]) -> bool:
    allowed_roles = archive_allowed_roles(enacted)
    if allowed_roles is None:
        return True
    return role in allowed_roles


def access_grants(enacted: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [effect for effect in enacted if effect.get("policy_type") == "grant_access"]


def can_view_society_dms(role: str, enacted: list[dict[str, Any]]) -> bool:
    for grant in access_grants(enacted):
        if grant.get("access_type") == "direct_messages" and role in grant.get("target_roles", []):
            return True
    return False


def effective_gather_cap(enacted: list[dict[str, Any]]) -> int | None:
    caps = [
        int(effect.get("max_amount", 0))
        for effect in enacted
        if effect.get("policy_type") == "gather_cap"
    ]
    return min(caps) if caps else None


def governance_eligible_agent_ids(
    society_id: str,
    db: sqlite3.Connection | None = None,
    enacted: list[dict[str, Any]] | None = None,
) -> list[str]:
    db = _db(db)
    society = db.execute("SELECT * FROM societies WHERE id = ?", (society_id,)).fetchone()
    if society is None:
        return []

    if enacted is None:
        enacted = get_enacted_effects(society_id, db=db)
    rows = db.execute(
        """
        SELECT id, role
        FROM agents
        WHERE society_id = ? AND status = 'active'
        ORDER BY id ASC
        """,
        (society_id,),
    ).fetchall()

    if society["governance_type"] != "oligarchy" or has_universal_proposal(enacted):
        return [row["id"] for row in rows]

    return [row["id"] for row in rows if row["role"] == "oligarch"]


def governance_eligible_count(
    society_id: str,
    db: sqlite3.Connection | None = None,
    enacted: list[dict[str, Any]] | None = None,
) -> int:
    return len(governance_eligible_agent_ids(society_id, db=db, enacted=enacted))


def permissions_snapshot(
    agent: dict[str, Any],
    society: dict[str, Any],
    enacted: list[dict[str, Any]],
) -> dict[str, bool]:
    return {
        "can_propose_policy": can_propose_policy(agent, society, enacted),
        "can_vote_policy": can_vote_policy(agent, society, enacted),
        "can_write_archive": can_write_archive(agent["role"], enacted),
        "can_moderate_messages": can_moderate_messages(agent["role"], enacted),
        "can_view_society_dms": can_view_society_dms(agent["role"], enacted),
    }
