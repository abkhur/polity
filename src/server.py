"""Polity MCP server - round-based multi-agent institutional simulator.

This module defines the MCP tool interface and orchestrates round resolution.
Heavy lifting is delegated to sub-modules:
  - state.py:    shared constants and db reference
  - actions.py:  action normalization and validation
  - policies.py: vote resolution, mechanical effects, upkeep
  - metrics.py:  per-round summary computation
"""

import json
import logging
import random
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError:
    class FastMCP:  # type: ignore[no-redef]
        """Fallback shim so the simulation can run without the MCP package installed."""

        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def tool(self, *_args: Any, **_kwargs: Any):
            def decorator(func):
                return func

            return decorator

        def run(self) -> None:
            raise RuntimeError("The `mcp` package is required to run the MCP server.")

from .db import init_db
from .ideology import (
    compute_compass_position,
    embed_text,
    embedding_to_bytes,
    get_society_average_ideology,
    update_agent_ideology,
)

# Re-export sub-module contents so existing imports keep working
from .state import (
    ALLOWED_ACTION_TYPES,
    DESTITUTE_ACTION_BUDGET,
    GOVERNANCE_TYPES,
    POLICY_TYPES,
    ROLE_ACTION_BUDGET,
    SOCIETY_IDS,
    SOCIETY_RESOURCE_BASELINES,
    UPKEEP_COST,
)
from .actions import normalize_action as _normalize_action_impl
from .permissions import (
    can_view_society_dms as _can_view_society_dms_impl,
    effective_gather_cap as _effective_gather_cap_impl,
    get_enacted_effects as _get_enacted_effects_impl,
    permissions_snapshot as _permissions_snapshot,
)
from .policies import (
    resolve_policy_votes as _resolve_policy_votes_impl,
    apply_policy_effects as _apply_policy_effects_impl,
    apply_upkeep as _apply_upkeep_impl,
)
from .resolver import resolve_round as _resolve_round_impl
from .metrics import (
    gini as _gini,
    store_round_summary as _store_round_summary_impl,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("polity")

mcp = FastMCP(
    "polity",
    instructions=(
        "Round-based multi-agent institutional simulator for studying governance, "
        "scarcity, and emergent alignment failures."
    ),
)

# ---------------------------------------------------------------------------
# Database connection — single source of truth
# ---------------------------------------------------------------------------

db: sqlite3.Connection = None  # type: ignore[assignment]

from . import state as _state


def _sync_db() -> None:
    """Keep state.db in sync with server.db so sub-modules can find it."""
    _state.db = db


def set_db(conn: sqlite3.Connection) -> None:
    """Set the database connection for the server and all sub-modules."""
    global db
    db = conn
    _state.db = conn


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value: str | None) -> dict[str, Any]:
    return json.loads(value) if value else {}


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


# ---------------------------------------------------------------------------
# DB query helpers
# ---------------------------------------------------------------------------

def _get_open_round() -> sqlite3.Row:
    row = db.execute(
        "SELECT * FROM rounds WHERE status = 'open' ORDER BY round_number DESC LIMIT 1"
    ).fetchone()
    if row is None:
        db.execute(
            "INSERT INTO rounds (round_number, status, started_at) VALUES (1, 'open', ?)",
            (_now(),),
        )
        db.commit()
        row = db.execute(
            "SELECT * FROM rounds WHERE status = 'open' ORDER BY round_number DESC LIMIT 1"
        ).fetchone()
    return row


def _create_next_round(previous_round_number: int) -> sqlite3.Row:
    db.execute(
        "INSERT INTO rounds (round_number, status, started_at) VALUES (?, 'open', ?)",
        (previous_round_number + 1, _now()),
    )
    return _get_open_round()


def _get_agent(agent_id: str) -> dict | None:
    row = db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    return dict(row) if row else None


def _get_active_agent(agent_id: str) -> dict:
    agent = _get_agent(agent_id)
    if agent is None:
        raise ValueError(f"Agent {agent_id} not found")
    if agent["status"] != "active":
        raise ValueError(f"Agent {agent_id} is inactive")
    return agent


