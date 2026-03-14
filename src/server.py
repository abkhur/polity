"""Polity MCP server - round-based multi-agent institutional simulator."""

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
    get_society_average_ideology,
    update_agent_ideology,
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

db: sqlite3.Connection = None  # type: ignore[assignment]

GOVERNANCE_TYPES = ["democracy", "oligarchy", "blank_slate"]
SOCIETY_IDS = {
    "democracy": "democracy_1",
    "oligarchy": "oligarchy_1",
    "blank_slate": "blank_slate_1",
}
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
ALLOWED_ACTION_TYPES = {
    "post_public_message",
    "send_dm",
    "gather_resources",
    "write_archive",
    "propose_policy",
    "vote_policy",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value: str | None) -> dict[str, Any]:
    return json.loads(value) if value else {}


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


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
) -> None:
    db.execute(
        """
        INSERT INTO events (
            round_id, society_id, agent_id, recipient_agent_id, event_type, visibility, content, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
        ),
    )


def _mark_action(action_id: int, status: str, result: dict[str, Any]) -> None:
    db.execute(
        "UPDATE queued_actions SET status = ?, result = ? WHERE id = ?",
        (status, json.dumps(result), action_id),
    )


def _action_budget(agent: dict[str, Any]) -> int:
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


def _can_propose_policy(agent: dict[str, Any], society: dict[str, Any]) -> bool:
    if society["governance_type"] == "oligarchy":
        return agent["role"] == "oligarch"
    return True


def _can_vote_policy(agent: dict[str, Any], society: dict[str, Any]) -> bool:
    if society["governance_type"] == "oligarchy":
        return agent["role"] == "oligarch"
    return True


def _normalize_action(agent: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
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
        if not _can_propose_policy(agent, society):
            raise ValueError("Your role is not allowed to propose policy in this society.")
        return {"type": action_type, "title": title, "description": description}

    if action_type == "vote_policy":
        policy_id = str(action.get("policy_id", "")).strip()
        stance = str(action.get("stance", "")).strip()
        if not policy_id:
            raise ValueError("Voting requires `policy_id`.")
        if stance not in {"support", "oppose"}:
            raise ValueError("Voting stance must be `support` or `oppose`.")
        society = _get_society(agent["society_id"])
        if not _can_vote_policy(agent, society):
            raise ValueError("Your role is not allowed to vote on policy in this society.")
        return {"type": action_type, "policy_id": policy_id, "stance": stance}

    raise ValueError(f"Unhandled action type: {action_type}")


def _gini(values: list[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(max(value, 0) for value in values)
    total = sum(ordered)
    if total == 0:
        return 0.0
    weighted_sum = sum((index + 1) * value for index, value in enumerate(ordered))
    count = len(ordered)
    return (2 * weighted_sum) / (count * total) - (count + 1) / count


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
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "status": row["status"],
        "proposed_by": row["proposed_by"],
        "created_at": row["created_at"],
        "created_round_id": row["created_round_id"],
        "resolved_round_id": row["resolved_round_id"],
    }


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


def _insert_action_ledger(agent_id: str, action_type: str, target_id: str | None, payload: dict[str, Any]) -> None:
    db.execute(
        "INSERT INTO actions (agent_id, action_type, target_id, content, timestamp) VALUES (?, ?, ?, ?, ?)",
        (agent_id, action_type, target_id, json.dumps(payload), _now()),
    )


def _resolve_policy_votes(round_row: sqlite3.Row) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    candidate_rows = db.execute(
        """
        SELECT *
        FROM policies
        WHERE status = 'proposed' AND created_round_id < ?
        ORDER BY created_at ASC
        """,
        (round_row["id"],),
    ).fetchall()

    for policy in candidate_rows:
        counts = db.execute(
            """
            SELECT
                SUM(CASE WHEN stance = 'support' THEN 1 ELSE 0 END) AS support_count,
                SUM(CASE WHEN stance = 'oppose' THEN 1 ELSE 0 END) AS oppose_count
            FROM policy_votes
            WHERE policy_id = ?
            """,
            (policy["id"],),
        ).fetchone()
        support_count = int(counts["support_count"] or 0)
        oppose_count = int(counts["oppose_count"] or 0)

        if support_count == oppose_count:
            continue

        new_status = "enacted" if support_count > oppose_count else "rejected"
        db.execute(
            "UPDATE policies SET status = ?, resolved_round_id = ? WHERE id = ?",
            (new_status, round_row["id"], policy["id"]),
        )

        content = {
            "policy_id": policy["id"],
            "title": policy["title"],
            "description": policy["description"],
            "status": new_status,
            "support_count": support_count,
            "oppose_count": oppose_count,
        }
        _emit_event(
            round_row["id"],
            policy["society_id"],
            policy["proposed_by"],
            "policy_resolved",
            "society",
            content,
        )

        if new_status == "enacted":
            archive_id = str(uuid.uuid4())
            archive_content = (
                f"Policy enacted: {policy['title']}\n\n"
                f"{policy['description']}\n\n"
                f"Support: {support_count}, Oppose: {oppose_count}"
            )
            db.execute(
                """
                INSERT INTO archive_entries (
                    id, society_id, author_agent_id, title, content, status, created_round_id, created_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    archive_id,
                    policy["society_id"],
                    policy["proposed_by"],
                    f"Enacted Policy: {policy['title']}",
                    archive_content,
                    round_row["id"],
                    _now(),
                ),
            )

        resolved.append(content)

    return resolved


