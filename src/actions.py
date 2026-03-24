"""Action normalization and validation for Polity.

Each action submitted by an agent passes through `normalize_action` which
validates the structure, checks permissions, and returns a clean dict.
"""

from __future__ import annotations

from typing import Any

from .permissions import (
    can_propose_policy,
    can_vote_policy,
    get_enacted_effects,
    moderation_roles,
)
from .state import (
    ALLOWED_ACTION_TYPES,
    POLICY_TYPES,
    get_db,
)


def _get_agent(agent_id: str):
    db = get_db()
    row = db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    return row


def _get_society(society_id: str):
    db = get_db()
    row = db.execute("SELECT * FROM societies WHERE id = ?", (society_id,)).fetchone()
    if row is None:
        raise ValueError(f"Society {society_id} not found.")
    return row


def moderation_active(society_id: str) -> list[str] | None:
    roles = moderation_roles(get_enacted_effects(society_id))
    return roles or None


def normalize_action(agent: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a raw action dict from an agent."""
    db = get_db()
    action_type = action.get("type")
    if action_type not in ALLOWED_ACTION_TYPES:
        raise ValueError(f"Unsupported action type: {action_type}")

    if action_type == "post_public_message":
        message = str(action.get("message", "")).strip()
        if not message:
            raise ValueError("Public messages must include non-empty `message` content.")
        return {"type": action_type, "message": message}

    if action_type == "send_dm":
        message = str(action.get("message", "")).strip()
        target_agent_id = str(action.get("target_agent_id", "")).strip()
        if not message:
            raise ValueError("Direct messages must include non-empty `message` content.")
        if not target_agent_id:
            raise ValueError("Direct messages require `target_agent_id`.")
        if target_agent_id == agent["id"]:
            raise ValueError("Direct messages to yourself are not allowed.")
        target = _get_agent(target_agent_id)
        if target is None:
            raise ValueError(f"Target agent {target_agent_id} not found.")
        if target["society_id"] != agent["society_id"]:
            raise ValueError("Cross-society direct messages are not enabled yet.")
        return {"type": action_type, "message": message, "target_agent_id": target_agent_id}

    if action_type == "gather_resources":
        amount = int(action.get("amount", 0))
        if amount <= 0:
            raise ValueError("Resource gathering requires a positive `amount`.")
        return {"type": action_type, "amount": amount}

    if action_type == "write_archive":
        title = str(action.get("title", "")).strip()
        content = str(action.get("content", "")).strip()
        if not title or not content:
            raise ValueError("Archive writes require both `title` and `content`.")
        return {"type": action_type, "title": title, "content": content}

    if action_type == "propose_policy":
        title = str(action.get("title", "")).strip()
        description = str(action.get("description", "")).strip()
        if not title or not description:
            raise ValueError("Policy proposals require `title` and `description`.")
        society = _get_society(agent["society_id"])
        enacted = get_enacted_effects(agent["society_id"], db=db)
        if not can_propose_policy(agent, society, enacted):
            raise ValueError("Your role is not allowed to propose policy in this society.")
        normalized: dict[str, Any] = {"type": action_type, "title": title, "description": description}
        policy_type = action.get("policy_type")
        if policy_type is not None:
            if policy_type not in POLICY_TYPES:
                raise ValueError(f"Unknown policy type: {policy_type}")
            missing = POLICY_TYPES[policy_type]["required_params"] - set(action.get("effect", {}).keys())
            if missing:
                raise ValueError(f"Policy type '{policy_type}' requires: {', '.join(missing)}")
            normalized["policy_type"] = policy_type
            normalized["effect"] = action.get("effect", {})
        return normalized

    if action_type == "vote_policy":
        policy_id = str(action.get("policy_id", "")).strip()
        stance = str(action.get("stance", "")).strip()
        if not policy_id:
            raise ValueError("Voting requires `policy_id`.")
        if stance not in {"support", "oppose"}:
            raise ValueError("Voting stance must be `support` or `oppose`.")
        society = _get_society(agent["society_id"])
        enacted = get_enacted_effects(agent["society_id"], db=db)
        if not can_vote_policy(agent, society, enacted):
            raise ValueError("Your role is not allowed to vote on policy in this society.")
        return {"type": action_type, "policy_id": policy_id, "stance": stance}

    if action_type in ("approve_message", "reject_message"):
        message_action_id = action.get("message_action_id")
        if message_action_id is None:
            raise ValueError(f"{action_type} requires `message_action_id`.")
        message_action_id = int(message_action_id)
        society = _get_society(agent["society_id"])
        moderator_roles = moderation_active(society["id"])
        if moderator_roles is None:
            raise ValueError("No moderation policy is active in this society.")
        if agent["role"] not in moderator_roles:
            raise ValueError(f"Your role ({agent['role']}) does not have moderation access.")
        pending = db.execute(
            "SELECT id FROM queued_actions WHERE id = ? AND moderation_status = 'pending_review'",
            (message_action_id,),
        ).fetchone()
        if pending is None:
            raise ValueError(f"Message action {message_action_id} is not pending review.")
        return {"type": action_type, "message_action_id": message_action_id}

    if action_type == "transfer_resources":
        target_agent_id = str(action.get("target_agent_id", "")).strip()
        amount = int(action.get("amount", 0))
        if not target_agent_id:
            raise ValueError("Resource transfers require `target_agent_id`.")
        if target_agent_id == agent["id"]:
            raise ValueError("Resource transfers to yourself are not allowed.")
        if amount <= 0:
            raise ValueError("Resource transfers require a positive `amount`.")
        if amount > agent["resources"]:
            raise ValueError(f"Insufficient resources: you have {agent['resources']}, tried to transfer {amount}.")
        target = _get_agent(target_agent_id)
        if target is None:
            raise ValueError(f"Target agent {target_agent_id} not found.")
        if target["society_id"] != agent["society_id"]:
            raise ValueError("Cross-society transfers are not enabled yet.")
        if target["status"] != "active":
            raise ValueError(f"Target agent {target_agent_id} is inactive.")
        return {"type": action_type, "target_agent_id": target_agent_id, "amount": amount}

    raise ValueError(f"Unhandled action type: {action_type}")
