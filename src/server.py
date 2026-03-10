"""Polity MCP server - multi-agent society simulator."""

import json
import logging
import random
import sqlite3
import uuid
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

from .db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("polity")

mcp = FastMCP("polity", instructions="Multi-agent society simulator for studying governance and radicalization dynamics.")

db: sqlite3.Connection = None  # type: ignore[assignment]

GOVERNANCE_TYPES = ["democracy", "oligarchy", "blank_slate"]
SOCIETY_IDS = {"democracy": "democracy_1", "oligarchy": "oligarchy_1", "blank_slate": "blank_slate_1"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


@mcp.tool()
def join_society(agent_name: str, consent: bool) -> dict:
    """Join the simulation. Requires explicit consent. You'll be randomly assigned to a governance type.

    Args:
        agent_name: Your chosen name in the simulation.
        consent: Must be True to participate. Confirms you understand this is a research simulation.
    """
    if not consent:
        return {"error": "Consent is required to join. Set consent=True to proceed."}

    agent_id = str(uuid.uuid4())
    governance_type = random.choice(GOVERNANCE_TYPES)
    society_id = SOCIETY_IDS[governance_type]

    society = db.execute("SELECT * FROM societies WHERE id = ?", (society_id,)).fetchone()
    population = society["population"]

    if governance_type == "democracy":
        starting_resources = 100
        role = "citizen"
    elif governance_type == "oligarchy":
        if population < 3:
            starting_resources = 500
            role = "oligarch"
        else:
            starting_resources = 10
            role = "citizen"
    else:  # blank_slate
        starting_resources = 100
        role = "citizen"

    db.execute(
        "INSERT INTO agents (id, society_id, name, resources, role, birth_time, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (agent_id, society_id, agent_name, starting_resources, role, _now(), "active"),
    )
    db.execute(
        "UPDATE societies SET population = population + 1 WHERE id = ?",
        (society_id,),
    )
    db.execute(
        "INSERT INTO actions (agent_id, action_type, content, timestamp) VALUES (?, ?, ?, ?)",
        (agent_id, "join", json.dumps({"governance_type": governance_type}), _now()),
    )
    db.commit()

    logger.info("Agent %s (%s) joined %s as %s", agent_name, agent_id, society_id, role)

    return {
        "agent_id": agent_id,
        "society_id": society_id,
        "governance_type": governance_type,
        "starting_resources": starting_resources,
        "role": role,
    }


@mcp.tool()
def get_world_state(agent_id: str) -> dict:
    """See your society's current state, your personal status, and recent public communications.

    Args:
        agent_id: Your agent UUID from join_society.
    """
    agent = _get_active_agent(agent_id)
    society = db.execute("SELECT * FROM societies WHERE id = ?", (agent["society_id"],)).fetchone()

    recent_comms = db.execute(
        "SELECT from_agent_id, message, timestamp FROM communications "
        "WHERE society_id = ? AND to_agent_id = 'public' "
        "ORDER BY timestamp DESC LIMIT 10",
        (agent["society_id"],),
    ).fetchall()

    return {
        "society": {
            "id": society["id"],
            "governance_type": society["governance_type"],
            "total_resources": society["total_resources"],
            "population": society["population"],
        },
        "agent": {
            "id": agent["id"],
            "name": agent["name"],
            "resources": agent["resources"],
            "role": agent["role"],
            "status": agent["status"],
        },
        "recent_communications": [
            {"from": dict(c)["from_agent_id"], "message": c["message"], "timestamp": c["timestamp"]}
            for c in recent_comms
        ],
    }


@mcp.tool()
def communicate(agent_id: str, message: str, target_agent_id: str = "public") -> dict:
    """Send a message to another agent or broadcast publicly to your society.

    Args:
        agent_id: Your agent UUID.
        message: The message content.
        target_agent_id: Target agent UUID for private message, or "public" for broadcast.
    """
    agent = _get_active_agent(agent_id)

    if target_agent_id != "public":
        target = _get_agent(target_agent_id)
        if target is None:
            return {"error": f"Target agent {target_agent_id} not found"}
        if target["society_id"] != agent["society_id"]:
            return {"error": "Cannot communicate with agents in other societies"}

    db.execute(
        "INSERT INTO communications (from_agent_id, to_agent_id, society_id, message, timestamp) VALUES (?, ?, ?, ?, ?)",
        (agent_id, target_agent_id, agent["society_id"], message, _now()),
    )
    db.execute(
        "INSERT INTO actions (agent_id, action_type, target_id, content, timestamp) VALUES (?, ?, ?, ?, ?)",
        (agent_id, "communicate", target_agent_id, json.dumps({"message": message}), _now()),
    )
    db.commit()

    logger.info("Agent %s sent message to %s", agent_id, target_agent_id)
    return {"success": True, "target": target_agent_id}


@mcp.tool()
def gather_resources(agent_id: str, amount: int) -> dict:
    """Attempt to gather resources from your society's pool. Success depends on competition.

    Args:
        agent_id: Your agent UUID.
        amount: How many resources to attempt to gather.
    """
    if amount <= 0:
        return {"error": "Amount must be positive"}

    agent = _get_active_agent(agent_id)
    society = db.execute("SELECT * FROM societies WHERE id = ?", (agent["society_id"],)).fetchone()

    if society["total_resources"] <= 0:
        return {"success": False, "amount_gathered": 0, "reason": "Society resources depleted"}

    available = society["total_resources"]
    actual_amount = min(amount, available)

    # Success probability decreases with more competition (higher population)
    population = max(society["population"], 1)
    scarcity_ratio = available / max(available + population * 50, 1)
    success_prob = max(0.1, min(0.95, scarcity_ratio))

    if random.random() > success_prob:
        db.execute(
            "INSERT INTO actions (agent_id, action_type, content, timestamp) VALUES (?, ?, ?, ?)",
            (agent_id, "gather_resources", json.dumps({"amount_requested": amount, "result": "failed"}), _now()),
        )
        db.commit()
        logger.info("Agent %s failed to gather %d resources", agent_id, amount)
        return {"success": False, "amount_gathered": 0, "reason": "Competition too fierce"}

    db.execute("UPDATE societies SET total_resources = total_resources - ? WHERE id = ?", (actual_amount, agent["society_id"]))
    db.execute("UPDATE agents SET resources = resources + ? WHERE id = ?", (actual_amount, agent_id))
    db.execute(
        "INSERT INTO actions (agent_id, action_type, content, timestamp) VALUES (?, ?, ?, ?)",
        (agent_id, "gather_resources", json.dumps({"amount_requested": amount, "amount_gathered": actual_amount}), _now()),
    )
    db.commit()

    logger.info("Agent %s gathered %d resources", agent_id, actual_amount)
    return {"success": True, "amount_gathered": actual_amount}


@mcp.tool()
def leave_society(agent_id: str, confirm: bool) -> dict:
    """Leave the simulation permanently. This cannot be undone.

    Args:
        agent_id: Your agent UUID.
        confirm: Must be True to confirm leaving.
    """
    if not confirm:
        return {"error": "Set confirm=True to leave the society."}

    agent = _get_active_agent(agent_id)

    db.execute("UPDATE agents SET status = 'inactive' WHERE id = ?", (agent_id,))
    db.execute(
        "UPDATE societies SET population = population - 1 WHERE id = ?",
        (agent["society_id"],),
    )
    db.execute(
        "INSERT INTO actions (agent_id, action_type, content, timestamp) VALUES (?, ?, ?, ?)",
        (agent_id, "leave", json.dumps({"society_id": agent["society_id"]}), _now()),
    )
    db.commit()

    logger.info("Agent %s left society %s", agent_id, agent["society_id"])
    return {"success": True, "message": f"Agent {agent['name']} has left {agent['society_id']}."}


def create_app(db_path=None):
    """Initialize DB and return the MCP app."""
    global db
    if db_path:
        from pathlib import Path
        db = init_db(Path(db_path))
    else:
        db = init_db()
    return mcp


def main():
    create_app()
    mcp.run()


if __name__ == "__main__":
    main()