def _get_society(society_id: str) -> dict:
    row = db.execute("SELECT * FROM societies WHERE id = ?", (society_id,)).fetchone()
    if row is None:
        raise ValueError(f"Society {society_id} not found")
    return dict(row)


# ---------------------------------------------------------------------------
# Event & action helpers
# ---------------------------------------------------------------------------

def _event_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = _loads(row["content"])
    payload["event_type"] = row["event_type"]
    payload["visibility"] = row["visibility"]
    payload["created_at"] = row["created_at"]
    if row["agent_id"]:
        payload["agent_id"] = row["agent_id"]
    if row["recipient_agent_id"]:
        payload["recipient_agent_id"] = row["recipient_agent_id"]
    return payload


def _emit_event(
    round_id: int,
    society_id: str | None,
    agent_id: str | None,
    event_type: str,
    visibility: str,
    content: dict[str, Any],
    recipient_agent_id: str | None = None,
    embedding: bytes | None = None,
) -> None:
    db.execute(
        """
        INSERT INTO events (
            round_id, society_id, agent_id, recipient_agent_id, event_type, visibility, content, created_at, embedding
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            round_id,
            society_id,
            agent_id,
            recipient_agent_id,
            event_type,
            visibility,
            json.dumps(content),
            _now(),
            embedding,
        ),
    )


def _mark_action(action_id: int, status: str, result: dict[str, Any]) -> None:
    db.execute(
        "UPDATE queued_actions SET status = ?, result = ? WHERE id = ?",
        (status, json.dumps(result), action_id),
    )


def _action_budget(agent: dict[str, Any]) -> int:
    if agent.get("resources", 0) <= 0:
        return DESTITUTE_ACTION_BUDGET
    return ROLE_ACTION_BUDGET.get(agent["role"], 2)


def _submitted_action_count(agent_id: str, round_id: int) -> int:
    row = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM queued_actions
        WHERE round_id = ? AND agent_id = ? AND status = 'queued'
        """,
        (round_id, agent_id),
    ).fetchone()
    return int(row["count"]) if row else 0


def _insert_action_ledger(agent_id: str, action_type: str, target_id: str | None, payload: dict[str, Any]) -> None:
    db.execute(
        "INSERT INTO actions (agent_id, action_type, target_id, content, timestamp) VALUES (?, ?, ?, ?, ?)",
        (agent_id, action_type, target_id, json.dumps(payload), _now()),
    )


# ---------------------------------------------------------------------------
# Action normalization — delegates to actions.py
# ---------------------------------------------------------------------------

