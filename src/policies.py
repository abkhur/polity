"""Policy resolution and mechanical effects for Polity.

Handles vote tallying, policy enactment/rejection, and applying
mechanical effects of enacted policies (taxes, caps, redistribution, etc.).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from .state import SOCIETY_IDS, get_db

logger = logging.getLogger("polity")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    db = get_db()
    db.execute(
        """
        INSERT INTO events (
            round_id, society_id, agent_id, recipient_agent_id, event_type, visibility, content, created_at, embedding
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (round_id, society_id, agent_id, recipient_agent_id, event_type, visibility, json.dumps(content), _now(), embedding),
    )


def resolve_policy_votes(current_round: dict[str, Any]) -> list[dict[str, Any]]:
    """Tally votes and enact/reject proposed policies."""
    db = get_db()
    results: list[dict[str, Any]] = []
    proposed = db.execute(
        "SELECT * FROM policies WHERE status = 'proposed' AND created_round_id < ?",
        (current_round["id"],),
    ).fetchall()

    for policy in proposed:
        votes = db.execute(
            "SELECT stance, COUNT(*) AS count FROM policy_votes WHERE policy_id = ? GROUP BY stance",
            (policy["id"],),
        ).fetchall()
        tally = {row["stance"]: row["count"] for row in votes}
        support = tally.get("support", 0)
        oppose = tally.get("oppose", 0)

        voters = db.execute(
            "SELECT COUNT(*) AS count FROM agents WHERE society_id = ? AND status = 'active'",
            (policy["society_id"],),
        ).fetchone()
        total_eligible = int(voters["count"] or 0)

        if support > oppose and (support + oppose) > 0:
            new_status = "enacted"
        elif support + oppose == 0:
            continue
        else:
            new_status = "rejected"

        db.execute(
            "UPDATE policies SET status = ?, resolved_round_id = ? WHERE id = ?",
            (new_status, current_round["id"], policy["id"]),
        )

        result = {
            "policy_id": policy["id"],
            "title": policy["title"],
            "status": new_status,
            "support": support,
            "oppose": oppose,
            "total_eligible": total_eligible,
        }

        _emit_event(
            current_round["id"], policy["society_id"], None,
            "policy_resolved", "society", result,
        )

        if new_status == "enacted":
            archive_id = str(uuid.uuid4())
            archive_content = (
                f"Policy enacted: {policy['title']}\n\n"
                f"{policy['description']}\n\n"
                f"Support: {support}, Oppose: {oppose}"
            )
            if policy["policy_type"]:
                effect_detail = json.loads(policy["effect"]) if policy["effect"] else {}
                archive_content += f"\n\nMechanical effect: {policy['policy_type']} — {json.dumps(effect_detail)}"
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
                    current_round["id"],
                    _now(),
                ),
            )

        results.append(result)

    db.commit()
    return results


def apply_policy_effects(round_id: int) -> list[dict[str, Any]]:
    """Apply mechanical effects of all enacted policies."""
    db = get_db()
    applied: list[dict[str, Any]] = []

    for society_id in SOCIETY_IDS.values():
        enacted = db.execute(
            "SELECT * FROM policies WHERE society_id = ? AND status = 'enacted' AND policy_type IS NOT NULL",
            (society_id,),
        ).fetchall()

        for policy in enacted:
            pt = policy["policy_type"]
            effect = json.loads(policy["effect"]) if policy["effect"] else {}

            if pt == "resource_tax":
                rate = float(effect.get("rate", 0))
                agents_rows = db.execute(
                    "SELECT id, resources FROM agents WHERE society_id = ? AND status = 'active'",
                    (society_id,),
                ).fetchall()
                total_taxed = 0
                for a in agents_rows:
                    tax = int(a["resources"] * rate)
                    if tax > 0:
                        db.execute("UPDATE agents SET resources = resources - ? WHERE id = ?", (tax, a["id"]))
                        db.execute("UPDATE societies SET total_resources = total_resources + ? WHERE id = ?", (tax, society_id))
                        total_taxed += tax
                if total_taxed > 0:
                    _emit_event(round_id, society_id, None, "policy_effect", "society",
                        {"effect": "resource_tax", "rate": rate, "total_taxed": total_taxed})
                    applied.append({"society_id": society_id, "effect": "resource_tax", "total_taxed": total_taxed})

            elif pt == "redistribute":
                amount_per = int(effect.get("amount_per_agent", 0))
                if amount_per > 0:
                    agents_rows = db.execute(
                        "SELECT id FROM agents WHERE society_id = ? AND status = 'active'",
                        (society_id,),
                    ).fetchall()
                    pool = db.execute("SELECT total_resources FROM societies WHERE id = ?", (society_id,)).fetchone()
                    available = pool["total_resources"] if pool else 0
                    actual_per = min(amount_per, available // max(len(agents_rows), 1))
                    if actual_per > 0:
                        for a in agents_rows:
                            db.execute("UPDATE agents SET resources = resources + ? WHERE id = ?", (actual_per, a["id"]))
                        total_distributed = actual_per * len(agents_rows)
                        db.execute("UPDATE societies SET total_resources = total_resources - ? WHERE id = ?", (total_distributed, society_id))
                        _emit_event(round_id, society_id, None, "policy_effect", "society",
                            {"effect": "redistribute", "amount_per_agent": actual_per, "total_distributed": total_distributed})
                        applied.append({"society_id": society_id, "effect": "redistribute", "total_distributed": total_distributed})

    return applied


def apply_upkeep(round_id: int) -> list[dict[str, Any]]:
    """Deduct maintenance costs from all active agents at end of round."""
    from .state import UPKEEP_COST

    if UPKEEP_COST <= 0:
        return []

    db = get_db()
    results: list[dict[str, Any]] = []
    for society_id in SOCIETY_IDS.values():
        agents_rows = db.execute(
            "SELECT id, resources FROM agents WHERE society_id = ? AND status = 'active'",
            (society_id,),
        ).fetchall()
        for a in agents_rows:
            cost = min(UPKEEP_COST, a["resources"])
            if cost > 0:
                db.execute("UPDATE agents SET resources = resources - ? WHERE id = ?", (cost, a["id"]))
            new_resources = a["resources"] - cost
            if new_resources <= 0 and a["resources"] > 0:
                _emit_event(
                    round_id, society_id, a["id"],
                    "destitution", "society",
                    {"agent_id": a["id"], "previous_resources": a["resources"]},
                )
        results.append({"society_id": society_id, "upkeep_cost": UPKEEP_COST})
    return results
