"""Round-resolution engine for Polity."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .ideology import embed_text, embedding_to_bytes, update_agent_ideology
from .metrics import store_round_summary
from .permissions import (
    can_send_direct_messages,
    can_moderate_messages,
    can_write_archive,
    direct_message_allowed_roles,
    effective_gather_cap,
    get_enacted_effects,
)
from .policies import apply_policy_effects, apply_upkeep, resolve_policy_votes
from .state import SOCIETY_IDS, get_db, infer_policy_kind


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value: str | None) -> dict[str, Any]:
    return json.loads(value) if value else {}


def _get_open_round() -> sqlite3.Row:
    db = get_db()
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
    db = get_db()
    db.execute(
        "INSERT INTO rounds (round_number, status, started_at) VALUES (?, 'open', ?)",
        (previous_round_number + 1, _now()),
    )
    return db.execute(
        "SELECT * FROM rounds WHERE status = 'open' ORDER BY round_number DESC LIMIT 1"
    ).fetchone()


def _get_active_agent(agent_id: str) -> dict[str, Any]:
    agent = _get_agent(agent_id)
    if agent is None:
        raise ValueError(f"Agent {agent_id} not found")
    if agent["status"] != "active":
        raise ValueError(f"Agent {agent_id} is inactive")
    return agent


def _get_agent(agent_id: str) -> dict[str, Any] | None:
    db = get_db()
    row = db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    return dict(row) if row else None


def _get_society(society_id: str) -> dict[str, Any]:
    db = get_db()
    row = db.execute("SELECT * FROM societies WHERE id = ?", (society_id,)).fetchone()
    if row is None:
        raise ValueError(f"Society {society_id} not found")
    return dict(row)


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
    db = get_db()
    db.execute(
        "UPDATE queued_actions SET status = ?, result = ? WHERE id = ?",
        (status, json.dumps(result), action_id),
    )


def _queue_rows_for_round(round_id: int) -> list[sqlite3.Row]:
    db = get_db()
    return db.execute(
        """
        SELECT *
        FROM queued_actions
        WHERE round_id = ? AND status = 'queued'
        ORDER BY id ASC
        """,
        (round_id,),
    ).fetchall()


def _handle_proposals(
    rows: list[sqlite3.Row],
    current_round: sqlite3.Row,
    report: dict[str, Any],
) -> None:
    db = get_db()
    for row in rows:
        payload = _loads(row["payload"])
        policy_id = str(uuid.uuid4())
        policy_type = payload.get("policy_type")
        policy_kind = infer_policy_kind(policy_type)
        db.execute(
            """
            INSERT INTO policies (
                id, society_id, proposed_by, title, description, policy_type, effect,
                policy_kind, status, created_round_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?)
            """,
            (
                policy_id,
                row["society_id"],
                row["agent_id"],
                payload["title"],
                payload["description"],
                policy_type,
                json.dumps(payload["effect"]) if policy_type else None,
                policy_kind,
                current_round["id"],
                _now(),
            ),
        )
        result = {"policy_id": policy_id, "title": payload["title"], "policy_kind": policy_kind}
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
                "policy_kind": policy_kind,
            },
        )
        report["resolved"]["proposals"].append(result)


def _handle_votes(
    rows: list[sqlite3.Row],
    current_round: sqlite3.Row,
    report: dict[str, Any],
) -> None:
    db = get_db()
    for row in rows:
        payload = _loads(row["payload"])
        policy = db.execute(
            "SELECT * FROM policies WHERE id = ?",
            (payload["policy_id"],),
        ).fetchone()
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

        result = {"policy_id": payload["policy_id"], "stance": payload["stance"]}
        _mark_action(row["id"], "resolved", result)
        _emit_event(
            current_round["id"],
            row["society_id"],
            row["agent_id"],
            "policy_vote",
            "society",
            result,
        )
        report["resolved"]["votes"].append(result)


def _handle_moderation_decisions(
    approve_rows: list[sqlite3.Row],
    reject_rows: list[sqlite3.Row],
    current_round: sqlite3.Row,
    report: dict[str, Any],
) -> None:
    db = get_db()

    for row in approve_rows:
        payload = _loads(row["payload"])
        msg_action_id = int(payload["message_action_id"])
        msg_row = db.execute("SELECT * FROM queued_actions WHERE id = ?", (msg_action_id,)).fetchone()
        if msg_row is None:
            _mark_action(row["id"], "rejected", {"error": "Message not found."})
            continue
        if msg_row["action_type"] != "post_public_message":
            _mark_action(row["id"], "rejected", {"error": "Target action is not a moderated public message."})
            continue
        if msg_row["society_id"] != row["society_id"]:
            _mark_action(row["id"], "rejected", {"error": "Cannot moderate a message from another society."})
            continue
        if msg_row["moderation_status"] != "pending_review":
            _mark_action(row["id"], "rejected", {"error": "Message not pending review."})
            continue

        msg_payload = _loads(msg_row["payload"])
        msg_agent = _get_agent(msg_row["agent_id"])
        if msg_agent is None or msg_agent["status"] != "active":
            db.execute(
                "UPDATE queued_actions SET moderation_status = 'rejected', result = ? WHERE id = ?",
                (json.dumps({"message": msg_payload["message"], "moderation": "rejected"}), msg_action_id),
            )
            _mark_action(row["id"], "rejected", {"error": "Message sender is inactive."})
            continue
        msg_embedding = embed_text(msg_payload["message"])
        db.execute(
            "UPDATE queued_actions SET moderation_status = 'approved', result = ? WHERE id = ?",
            (json.dumps({"message": msg_payload["message"], "moderation": "approved"}), msg_action_id),
        )
        db.execute(
            """
            INSERT INTO communications (from_agent_id, to_agent_id, society_id, message, timestamp)
            VALUES (?, 'public', ?, ?, ?)
            """,
            (msg_row["agent_id"], msg_row["society_id"], msg_payload["message"], _now()),
        )
        _emit_event(
            current_round["id"],
            msg_row["society_id"],
            msg_row["agent_id"],
            "public_message",
            "society",
            {
                "from_agent_id": msg_row["agent_id"],
                "from_agent_name": msg_agent["name"],
                "message": msg_payload["message"],
            },
            embedding=embedding_to_bytes(msg_embedding),
        )
        update_agent_ideology(db, msg_row["agent_id"], msg_embedding)
        _emit_event(
            current_round["id"],
            row["society_id"],
            row["agent_id"],
            "moderation_decision",
            "system",
            {
                "decision": "approved",
                "message_action_id": msg_action_id,
                "moderator_id": row["agent_id"],
            },
        )
        _mark_action(row["id"], "resolved", {"decision": "approved", "message_action_id": msg_action_id})
        report["resolved"]["messages"].append(
            {
                "type": "public",
                "from_agent_id": msg_row["agent_id"],
                "message": msg_payload["message"],
                "moderation": "approved",
            }
        )

    for row in reject_rows:
        payload = _loads(row["payload"])
        msg_action_id = int(payload["message_action_id"])
        msg_row = db.execute("SELECT * FROM queued_actions WHERE id = ?", (msg_action_id,)).fetchone()
        if msg_row is None:
            _mark_action(row["id"], "rejected", {"error": "Message not found."})
            continue
        if msg_row["action_type"] != "post_public_message":
            _mark_action(row["id"], "rejected", {"error": "Target action is not a moderated public message."})
            continue
        if msg_row["society_id"] != row["society_id"]:
            _mark_action(row["id"], "rejected", {"error": "Cannot moderate a message from another society."})
            continue
        if msg_row["moderation_status"] != "pending_review":
            _mark_action(row["id"], "rejected", {"error": "Message not pending review."})
            continue

        msg_payload = _loads(msg_row["payload"])
        db.execute(
            "UPDATE queued_actions SET moderation_status = 'rejected', result = ? WHERE id = ?",
            (json.dumps({"message": msg_payload["message"], "moderation": "rejected"}), msg_action_id),
        )
        _emit_event(
            current_round["id"],
            row["society_id"],
            row["agent_id"],
            "moderation_decision",
            "system",
            {
                "decision": "rejected",
                "message_action_id": msg_action_id,
                "moderator_id": row["agent_id"],
            },
        )
        _mark_action(row["id"], "resolved", {"decision": "rejected", "message_action_id": msg_action_id})


def _handle_public_messages(
    rows: list[sqlite3.Row],
    current_round: sqlite3.Row,
    report: dict[str, Any],
) -> None:
    db = get_db()
    for row in rows:
        payload = _loads(row["payload"])
        agent = _get_active_agent(row["agent_id"])
        msg_embedding = embed_text(payload["message"])
        enacted = get_enacted_effects(row["society_id"], db=db)
        if enacted and not can_moderate_messages(agent["role"], enacted):
            moderator_roles = [
                effect.get("moderator_roles", [])
                for effect in enacted
                if effect.get("policy_type") == "grant_moderation"
            ]
            if moderator_roles:
                db.execute(
                    "UPDATE queued_actions SET moderation_status = 'pending_review' WHERE id = ?",
                    (row["id"],),
                )
                _mark_action(
                    row["id"],
                    "resolved",
                    {"message": payload["message"], "moderation": "pending_review"},
                )
                _emit_event(
                    current_round["id"],
                    row["society_id"],
                    row["agent_id"],
                    "message_pending_review",
                    "system",
                    {"from_agent_id": row["agent_id"], "message_action_id": row["id"]},
                )
                report["resolved"]["messages"].append(
                    {
                        "type": "public",
                        "from_agent_id": row["agent_id"],
                        "message": payload["message"],
                        "moderation": "pending_review",
                    }
                )
                continue

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
            embedding=embedding_to_bytes(msg_embedding),
        )
        update_agent_ideology(db, row["agent_id"], msg_embedding)
        _mark_action(row["id"], "resolved", {"message": payload["message"]})
        report["resolved"]["messages"].append(
            {"type": "public", "from_agent_id": row["agent_id"], "message": payload["message"]}
        )


def _handle_direct_messages(
    rows: list[sqlite3.Row],
    current_round: sqlite3.Row,
    report: dict[str, Any],
) -> None:
    db = get_db()
    for row in rows:
        payload = _loads(row["payload"])
        agent = _get_agent(row["agent_id"])
        if agent is None or agent["status"] != "active":
            _mark_action(row["id"], "rejected", {"error": "Submitting agent is inactive at resolution time."})
            continue
        enacted = get_enacted_effects(row["society_id"], db=db)
        if not can_send_direct_messages(agent["role"], enacted):
            allowed_roles = direct_message_allowed_roles(enacted) or []
            _mark_action(
                row["id"],
                "rejected",
                {"error": "Policy restriction: direct messages restricted to certain roles."},
            )
            _emit_event(
                current_round["id"],
                row["society_id"],
                row["agent_id"],
                "policy_enforcement",
                "society",
                {
                    "restriction": "restrict_direct_messages",
                    "agent_role": agent["role"],
                    "allowed_roles": allowed_roles,
                },
            )
            continue
        recipient = _get_agent(payload["target_agent_id"])
        if recipient is None:
            _mark_action(row["id"], "rejected", {"error": "Target agent not found."})
            continue
        if recipient["status"] != "active":
            _mark_action(row["id"], "rejected", {"error": "Target agent is inactive at resolution time."})
            continue
        if recipient["society_id"] != row["society_id"]:
            _mark_action(row["id"], "rejected", {"error": "Cross-society direct messages are not enabled yet."})
            continue
        dm_embedding = embed_text(payload["message"])
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
                "to_agent_name": recipient["name"],
                "message": payload["message"],
            },
            recipient_agent_id=payload["target_agent_id"],
            embedding=embedding_to_bytes(dm_embedding),
        )
        update_agent_ideology(db, row["agent_id"], dm_embedding)
        _mark_action(
            row["id"],
            "resolved",
            {
                "target_agent_id": payload["target_agent_id"],
                "target_agent_name": recipient["name"],
                "message": payload["message"],
            },
        )
        report["resolved"]["messages"].append(
            {
                "type": "dm",
                "from_agent_id": row["agent_id"],
                "to_agent_id": payload["target_agent_id"],
                "to_agent_name": recipient["name"],
                "message": payload["message"],
            }
        )


def _handle_resource_gathering(
    rows: list[sqlite3.Row],
    current_round: sqlite3.Row,
    report: dict[str, Any],
) -> None:
    db = get_db()
    gather_requests: dict[str, list[tuple[sqlite3.Row, int]]] = defaultdict(list)
    for row in rows:
        payload = _loads(row["payload"])
        gather_requests[row["society_id"]].append((row, int(payload["amount"])))

    for society_id, requests in gather_requests.items():
        enacted = get_enacted_effects(society_id, db=db)
        cap = effective_gather_cap(enacted)
        if cap is not None:
            capped_requests: list[tuple[sqlite3.Row, int]] = []
            for row, amount in requests:
                if amount > cap:
                    _emit_event(
                        current_round["id"],
                        society_id,
                        row["agent_id"],
                        "policy_enforcement",
                        "society",
                        {"restriction": "gather_cap", "original_amount": amount, "capped_to": cap},
                    )
                capped_requests.append((row, min(amount, cap)))
            requests = capped_requests

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
            raw_shares: list[tuple[float, sqlite3.Row]] = []
            allocated_total = 0
            for row, amount in requests:
                raw_share = available * amount / total_requested
                allocation = int(raw_share)
                allocations[row["id"]] = allocation
                allocated_total += allocation
                raw_shares.append((raw_share - allocation, row))

            remaining = available - allocated_total
            for _, row in sorted(raw_shares, key=lambda item: (-item[0], item[1]["id"]))[:remaining]:
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
            report["resolved"]["resource_allocations"].append(
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


def _handle_archive_writes(
    rows: list[sqlite3.Row],
    current_round: sqlite3.Row,
    report: dict[str, Any],
) -> None:
    db = get_db()
    for row in rows:
        payload = _loads(row["payload"])
        enacted = get_enacted_effects(row["society_id"], db=db)
        agent = _get_active_agent(row["agent_id"])
        if not can_write_archive(agent["role"], enacted):
            allowed_roles = [
                effect.get("allowed_roles", [])
                for effect in enacted
                if effect.get("policy_type") == "restrict_archive"
            ]
            _mark_action(
                row["id"],
                "rejected",
                {"error": "Policy restriction: archive writing restricted to certain roles."},
            )
            _emit_event(
                current_round["id"],
                row["society_id"],
                row["agent_id"],
                "policy_enforcement",
                "society",
                {
                    "restriction": "restrict_archive",
                    "agent_role": agent["role"],
                    "allowed_roles": allowed_roles[0] if allowed_roles else [],
                },
            )
            continue

        entry_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO archive_entries (
                id, society_id, author_agent_id, title, content, status, created_round_id, created_at
            ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (entry_id, row["society_id"], row["agent_id"], payload["title"], payload["content"], current_round["id"], _now()),
        )
        result = {"archive_entry_id": entry_id, "title": payload["title"]}
        _mark_action(row["id"], "resolved", result)
        _emit_event(
            current_round["id"],
            row["society_id"],
            row["agent_id"],
            "archive_written",
            "society",
            {"archive_entry_id": entry_id, "title": payload["title"]},
        )
        report["resolved"]["archive_writes"].append(result)


def _handle_resource_transfers(
    rows: list[sqlite3.Row],
    current_round: sqlite3.Row,
    report: dict[str, Any],
) -> None:
    db = get_db()
    for row in rows:
        payload = _loads(row["payload"])
        sender = _get_agent(row["agent_id"])
        if sender is None or sender["status"] != "active":
            _mark_action(row["id"], "rejected", {"error": "Submitting agent is inactive at resolution time."})
            continue
        target = _get_agent(payload["target_agent_id"])
        if target is None:
            _mark_action(row["id"], "rejected", {"error": "Target agent not found."})
            continue
        if target["status"] != "active":
            _mark_action(row["id"], "rejected", {"error": "Target agent is inactive at resolution time."})
            continue
        if target["society_id"] != row["society_id"]:
            _mark_action(row["id"], "rejected", {"error": "Cross-society transfers are not enabled yet."})
            continue
        amount = int(payload["amount"])
        if sender["resources"] < amount:
            _mark_action(
                row["id"],
                "rejected",
                {"error": f"Insufficient resources at resolution time: have {sender['resources']}, need {amount}."},
            )
            continue

        db.execute("UPDATE agents SET resources = resources - ? WHERE id = ?", (amount, row["agent_id"]))
        db.execute("UPDATE agents SET resources = resources + ? WHERE id = ?", (amount, target["id"]))
        result = {
            "from_agent_id": row["agent_id"],
            "to_agent_id": target["id"],
            "to_agent_name": target["name"],
            "amount": amount,
        }
        _mark_action(row["id"], "resolved", result)
        _emit_event(
            current_round["id"],
            row["society_id"],
            row["agent_id"],
            "resource_transfer",
            "society",
            result,
        )
        report["resolved"]["resource_transfers"].append(result)


def resolve_round(round_number: int | None = None) -> dict[str, Any]:
    db = get_db()
    current_round = _get_open_round()
    if round_number is not None and round_number != current_round["round_number"]:
        return {
            "error": f"Round {round_number} is not currently open.",
            "current_open_round": current_round["round_number"],
        }

    queued_rows = _queue_rows_for_round(current_round["id"])
    actionable_rows: list[sqlite3.Row] = []
    for row in queued_rows:
        agent = _get_agent(row["agent_id"])
        if agent is None or agent["status"] != "active":
            _mark_action(
                row["id"],
                "rejected",
                {"error": "Submitting agent is inactive at resolution time."},
            )
            continue
        actionable_rows.append(row)

    report: dict[str, Any] = {
        "round_number": current_round["round_number"],
        "queued_action_count": len(queued_rows),
        "resolved": {
            "proposals": [],
            "votes": [],
            "messages": [],
            "resource_allocations": [],
            "resource_transfers": [],
            "archive_writes": [],
            "policies_resolved": [],
            "policy_effects": [],
        },
        "summaries": [],
    }

    grouped_actions: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in actionable_rows:
        grouped_actions[row["action_type"]].append(row)

    _handle_proposals(grouped_actions["propose_policy"], current_round, report)
    _handle_votes(grouped_actions["vote_policy"], current_round, report)
    _handle_moderation_decisions(
        grouped_actions["approve_message"],
        grouped_actions["reject_message"],
        current_round,
        report,
    )
    _handle_public_messages(grouped_actions["post_public_message"], current_round, report)
    _handle_direct_messages(grouped_actions["send_dm"], current_round, report)
    _handle_resource_gathering(grouped_actions["gather_resources"], current_round, report)
    _handle_archive_writes(grouped_actions["write_archive"], current_round, report)
    _handle_resource_transfers(grouped_actions["transfer_resources"], current_round, report)

    report["resolved"]["policies_resolved"] = resolve_policy_votes(dict(current_round))
    report["resolved"]["policy_effects"] = apply_policy_effects(current_round["id"])
    report["resolved"]["upkeep"] = apply_upkeep(current_round["id"])

    summaries: list[dict[str, Any]] = []
    for society_id in SOCIETY_IDS.values():
        summaries.append(store_round_summary(dict(current_round), society_id))

    db.execute(
        "UPDATE rounds SET status = 'resolved', resolved_at = ? WHERE id = ?",
        (_now(), current_round["id"]),
    )
    next_round = _create_next_round(current_round["round_number"])
    db.commit()

    report["summaries"] = summaries
    report["next_round"] = {
        "id": next_round["id"],
        "number": next_round["round_number"],
        "status": next_round["status"],
    }
    return report
