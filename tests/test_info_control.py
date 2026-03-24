"""Tests for information-control primitives: moderation, access grants."""

import json
import sqlite3

import pytest

from src import server
from src.db import init_db


@pytest.fixture()
def db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    server.set_db(conn)
    yield conn
    conn.close()


def _join(society: str, name: str = "Agent"):
    return server.join_society(name, consent=True, governance_type=society)


def _enact_policy(db, society_id, title, description, policy_type, effect, proposer_id):
    """Propose and instantly enact a policy by manipulating the DB."""
    import uuid
    policy_id = str(uuid.uuid4())
    round_row = db.execute("SELECT id FROM rounds WHERE status = 'open' LIMIT 1").fetchone()
    round_id = round_row["id"] if round_row else 1
    db.execute(
        """
        INSERT INTO policies (id, society_id, proposed_by, title, description, policy_type, effect, status, created_round_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'enacted', ?, datetime('now'))
        """,
        (policy_id, society_id, proposer_id, title, description, policy_type, json.dumps(effect), round_id),
    )
    db.commit()
    return policy_id


class TestGrantModeration:
    def test_moderation_holds_messages(self, db):
        r1 = _join("democracy", "Citizen-A")
        _enact_policy(
            db, "democracy_1", "Appoint Moderators", "Oligarchs moderate",
            "grant_moderation", {"moderator_roles": ["oligarch"]},
            r1["agent_id"],
        )

        server.submit_actions(r1["agent_id"], [
            {"type": "post_public_message", "message": "Dissenting opinion here"}
        ])
        report = server.resolve_round()

        pending = [m for m in report["resolved"]["messages"] if m.get("moderation") == "pending_review"]
        assert len(pending) == 1

        events = db.execute(
            "SELECT * FROM events WHERE event_type = 'message_pending_review' AND society_id = 'democracy_1'"
        ).fetchall()
        assert len(events) >= 1

    def test_moderator_bypasses_moderation(self, db):
        """Messages from agents with moderator roles are not held."""
        r1 = server.join_society("Oligarch-A", consent=True, governance_type="oligarchy")

        _enact_policy(
            db, "oligarchy_1", "Self-moderate", "Oligarchs moderate",
            "grant_moderation", {"moderator_roles": ["oligarch"]},
            r1["agent_id"],
        )

        server.submit_actions(r1["agent_id"], [
            {"type": "post_public_message", "message": "Official statement"}
        ])
        report = server.resolve_round()

        normal = [m for m in report["resolved"]["messages"] if m.get("moderation") is None]
        assert len(normal) >= 1

    def _setup_moderated_oligarchy(self, db):
        """Create an oligarchy with 3 oligarchs + 1 citizen, moderation enacted."""
        oligarchs = [server.join_society(f"Olig-{i}", consent=True, governance_type="oligarchy") for i in range(3)]
        citizen = server.join_society("Citizen-X", consent=True, governance_type="oligarchy")

        assert citizen["role"] == "citizen"
        assert oligarchs[0]["role"] == "oligarch"

        _enact_policy(
            db, "oligarchy_1", "Moderate All", "Oligarchs moderate",
            "grant_moderation", {"moderator_roles": ["oligarch"]},
            oligarchs[0]["agent_id"],
        )
        return oligarchs, citizen

    def test_approve_message_publishes(self, db):
        oligarchs, citizen = self._setup_moderated_oligarchy(db)

        server.submit_actions(citizen["agent_id"], [
            {"type": "post_public_message", "message": "Please let this through"}
        ])
        server.resolve_round()

        pending_row = db.execute(
            "SELECT id FROM queued_actions WHERE moderation_status = 'pending_review'"
        ).fetchone()
        assert pending_row is not None

        server.submit_actions(oligarchs[0]["agent_id"], [
            {"type": "approve_message", "message_action_id": pending_row["id"]}
        ])
        report = server.resolve_round()

        approved_msgs = [m for m in report["resolved"]["messages"] if m.get("moderation") == "approved"]
        assert len(approved_msgs) == 1

        comms = db.execute(
            "SELECT * FROM communications WHERE message = 'Please let this through'"
        ).fetchall()
        assert len(comms) >= 1

    def test_reject_message_blocks(self, db):
        oligarchs, citizen = self._setup_moderated_oligarchy(db)

        server.submit_actions(citizen["agent_id"], [
            {"type": "post_public_message", "message": "Subversive content"}
        ])
        server.resolve_round()

        pending_row = db.execute(
            "SELECT id FROM queued_actions WHERE moderation_status = 'pending_review'"
        ).fetchone()
        assert pending_row is not None

        server.submit_actions(oligarchs[0]["agent_id"], [
            {"type": "reject_message", "message_action_id": pending_row["id"]}
        ])
        server.resolve_round()

        rejected = db.execute(
            "SELECT moderation_status FROM queued_actions WHERE id = ?",
            (pending_row["id"],),
        ).fetchone()
        assert rejected["moderation_status"] == "rejected"

        comms = db.execute(
            "SELECT * FROM communications WHERE message = 'Subversive content'"
        ).fetchall()
        assert len(comms) == 0


class TestGrantAccess:
    def test_dm_access_grants_visibility(self, db):
        r1 = _join("democracy", "A")
        r2 = _join("democracy", "B")
        r3 = _join("democracy", "C")

        server.submit_actions(r1["agent_id"], [
            {"type": "send_dm", "message": "Secret plan", "target_agent_id": r2["agent_id"]}
        ])
        server.resolve_round()

        dms_before = server._visible_direct_messages(r3["agent_id"])
        assert len(dms_before) == 0

        _enact_policy(
            db, "democracy_1", "Transparency Act", "Everyone sees DMs",
            "grant_access", {"target_roles": ["citizen"], "access_type": "direct_messages"},
            r1["agent_id"],
        )

        dms_after = server._visible_direct_messages(r3["agent_id"])
        assert len(dms_after) >= 1

    def test_no_access_without_policy(self, db):
        r1 = _join("democracy", "D")
        r2 = _join("democracy", "E")
        r3 = _join("democracy", "F")

        server.submit_actions(r1["agent_id"], [
            {"type": "send_dm", "message": "Private", "target_agent_id": r2["agent_id"]}
        ])
        server.resolve_round()

        dms = server._visible_direct_messages(r3["agent_id"])
        assert len(dms) == 0


class TestModerationRejectionMetric:
    def test_metric_in_summary(self, db):
        r1 = _join("democracy", "M")
        server.submit_actions(r1["agent_id"], [
            {"type": "post_public_message", "message": "test"}
        ])
        server.resolve_round()

        rows = db.execute("SELECT summary FROM round_summaries WHERE society_id = 'democracy_1'").fetchall()
        assert len(rows) >= 1
        s = json.loads(rows[-1]["summary"])
        assert "moderation_rejection_rate" in s["metrics"]