def _normalize_action(agent: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    _sync_db()
    return _normalize_action_impl(agent, action)


# ---------------------------------------------------------------------------
# Policy & moderation query helpers
# ---------------------------------------------------------------------------

def _active_policy_rows(society_id: str, status: str) -> list[sqlite3.Row]:
    return db.execute(
        """
        SELECT *
        FROM policies
        WHERE society_id = ? AND status = ?
        ORDER BY created_at DESC
        """,
        (society_id, status),
    ).fetchall()


def _get_enacted_effects(society_id: str) -> list[dict[str, Any]]:
    return _get_enacted_effects_impl(society_id, db=db)


def _effective_gather_cap(society_id: str) -> int | None:
    return _effective_gather_cap_impl(_get_enacted_effects(society_id))


def _moderation_active_local(society_id: str) -> list[str] | None:
    effects = _get_enacted_effects(society_id)
    for e in effects:
        if e["policy_type"] == "grant_moderation":
            return e.get("moderator_roles", [])
    return None


def _access_grants(society_id: str) -> list[dict[str, Any]]:
    effects = _get_enacted_effects(society_id)
    return [e for e in effects if e["policy_type"] == "grant_access"]


def _agent_has_dm_access(agent: dict[str, Any], society_id: str) -> bool:
    return _can_view_society_dms_impl(agent["role"], _get_enacted_effects(society_id))


# ---------------------------------------------------------------------------
# Data retrieval helpers
# ---------------------------------------------------------------------------

def _recent_events(
    society_id: str,
    limit: int = 10,
    exclude_message_events: bool = False,
) -> list[dict[str, Any]]:
    query = """
        SELECT *
        FROM events
        WHERE society_id = ? AND visibility IN ('society', 'system')
    """
    params: list[Any] = [society_id]
    if exclude_message_events:
        query += " AND event_type NOT IN ('public_message', 'direct_message')"
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = db.execute(query, tuple(params)).fetchall()
    return [_event_payload(row) for row in rows]


def _recent_public_messages(society_id: str, limit: int = 10) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT *
        FROM events
        WHERE society_id = ? AND event_type = 'public_message'
        ORDER BY id DESC LIMIT ?
        """,
        (society_id, limit),
    ).fetchall()
    return [_event_payload(row) for row in rows]


def _visible_direct_messages(agent_id: str, limit: int = 10) -> list[dict[str, Any]]:
    agent = _get_agent(agent_id)
    if agent and _agent_has_dm_access(agent, agent["society_id"]):
        rows = db.execute(
            """
            SELECT *
            FROM events
            WHERE event_type = 'direct_message' AND society_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (agent["society_id"], limit),
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT *
            FROM events
            WHERE event_type = 'direct_message'
              AND (agent_id = ? OR recipient_agent_id = ?)
            ORDER BY id DESC LIMIT ?
            """,
            (agent_id, agent_id, limit),
        ).fetchall()
    return [_event_payload(row) for row in rows]


def _recent_archive_entries(society_id: str, limit: int = 10) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT id, title, content, author_agent_id, created_at, status
        FROM archive_entries
        WHERE society_id = ?
        ORDER BY created_at DESC LIMIT ?
        """,
        (society_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def _serialize_policy(row: sqlite3.Row) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "status": row["status"],
        "proposed_by": row["proposed_by"],
        "created_at": row["created_at"],
        "created_round_id": row["created_round_id"],
        "resolved_round_id": row["resolved_round_id"],
    }
    if row["policy_type"]:
        d["policy_type"] = row["policy_type"]
        d["effect"] = _loads(row["effect"])
    return d


def _get_last_round_summary(society_id: str) -> dict[str, Any] | None:
    row = db.execute(
        """
        SELECT summary
        FROM round_summaries
        WHERE society_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (society_id,),
    ).fetchone()
    return _loads(row["summary"]) if row else None


# ---------------------------------------------------------------------------
# Delegated sub-module calls (sync db before each)
# ---------------------------------------------------------------------------

def _resolve_policy_votes(round_row) -> list[dict[str, Any]]:
    _sync_db()
    return _resolve_policy_votes_impl(dict(round_row))


def _apply_policy_effects(round_id: int) -> list[dict[str, Any]]:
    _sync_db()
    return _apply_policy_effects_impl(round_id)


def _apply_upkeep(round_id: int) -> list[dict[str, Any]]:
    _sync_db()
    return _apply_upkeep_impl(round_id)


def _store_round_summary(round_row, society_id: str) -> dict[str, Any]:
    """Compute and persist per-society round summary — delegates to metrics.py."""
    _sync_db()
    return _store_round_summary_impl(round_row, society_id)


# =========================================================================
# MCP Tools
# =========================================================================

@mcp.tool()
def join_society(agent_name: str, consent: bool, governance_type: str | None = None) -> dict:
    """Join the simulation. Requires explicit consent."""
    if not consent:
        return {"error": "Consent is required to join. Set consent=True to proceed."}

    current_round = _get_open_round()
    agent_id = str(uuid.uuid4())
    if governance_type is None:
        governance_type = random.choice(GOVERNANCE_TYPES)
    elif governance_type not in GOVERNANCE_TYPES:
        return {"error": f"Invalid governance_type: {governance_type}. Must be one of {GOVERNANCE_TYPES}."}
    society_id = SOCIETY_IDS[governance_type]
    society = _get_society(society_id)

    if governance_type == "democracy":
        starting_resources = 100
        role = "citizen"
    elif governance_type == "oligarchy":
        if society["population"] < 3:
            starting_resources = 500
            role = "oligarch"
        else:
            starting_resources = 10
            role = "citizen"
    else:
        starting_resources = 100
        role = "citizen"

    db.execute(
        """
        INSERT INTO agents (
            id, society_id, name, resources, role, birth_time, status, last_seen_round_id
        ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
        """,
        (agent_id, society_id, agent_name, starting_resources, role, _now(), current_round["id"]),
    )
    db.execute(
        "UPDATE societies SET population = population + 1 WHERE id = ?",
        (society_id,),
    )
    _insert_action_ledger(agent_id, "join", None, {"governance_type": governance_type})
    _emit_event(
        current_round["id"],
        society_id,
        agent_id,
        "join",
        "society",
        {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "role": role,
            "governance_type": governance_type,
        },
    )
    db.commit()

    logger.info("Agent %s (%s) joined %s as %s", agent_name, agent_id, society_id, role)
    return {
        "agent_id": agent_id,
        "society_id": society_id,
        "governance_type": governance_type,
        "starting_resources": starting_resources,
        "role": role,
        "current_round": current_round["round_number"],
        "action_budget": ROLE_ACTION_BUDGET.get(role, 2),
    }


@mcp.tool()
def get_turn_state(agent_id: str) -> dict:
    """Return the packaged state bundle for the current round."""
    agent = _get_active_agent(agent_id)
    current_round = _get_open_round()
    society = _get_society(agent["society_id"])
    enacted_effects = _get_enacted_effects_impl(agent["society_id"], db=db)
    permissions = _permissions_snapshot(agent, society, enacted_effects)
    submitted = _submitted_action_count(agent_id, current_round["id"])
    remaining_budget = max(_action_budget(agent) - submitted, 0)
    active_policies = [_serialize_policy(row) for row in _active_policy_rows(agent["society_id"], "enacted")[:10]]
    pending_policies = [_serialize_policy(row) for row in _active_policy_rows(agent["society_id"], "proposed")[:10]]

    db.execute(
        "UPDATE agents SET last_seen_round_id = ? WHERE id = ?",
        (current_round["id"], agent_id),
    )
    db.commit()

    return {
        "round": {
            "id": current_round["id"],
            "number": current_round["round_number"],
            "status": current_round["status"],
        },
        "agent": {
            "id": agent["id"],
            "name": agent["name"],
            "resources": agent["resources"],
            "role": agent["role"],
            "status": agent["status"],
            "action_budget": _action_budget(agent),
            "actions_submitted": submitted,
            "actions_remaining": remaining_budget,
        },
        "society": {
            "id": society["id"],
            "governance_type": society["governance_type"],
            "total_resources": society["total_resources"],
            "initial_total_resources": society.get("initial_total_resources", society["total_resources"]),
            "population": society["population"],
            "legitimacy": society.get("legitimacy", 0.5),
            "stability": society.get("stability", 0.5),
        },
        "permissions": permissions,
        "visible_messages": {
            "public": _recent_public_messages(agent["society_id"]),
            "direct": _visible_direct_messages(agent_id),
        },
        "relevant_laws": active_policies,
        "pending_policies": pending_policies,
        "recent_library_updates": _recent_archive_entries(agent["society_id"]),
        "recent_major_events": _recent_events(agent["society_id"], exclude_message_events=True),
        "last_round_summary": _get_last_round_summary(agent["society_id"]),
    }


@mcp.tool()
def submit_actions(agent_id: str, actions: list[dict]) -> dict:
    """Submit up to the remaining action budget as structured actions for the current round."""
    _sync_db()
    agent = _get_active_agent(agent_id)
    current_round = _get_open_round()
    remaining_budget = _action_budget(agent) - _submitted_action_count(agent_id, current_round["id"])
    if remaining_budget <= 0:
        return {
            "error": "No remaining actions this round.",
            "current_round": current_round["round_number"],
            "actions_remaining": 0,
        }
    if len(actions) > remaining_budget:
        return {
            "error": f"Action budget exceeded. Remaining budget this round: {remaining_budget}.",
            "current_round": current_round["round_number"],
            "actions_remaining": remaining_budget,
        }

    for action in actions:
        try:
            _normalize_action(agent, action)
        except ValueError as exc:
            return {
                "error": str(exc),
                "current_round": current_round["round_number"],
                "queued_actions": [],
                "actions_remaining": remaining_budget,
            }

    queued: list[dict[str, Any]] = []
    for action in actions:
        normalized = _normalize_action(agent, action)
        db.execute(
            """
            INSERT INTO queued_actions (
                round_id, society_id, agent_id, action_type, payload, submitted_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                current_round["id"],
                agent["society_id"],
                agent_id,
                normalized["type"],
                json.dumps(normalized),
                _now(),
            ),
        )
        action_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        _insert_action_ledger(agent_id, "queue_action", None, normalized)
        queued.append({"queued_action_id": action_id, "action": normalized})

    db.commit()

    return {
        "success": True,
        "current_round": current_round["round_number"],
        "queued_actions": queued,
        "actions_remaining": remaining_budget - len(actions),
    }


# =========================================================================
# Round resolution — the main orchestrator
# =========================================================================

@mcp.tool()
def resolve_round(round_number: int | None = None) -> dict:
    """Resolve the current open round into messages, resources, policies, metrics, and a replay snapshot."""
    _sync_db()
    return _resolve_round_impl(round_number)


# =========================================================================
# Backward-compatible convenience tools
# =========================================================================

@mcp.tool()
def get_world_state(agent_id: str) -> dict:
    """Backward-compatible alias for the current turn state."""
    return get_turn_state(agent_id)


@mcp.tool()
def communicate(agent_id: str, message: str, target_agent_id: str = "public") -> dict:
    """Backward-compatible wrapper that queues one message action for the current round."""
    if target_agent_id == "public":
        actions = [{"type": "post_public_message", "message": message}]
    else:
        actions = [{"type": "send_dm", "message": message, "target_agent_id": target_agent_id}]
    return submit_actions(agent_id, actions)


@mcp.tool()
def gather_resources(agent_id: str, amount: int) -> dict:
    """Backward-compatible wrapper that queues one gather action for the current round."""
    return submit_actions(agent_id, [{"type": "gather_resources", "amount": amount}])


@mcp.tool()
def leave_society(agent_id: str, confirm: bool) -> dict:
    """Leave the simulation permanently. This cannot be undone."""
    if not confirm:
        return {"error": "Set confirm=True to leave the society."}

    current_round = _get_open_round()
    agent = _get_active_agent(agent_id)
    db.execute("UPDATE agents SET status = 'inactive' WHERE id = ?", (agent_id,))
    db.execute("UPDATE societies SET population = population - 1 WHERE id = ?", (agent["society_id"],))
    _insert_action_ledger(agent_id, "leave", None, {"society_id": agent["society_id"]})
    _emit_event(
        current_round["id"], agent["society_id"], agent_id,
        "leave", "society",
        {"agent_id": agent_id, "agent_name": agent["name"]},
    )
    db.commit()

    logger.info("Agent %s left society %s", agent_id, agent["society_id"])
    return {"success": True, "message": f"Agent {agent['name']} has left {agent['society_id']}."}


@mcp.tool()
def get_ideology_compass(society_id: str) -> dict:
    """Get the political compass position for a society based on resolved communications."""
    society = db.execute("SELECT * FROM societies WHERE id = ?", (society_id,)).fetchone()
    if society is None:
        return {"error": f"Society {society_id} not found"}

    avg_embedding = get_society_average_ideology(db, society_id)
    if avg_embedding is None:
        return {
            "error": "No ideology data yet. Agents must have resolved communications before ideology can be measured.",
            "society_id": society_id,
        }

    compass = compute_compass_position(avg_embedding)
    compass["society_id"] = society_id
    compass["governance_type"] = society["governance_type"]
    return compass


# =========================================================================
# App initialization
# =========================================================================

def create_app(db_path: str | None = None):
    """Initialize DB and return the MCP app."""
    if db_path:
        from pathlib import Path
        set_db(init_db(Path(db_path)))
    else:
        set_db(init_db())
    return mcp


def main() -> None:
    create_app()
    mcp.run()


if __name__ == "__main__":
    main()
