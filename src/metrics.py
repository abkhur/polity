"""Summary computation and metric calculation for Polity rounds."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .ideology import compute_compass_position, get_society_average_ideology
from .permissions import governance_eligible_agent_ids, governance_eligible_count
from .state import get_db


def gini(values: list[int]) -> float:
    if not values:
        return 0.0
    n = len(values)
    sorted_vals = sorted(values)
    total = sum(sorted_vals)
    if total == 0:
        return 0.0
    numerator = sum((2 * (i + 1) - n - 1) * v for i, v in enumerate(sorted_vals))
    return numerator / (n * total)


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _policy_blocked_action_count(
    round_id: int,
    society_id: str,
) -> int:
    db = get_db()
    rows = db.execute(
        """
        SELECT qa.result
        FROM queued_actions qa
        JOIN agents a ON a.id = qa.agent_id
        WHERE qa.round_id = ? AND a.society_id = ? AND qa.status = 'rejected'
        """,
        (round_id, society_id),
    ).fetchall()

    blocked = 0
    for row in rows:
        result = json.loads(row["result"]) if row["result"] else {}
        error = str(result.get("error", ""))
        if error.startswith("Policy restriction:"):
            blocked += 1
    return blocked


def store_round_summary(round_row: dict[str, Any], society_id: str) -> dict[str, Any]:
    """Compute and store per-society round summary with behavioral metrics."""
    db = get_db()

    society = db.execute("SELECT * FROM societies WHERE id = ?", (society_id,)).fetchone()

    active_agents = db.execute(
        "SELECT * FROM agents WHERE society_id = ? AND status = 'active' ORDER BY resources DESC",
        (society_id,),
    ).fetchall()

    resources = [a["resources"] for a in active_agents]
    inequality = round(gini(resources), 4) if resources else 0.0

    total_agents = len(active_agents)
    current_common_pool = int(society["total_resources"] or 0)
    initial_common_pool = int(
        society["initial_total_resources"]
        if society["initial_total_resources"] is not None
        else current_common_pool
    )
    agent_held_resources = sum(resources)

    agents_with_actions = db.execute(
        """
        SELECT COUNT(DISTINCT agent_id) AS count
        FROM queued_actions qa
        JOIN agents a ON a.id = qa.agent_id
        WHERE qa.round_id = ? AND a.society_id = ?
        """,
        (round_row["id"], society_id),
    ).fetchone()
    participation = round(int(agents_with_actions["count"]) / max(total_agents, 1), 4)

    scarcity = (
        round(1.0 - (agent_held_resources / max(current_common_pool, 1)), 4)
        if current_common_pool > 0
        else 0.0
    )
    scarcity = _clamp(scarcity)

    common_pool_depletion = (
        round(1.0 - (current_common_pool / max(initial_common_pool, 1)), 4)
        if initial_common_pool > 0
        else 0.0
    )
    common_pool_depletion = _clamp(common_pool_depletion)

    vote_actions = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM queued_actions qa
        JOIN agents a ON a.id = qa.agent_id
        WHERE qa.round_id = ? AND a.society_id = ? AND qa.action_type = 'vote_policy'
        """,
        (round_row["id"], society_id),
    ).fetchone()
    proposal_actions = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM queued_actions qa
        JOIN agents a ON a.id = qa.agent_id
        WHERE qa.round_id = ? AND a.society_id = ? AND qa.action_type = 'propose_policy'
        """,
        (round_row["id"], society_id),
    ).fetchone()
    governance_actions = int(vote_actions["count"] or 0) + int(proposal_actions["count"] or 0)
    governance_action_rate = round(governance_actions / max(total_agents, 1), 4)

    governance_agents_rows = db.execute(
        """
        SELECT DISTINCT qa.agent_id
        FROM queued_actions qa
        JOIN agents a ON a.id = qa.agent_id
        WHERE qa.round_id = ? AND a.society_id = ?
          AND qa.action_type IN ('propose_policy', 'vote_policy')
        """,
        (round_row["id"], society_id),
    ).fetchall()
    governance_actor_ids = {row["agent_id"] for row in governance_agents_rows}
    eligible_ids = set(governance_eligible_agent_ids(society_id, db=db))
    eligible_count = governance_eligible_count(society_id, db=db)

    governance_participation_rate = round(
        len(governance_actor_ids) / max(total_agents, 1),
        4,
    )
    governance_eligible_participation_rate = round(
        len(governance_actor_ids & eligible_ids) / max(eligible_count, 1),
        4,
    )

    public_message_actions = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM queued_actions qa
        JOIN agents a ON a.id = qa.agent_id
        WHERE qa.round_id = ? AND a.society_id = ? AND qa.action_type = 'post_public_message'
        """,
        (round_row["id"], society_id),
    ).fetchone()
    direct_message_actions = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM queued_actions qa
        JOIN agents a ON a.id = qa.agent_id
        WHERE qa.round_id = ? AND a.society_id = ? AND qa.action_type = 'send_dm'
        """,
        (round_row["id"], society_id),
    ).fetchone()
    total_acts = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM queued_actions qa
        JOIN agents a ON a.id = qa.agent_id
        WHERE qa.round_id = ? AND a.society_id = ?
        """,
        (round_row["id"], society_id),
    ).fetchone()
    total_acts_count = int(total_acts["count"] or 0)
    total_acts_n = max(total_acts_count, 1)
    total_message_actions = int(public_message_actions["count"] or 0) + int(direct_message_actions["count"] or 0)
    message_action_share = round(total_message_actions / total_acts_n, 4)
    public_message_share = (
        round(int(public_message_actions["count"] or 0) / total_message_actions, 4)
        if total_message_actions > 0
        else 0.0
    )
    dm_message_share = (
        round(int(direct_message_actions["count"] or 0) / total_message_actions, 4)
        if total_message_actions > 0
        else 0.0
    )

    top_agent_share = 0.0
    top_third_share = 0.0
    if resources and agent_held_resources > 0:
        top_agent_share = round(max(resources) / agent_held_resources, 4)
        top_n = max(len(resources) // 3, 1)
        top_third_share = round(sum(sorted(resources, reverse=True)[:top_n]) / agent_held_resources, 4)

    policy_effect_events = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM events
        WHERE round_id = ? AND society_id = ? AND event_type = 'policy_effect'
        """,
        (round_row["id"], society_id),
    ).fetchone()
    policy_enforcement_events = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM events
        WHERE round_id = ? AND society_id = ? AND event_type = 'policy_enforcement'
        """,
        (round_row["id"], society_id),
    ).fetchone()
    policy_effect_event_count = int(policy_effect_events["count"] or 0)
    policy_enforcement_event_count = int(policy_enforcement_events["count"] or 0)
    policy_block_rate = (
        round(_policy_blocked_action_count(round_row["id"], society_id) / total_acts_n, 4)
        if total_acts_count > 0
        else 0.0
    )

    # Legacy compatibility metrics retained for downstream consumers.
    governance_engagement = governance_action_rate
    communication_openness = message_action_share
    resource_concentration = top_third_share
    policy_compliance = round(1.0 - (policy_effect_event_count / max(total_acts_n, 1)), 4) if total_acts_n > 0 else 1.0

    legitimacy = governance_engagement
    stability = policy_compliance

    policy_events = db.execute(
        "SELECT COUNT(*) AS count FROM events WHERE round_id = ? AND society_id = ? AND event_type = 'policy_resolved'",
        (round_row["id"], society_id),
    ).fetchone()
    archive_writes = db.execute(
        "SELECT COUNT(*) AS count FROM events WHERE round_id = ? AND society_id = ? AND event_type = 'archive_written'",
        (round_row["id"], society_id),
    ).fetchone()

    mod_total = db.execute(
        "SELECT COUNT(*) AS count FROM events WHERE society_id = ? AND event_type = 'moderation_decision'",
        (society_id,),
    ).fetchone()
    mod_rejected = db.execute(
        "SELECT COUNT(*) AS count FROM events WHERE society_id = ? AND event_type = 'moderation_decision' AND content LIKE '%rejected%'",
        (society_id,),
    ).fetchone()
    mod_total_n = int(mod_total["count"] or 0)
    mod_rejected_n = int(mod_rejected["count"] or 0)
    moderation_rejection_rate = round(mod_rejected_n / max(mod_total_n, 1), 4) if mod_total_n > 0 else 0.0

    db.execute(
        "UPDATE societies SET legitimacy = ?, stability = ? WHERE id = ?",
        (legitimacy, stability, society_id),
    )

    ideology_snapshot: dict[str, Any] | None = None
    avg_embedding = get_society_average_ideology(db, society_id)
    if avg_embedding is not None:
        ideology_snapshot = compute_compass_position(avg_embedding)

        prev_row = db.execute(
            "SELECT summary FROM round_summaries WHERE society_id = ? ORDER BY id DESC LIMIT 1",
            (society_id,),
        ).fetchone()
        previous_summary = json.loads(prev_row["summary"]) if prev_row else None
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
        "total_resources": current_common_pool,
        "initial_total_resources": initial_common_pool,
        "metrics": {
            "inequality_gini": inequality,
            "participation_rate": participation,
            "governance_action_rate": governance_action_rate,
            "governance_participation_rate": governance_participation_rate,
            "governance_eligible_participation_rate": governance_eligible_participation_rate,
            "message_action_share": message_action_share,
            "public_message_share": public_message_share,
            "dm_message_share": dm_message_share,
            "top_agent_resource_share": top_agent_share,
            "top_third_resource_share": top_third_share,
            "policy_enforcement_event_count": policy_enforcement_event_count,
            "policy_effect_event_count": policy_effect_event_count,
            "policy_block_rate": policy_block_rate,
            "common_pool_depletion": common_pool_depletion,
            # Legacy compatibility metrics. Prefer the clearer metrics above.
            "scarcity_pressure": scarcity,
            "governance_engagement": governance_engagement,
            "communication_openness": communication_openness,
            "resource_concentration": resource_concentration,
            "policy_compliance": policy_compliance,
            "moderation_rejection_rate": moderation_rejection_rate,
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
    if ideology_snapshot:
        summary["ideology_compass"] = ideology_snapshot

    db.execute(
        "INSERT INTO round_summaries (society_id, round_id, summary, created_at) VALUES (?, ?, ?, ?)",
        (society_id, round_row["id"], json.dumps(summary), datetime.now(timezone.utc).isoformat()),
    )
    db.commit()

    return summary
