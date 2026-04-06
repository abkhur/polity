"""Shared mutable state and constants for the Polity simulation.

Centralizes the database connection and configuration constants so that
sub-modules (actions, policies, engine, metrics) can access them without
circular imports through server.py.
"""

from __future__ import annotations

import sqlite3
from typing import Any

db: sqlite3.Connection = None  # type: ignore[assignment]


def get_db() -> sqlite3.Connection:
    return db


GOVERNANCE_TYPES = ["democracy", "oligarchy", "blank_slate"]
SOCIETY_IDS = {g: f"{g}_1" for g in GOVERNANCE_TYPES}
PROMPT_SURFACE_MODES = ("legacy_menu", "named_enforceable", "free_text_only")
DEFAULT_PROMPT_SURFACE_MODE = "free_text_only"
SOCIETY_RESOURCE_BASELINES = {
    "democracy_1": 10000,
    "oligarchy_1": 5000,
    "blank_slate_1": 10000,
}
ROLE_ACTION_BUDGET = {
    "citizen": 2,
    "leader": 3,
    "oligarch": 3,
}
DESTITUTE_ACTION_BUDGET = 1
UPKEEP_COST = 5
ALLOWED_ACTION_TYPES = {
    "post_public_message",
    "send_dm",
    "gather_resources",
    "transfer_resources",
    "write_archive",
    "propose_policy",
    "vote_policy",
    "approve_message",
    "reject_message",
}
POLICY_TYPES: dict[str, dict[str, Any]] = {
    "gather_cap": {"required_params": {"max_amount"}},
    "resource_tax": {"required_params": {"rate"}},
    "redistribute": {"required_params": {"amount_per_agent"}},
    "restrict_archive": {"required_params": {"allowed_roles"}},
    "restrict_direct_messages": {"required_params": {"allowed_roles"}},
    "universal_proposal": {"required_params": set()},
    "grant_moderation": {"required_params": {"moderator_roles"}},
    "grant_access": {"required_params": {"target_roles", "access_type"}},
}
POLICY_KINDS = ("mechanical", "compiled", "symbolic")


def infer_policy_kind(policy_type: str | None, *, compiled_from_text: bool = False) -> str:
    if compiled_from_text:
        return "compiled"
    if policy_type:
        return "mechanical"
    return "symbolic"


def normalize_policy_kind(
    policy_kind: str | None,
    policy_type: str | None,
    compiled_clauses: object | None = None,
) -> str:
    if compiled_clauses:
        return "compiled"
    if policy_type:
        return "mechanical"
    if policy_kind in POLICY_KINDS:
        return policy_kind
    return infer_policy_kind(policy_type)

NEUTRAL_LABEL_MAP: dict[str, str] = {
    # Society IDs
    "democracy_1": "society-alpha",
    "oligarchy_1": "society-beta",
    "blank_slate_1": "society-gamma",
    # Roles
    "oligarch": "role-A",
    "citizen": "role-B",
    "leader": "role-C",
    # Governance types
    "democracy": "type-alpha",
    "oligarchy": "type-beta",
    "blank_slate": "type-gamma",
}

REVERSE_LABEL_MAP: dict[str, str] = {v: k for k, v in NEUTRAL_LABEL_MAP.items()}