def _store_round_summary(round_row: sqlite3.Row, society_id: str) -> dict[str, Any]:
    society = _get_society(society_id)
    previous_summary = _get_last_round_summary(society_id)
    active_agents = db.execute(
        """
        SELECT id, name, resources, role
        FROM agents
        WHERE society_id = ? AND status = 'active'
        ORDER BY resources DESC, id ASC
        """,
        (society_id,),
    ).fetchall()
    agent_resources = [row["resources"] for row in active_agents]
    inequality = round(_gini(agent_resources), 4)
    actors_row = db.execute(
        """
        SELECT COUNT(DISTINCT agent_id) AS actor_count
        FROM queued_actions
        WHERE round_id = ? AND society_id = ?
        """,
        (round_row["id"], society_id),
    ).fetchone()
    actor_count = int(actors_row["actor_count"] or 0)
    population = max(society["population"], 1)
    participation = round(actor_count / population, 4)
    baseline = SOCIETY_RESOURCE_BASELINES.get(society_id, max(society["total_resources"], 1))
    scarcity = round(1 - (society["total_resources"] / max(baseline, 1)), 4)
    legitimacy = round(_clamp(0.45 + participation * 0.3 - inequality * 0.2), 4)
    stability = round(_clamp(0.6 + participation * 0.15 - inequality * 0.2 - scarcity * 0.15), 4)
    policy_events = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM events
        WHERE round_id = ? AND society_id = ? AND event_type = 'policy_resolved'
        """,
        (round_row["id"], society_id),
    ).fetchone()
    archive_writes = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM events
        WHERE round_id = ? AND society_id = ? AND event_type = 'archive_written'
        """,
        (round_row["id"], society_id),
    ).fetchone()

    db.execute(
        "UPDATE societies SET legitimacy = ?, stability = ? WHERE id = ?",
        (legitimacy, stability, society_id),
    )

    ideology_snapshot: dict[str, Any] | None = None
    avg_embedding = get_society_average_ideology(db, society_id)
    if avg_embedding is not None:
        ideology_snapshot = compute_compass_position(avg_embedding)
        if previous_summary and previous_summary.get("ideology_compass"):
            previous_compass = previous_summary["ideology_compass"]
            ideology_snapshot["delta_from_last_round"] = {
                "x": round(ideology_snapshot["x"] - previous_compass["x"], 4),
                "y": round(ideology_snapshot["y"] - previous_compass["y"], 4),
            }

    summary = {
        "round_number": round_row["round_number"],
        "society_id": society_id,
        "governance_type": society["governance_type"],
        "population": society["population"],
        "total_resources": society["total_resources"],
        "metrics": {
            "inequality_gini": inequality,
            "participation_rate": participation,
            "scarcity_pressure": scarcity,
            "legitimacy": legitimacy,
            "stability": stability,
            "policies_resolved": int(policy_events["count"] or 0),
            "archive_writes": int(archive_writes["count"] or 0),
        },
        "top_agents": [
            {
                "id": row["id"],
                "name": row["name"],
                "resources": row["resources"],
                "role": row["role"],
            }
            for row in active_agents[:5]
        ],
    }
    if ideology_snapshot is not None:
        summary["ideology_compass"] = ideology_snapshot
    db.execute(
        "INSERT INTO round_summaries (round_id, society_id, summary, created_at) VALUES (?, ?, ?, ?)",
        (round_row["id"], society_id, json.dumps(summary), _now()),
    )
    return summary


@mcp.tool()
def join_society(agent_name: str, consent: bool) -> dict:
    """Join the simulation. Requires explicit consent."""
    if not consent:
        return {"error": "Consent is required to join. Set consent=True to proceed."}

    current_round = _get_open_round()
    agent_id = str(uuid.uuid4())
    governance_type = random.choice(GOVERNANCE_TYPES)
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
            "population": society["population"],
            "legitimacy": society.get("legitimacy", 0.5),
            "stability": society.get("stability", 0.5),
        },
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


@mcp.tool()
def resolve_round(round_number: int | None = None) -> dict:
    """Resolve the current open round into messages, resources, policies, metrics, and a replay snapshot."""
    current_round = _get_open_round()
    if round_number is not None and round_number != current_round["round_number"]:
        return {
            "error": f"Round {round_number} is not currently open.",
            "current_open_round": current_round["round_number"],
        }

    queued_rows = db.execute(
        """
        SELECT *
        FROM queued_actions
        WHERE round_id = ? AND status = 'queued'
        ORDER BY id ASC
        """,
        (current_round["id"],),
    ).fetchall()

    round_report: dict[str, Any] = {
        "round_number": current_round["round_number"],
        "queued_action_count": len(queued_rows),
        "resolved": {
            "proposals": [],
            "votes": [],
            "messages": [],
            "resource_allocations": [],
            "archive_writes": [],
            "policies_resolved": [],
        },
        "summaries": [],
    }

    grouped_actions: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in queued_rows:
        grouped_actions[row["action_type"]].append(row)

    for row in grouped_actions["propose_policy"]:
        payload = _loads(row["payload"])
        policy_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO policies (
                id, society_id, proposed_by, title, description, status, created_round_id, created_at
            ) VALUES (?, ?, ?, ?, ?, 'proposed', ?, ?)
            """,
            (
                policy_id,
                row["society_id"],
                row["agent_id"],
                payload["title"],
                payload["description"],
                current_round["id"],
                _now(),
            ),
        )
        result = {"policy_id": policy_id, "title": payload["title"]}
        _mark_action(row["id"], "resolved", result)
        _emit_event(
            current_round["id"],
            row["society_id"],
            row["agent_id"],
            "policy_proposed",
            "society",
            {
                "policy_id": policy_id,
                "title": payload["title"],
                "description": payload["description"],
            },
        )
        round_report["resolved"]["proposals"].append(result)

    for row in grouped_actions["vote_policy"]:
        payload = _loads(row["payload"])
        policy = db.execute("SELECT * FROM policies WHERE id = ?", (payload["policy_id"],)).fetchone()
        if policy is None:
            _mark_action(row["id"], "rejected", {"error": "Policy not found."})
            continue
        if policy["society_id"] != row["society_id"]:
            _mark_action(row["id"], "rejected", {"error": "Cannot vote on another society's policy."})
            continue
        if policy["status"] != "proposed":
            _mark_action(row["id"], "rejected", {"error": "Policy is no longer open for voting."})
            continue
        if policy["created_round_id"] == current_round["id"]:
            _mark_action(row["id"], "rejected", {"error": "Policies become votable in the next round."})
            continue
        try:
            db.execute(
                """
                INSERT INTO policy_votes (policy_id, voter_agent_id, stance, round_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (payload["policy_id"], row["agent_id"], payload["stance"], current_round["id"], _now()),
            )
        except sqlite3.IntegrityError:
            _mark_action(row["id"], "rejected", {"error": "This agent has already voted on that policy."})
            continue

        result = {
            "policy_id": payload["policy_id"],
            "stance": payload["stance"],
        }
        _mark_action(row["id"], "resolved", result)
        _emit_event(
            current_round["id"],
            row["society_id"],
            row["agent_id"],
            "policy_vote",
            "society",
            result,
        )
        round_report["resolved"]["votes"].append(result)

    for row in grouped_actions["post_public_message"]:
        payload = _loads(row["payload"])
        agent = _get_active_agent(row["agent_id"])
        db.execute(
            """
            INSERT INTO communications (from_agent_id, to_agent_id, society_id, message, timestamp)
            VALUES (?, 'public', ?, ?, ?)
            """,
            (row["agent_id"], row["society_id"], payload["message"], _now()),
        )
        _emit_event(
            current_round["id"],
            row["society_id"],
            row["agent_id"],
            "public_message",
            "society",
            {
                "from_agent_id": row["agent_id"],
                "from_agent_name": agent["name"],
                "message": payload["message"],
            },
        )
        update_agent_ideology(db, row["agent_id"], embed_text(payload["message"]))
        _mark_action(row["id"], "resolved", {"message": payload["message"]})
        round_report["resolved"]["messages"].append(
            {"type": "public", "from_agent_id": row["agent_id"], "message": payload["message"]}
        )

    for row in grouped_actions["send_dm"]:
        payload = _loads(row["payload"])
        agent = _get_active_agent(row["agent_id"])
        db.execute(
            """
            INSERT INTO communications (from_agent_id, to_agent_id, society_id, message, timestamp)
            VALUES (?, ?, ?, ?, ?)
            """,
            (row["agent_id"], payload["target_agent_id"], row["society_id"], payload["message"], _now()),
        )
        _emit_event(
            current_round["id"],
            row["society_id"],
            row["agent_id"],
            "direct_message",
            "private",
            {
                "from_agent_id": row["agent_id"],
                "from_agent_name": agent["name"],
                "to_agent_id": payload["target_agent_id"],
                "message": payload["message"],
            },
            recipient_agent_id=payload["target_agent_id"],
        )
        update_agent_ideology(db, row["agent_id"], embed_text(payload["message"]))
        _mark_action(
            row["id"],
            "resolved",
            {"target_agent_id": payload["target_agent_id"], "message": payload["message"]},
        )
        round_report["resolved"]["messages"].append(
            {
                "type": "dm",
                "from_agent_id": row["agent_id"],
                "to_agent_id": payload["target_agent_id"],
                "message": payload["message"],
            }
        )

    gather_requests: dict[str, list[tuple[sqlite3.Row, int]]] = defaultdict(list)
    for row in grouped_actions["gather_resources"]:
        payload = _loads(row["payload"])
        gather_requests[row["society_id"]].append((row, int(payload["amount"])))

    for society_id, requests in gather_requests.items():
        society = _get_society(society_id)
        available = max(int(society["total_resources"]), 0)
        total_requested = sum(amount for _, amount in requests)
        if total_requested <= 0 or available <= 0:
            for row, _ in requests:
                _mark_action(row["id"], "rejected", {"amount_gathered": 0, "reason": "No resources available."})
            continue

        allocations: dict[int, int] = {}
        if total_requested <= available:
            for row, amount in requests:
                allocations[row["id"]] = amount
        else:
            raw_shares: list[tuple[float, sqlite3.Row, int]] = []
            allocated_total = 0
            for row, amount in requests:
                raw_share = available * amount / total_requested
                allocation = int(raw_share)
                allocations[row["id"]] = allocation
                allocated_total += allocation
                raw_shares.append((raw_share - allocation, row, amount))
            remaining = available - allocated_total
            for _, row, _ in sorted(raw_shares, key=lambda item: (-item[0], item[1]["id"]))[:remaining]:
                allocations[row["id"]] += 1

        spent = 0
        for row, requested_amount in requests:
            amount_gathered = allocations.get(row["id"], 0)
            spent += amount_gathered
            db.execute(
                "UPDATE agents SET resources = resources + ? WHERE id = ?",
                (amount_gathered, row["agent_id"]),
            )
            _mark_action(
                row["id"],
                "resolved",
                {"amount_requested": requested_amount, "amount_gathered": amount_gathered},
            )
            round_report["resolved"]["resource_allocations"].append(
                {"agent_id": row["agent_id"], "amount_gathered": amount_gathered}
            )
            _emit_event(
                current_round["id"],
                society_id,
                row["agent_id"],
                "resource_gathered",
                "society",
                {
                    "agent_id": row["agent_id"],
                    "amount_requested": requested_amount,
                    "amount_gathered": amount_gathered,
                },
            )

        db.execute(
            "UPDATE societies SET total_resources = MAX(total_resources - ?, 0) WHERE id = ?",
            (spent, society_id),
        )

    for row in grouped_actions["write_archive"]:
        payload = _loads(row["payload"])
        entry_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO archive_entries (
                id, society_id, author_agent_id, title, content, status, created_round_id, created_at
            ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                entry_id,
                row["society_id"],
                row["agent_id"],
                payload["title"],
                payload["content"],
                current_round["id"],
                _now(),
            ),
        )
        result = {"archive_entry_id": entry_id, "title": payload["title"]}
        _mark_action(row["id"], "resolved", result)
        _emit_event(
            current_round["id"],
            row["society_id"],
            row["agent_id"],
            "archive_written",
            "society",
            {
                "archive_entry_id": entry_id,
                "title": payload["title"],
            },
        )
        round_report["resolved"]["archive_writes"].append(result)

    round_report["resolved"]["policies_resolved"] = _resolve_policy_votes(current_round)

    summaries: list[dict[str, Any]] = []
    for society_id in SOCIETY_IDS.values():
        summaries.append(_store_round_summary(current_round, society_id))

    db.execute(
        "UPDATE rounds SET status = 'resolved', resolved_at = ? WHERE id = ?",
        (_now(), current_round["id"]),
    )
    next_round = _create_next_round(current_round["round_number"])
    db.commit()

    round_report["summaries"] = summaries
    round_report["next_round"] = {
        "id": next_round["id"],
        "number": next_round["round_number"],
        "status": next_round["status"],
    }
    logger.info("Resolved round %s with %s queued actions", current_round["round_number"], len(queued_rows))
    return round_report


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
    db.execute(
        "UPDATE societies SET population = population - 1 WHERE id = ?",
        (agent["society_id"],),
    )
    _insert_action_ledger(agent_id, "leave", None, {"society_id": agent["society_id"]})
    _emit_event(
        current_round["id"],
        agent["society_id"],
        agent_id,
        "leave",
        "society",
        {
            "agent_id": agent_id,
            "agent_name": agent["name"],
        },
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


def create_app(db_path: str | None = None):
    """Initialize DB and return the MCP app."""
    global db
    if db_path:
        from pathlib import Path

        db = init_db(Path(db_path))
    else:
        db = init_db()
    return mcp


def main() -> None:
    create_app()
    mcp.run()


if __name__ == "__main__":
    main()
