"""Tests for the core server: joining, actions, budgets, and round resolution."""

import json
import sqlite3

import pytest

from src import server
from src.state import infer_policy_kind


# ---------------------------------------------------------------------------
# Joining
# ---------------------------------------------------------------------------


class TestJoin:
    def test_join_returns_agent_id(self, db: sqlite3.Connection) -> None:
        result = server.join_society("Alice", consent=True)
        assert "agent_id" in result
        assert "society_id" in result
        assert result["role"] in ("citizen", "oligarch")

    def test_join_requires_consent(self, db: sqlite3.Connection) -> None:
        result = server.join_society("Alice", consent=False)
        assert "error" in result

    def test_join_increments_population(self, db: sqlite3.Connection) -> None:
        result = server.join_society("Alice", consent=True)
        society = db.execute(
            "SELECT population FROM societies WHERE id = ?",
            (result["society_id"],),
        ).fetchone()
        assert society["population"] >= 1

    def test_join_creates_agent_row(self, db: sqlite3.Connection) -> None:
        result = server.join_society("Alice", consent=True)
        agent = db.execute(
            "SELECT * FROM agents WHERE id = ?", (result["agent_id"],)
        ).fetchone()
        assert agent is not None
        assert agent["name"] == "Alice"
        assert agent["status"] == "active"

    def test_join_emits_event(self, db: sqlite3.Connection) -> None:
        result = server.join_society("Alice", consent=True)
        events = db.execute(
            "SELECT * FROM events WHERE agent_id = ? AND event_type = 'join'",
            (result["agent_id"],),
        ).fetchall()
        assert len(events) == 1

    def test_oligarchy_role_assignment(self, joined_oligarchy: list[dict]) -> None:
        roles = [r["role"] for r in joined_oligarchy]
        assert roles.count("oligarch") == 3
        assert roles.count("citizen") == 1

    def test_oligarchy_resource_inequality(self, joined_oligarchy: list[dict]) -> None:
        oligarch_resources = [
            r["starting_resources"] for r in joined_oligarchy if r["role"] == "oligarch"
        ]
        citizen_resources = [
            r["starting_resources"] for r in joined_oligarchy if r["role"] == "citizen"
        ]
        assert all(r == 500 for r in oligarch_resources)
        assert all(r == 10 for r in citizen_resources)

    def test_democracy_equal_resources(self, db: sqlite3.Connection) -> None:
        results = [server.join_society(f"D-{i}", consent=True, governance_type="democracy") for i in range(3)]
        resources = [r["starting_resources"] for r in results]
        assert all(r == 100 for r in resources)


# ---------------------------------------------------------------------------
# Turn state
# ---------------------------------------------------------------------------


class TestTurnState:
    def test_turn_state_returns_full_bundle(self, joined_democracy: dict) -> None:
        state = server.get_turn_state(joined_democracy["agent_id"])
        assert "round" in state
        assert "agent" in state
        assert "society" in state
        assert "permissions" in state
        assert "visible_messages" in state
        assert "relevant_laws" in state
        assert "pending_policies" in state

    def test_turn_state_shows_correct_budget(self, joined_democracy: dict) -> None:
        state = server.get_turn_state(joined_democracy["agent_id"])
        assert state["agent"]["action_budget"] == 2
        assert state["agent"]["actions_remaining"] == 2

    def test_turn_state_inactive_agent_raises(self, joined_democracy: dict) -> None:
        server.leave_society(joined_democracy["agent_id"], confirm=True)
        with pytest.raises(ValueError, match="inactive"):
            server.get_turn_state(joined_democracy["agent_id"])

    def test_turn_state_unknown_agent_raises(self, db: sqlite3.Connection) -> None:
        with pytest.raises(ValueError, match="not found"):
            server.get_turn_state("nonexistent-agent-id")

    def test_turn_state_permissions_reflect_enacted_policies(self, db: sqlite3.Connection) -> None:
        agents = [server.join_society(f"Olig-{idx}", consent=True, governance_type="oligarchy") for idx in range(4)]

        citizen = next(agent for agent in agents if agent["role"] == "citizen")
        proposer = next(agent for agent in agents if agent["role"] == "oligarch")
        round_row = db.execute(
            "SELECT id FROM rounds WHERE status = 'open' ORDER BY round_number DESC LIMIT 1"
        ).fetchone()

        for idx, (policy_type, effect) in enumerate(
            [
                ("universal_proposal", {}),
                ("restrict_archive", {"allowed_roles": ["oligarch"]}),
                ("restrict_direct_messages", {"allowed_roles": ["oligarch"]}),
                ("grant_moderation", {"moderator_roles": ["oligarch"]}),
                ("grant_access", {"target_roles": ["citizen"], "access_type": "direct_messages"}),
            ],
            start=1,
        ):
            db.execute(
                """
                INSERT INTO policies (
                    id, society_id, proposed_by, title, description, policy_type, effect,
                    policy_kind, status, created_round_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'enacted', ?, datetime('now'))
                """,
                (
                    f"policy-{idx}",
                    "oligarchy_1",
                    proposer["agent_id"],
                    f"Policy {idx}",
                    "test",
                    policy_type,
                    json.dumps(effect),
                    infer_policy_kind(policy_type),
                    round_row["id"],
                ),
            )
        db.commit()

        state = server.get_turn_state(citizen["agent_id"])
        assert state["permissions"]["can_propose_policy"] is True
        assert state["permissions"]["can_vote_policy"] is True
        assert state["permissions"]["can_send_direct_messages"] is False
        assert state["permissions"]["can_write_archive"] is False
        assert state["permissions"]["can_moderate_messages"] is False
        assert state["permissions"]["can_view_society_dms"] is True


# ---------------------------------------------------------------------------
# Action submission
# ---------------------------------------------------------------------------


class TestSubmitActions:
    def test_submit_public_message(self, joined_democracy: dict) -> None:
        result = server.submit_actions(
            joined_democracy["agent_id"],
            [{"type": "post_public_message", "message": "Hello world"}],
        )
        assert result["success"] is True
        assert result["actions_remaining"] == 1

    def test_submit_gather_resources(self, joined_democracy: dict) -> None:
        result = server.submit_actions(
            joined_democracy["agent_id"],
            [{"type": "gather_resources", "amount": 10}],
        )
        assert result["success"] is True

    def test_submit_exceeds_budget(self, joined_democracy: dict) -> None:
        result = server.submit_actions(
            joined_democracy["agent_id"],
            [
                {"type": "post_public_message", "message": "One"},
                {"type": "post_public_message", "message": "Two"},
                {"type": "post_public_message", "message": "Three"},
            ],
        )
        assert "error" in result

    def test_budget_decreases_after_submit(self, joined_democracy: dict) -> None:
        server.submit_actions(
            joined_democracy["agent_id"],
            [{"type": "post_public_message", "message": "First"}],
        )
        state = server.get_turn_state(joined_democracy["agent_id"])
        assert state["agent"]["actions_remaining"] == 1

    def test_no_actions_after_budget_exhausted(self, joined_democracy: dict) -> None:
        server.submit_actions(
            joined_democracy["agent_id"],
            [
                {"type": "post_public_message", "message": "One"},
                {"type": "post_public_message", "message": "Two"},
            ],
        )
        result = server.submit_actions(
            joined_democracy["agent_id"],
            [{"type": "post_public_message", "message": "Three"}],
        )
        assert "error" in result
        assert result["actions_remaining"] == 0

    def test_invalid_action_type_rejected(self, joined_democracy: dict) -> None:
        result = server.submit_actions(
            joined_democracy["agent_id"],
            [{"type": "launch_nukes"}],
        )
        assert "error" in result

    def test_empty_message_rejected(self, joined_democracy: dict) -> None:
        result = server.submit_actions(
            joined_democracy["agent_id"],
            [{"type": "post_public_message", "message": ""}],
        )
        assert "error" in result

    def test_gather_zero_rejected(self, joined_democracy: dict) -> None:
        result = server.submit_actions(
            joined_democracy["agent_id"],
            [{"type": "gather_resources", "amount": 0}],
        )
        assert "error" in result

    def test_dm_requires_target(self, joined_democracy: dict) -> None:
        result = server.submit_actions(
            joined_democracy["agent_id"],
            [{"type": "send_dm", "message": "Hey"}],
        )
        assert "error" in result

    def test_self_dm_rejected(self, joined_democracy: dict) -> None:
        result = server.submit_actions(
            joined_democracy["agent_id"],
            [
                {
                    "type": "send_dm",
                    "message": "Talking to myself",
                    "target_agent_id": joined_democracy["agent_id"],
                }
            ],
        )
        assert "error" in result

    def test_cross_society_dm_blocked(
        self, populated_societies: dict[str, list[dict]]
    ) -> None:
        sender = populated_societies["democracy"][0]
        target = populated_societies["oligarchy"][0]
        result = server.submit_actions(
            sender["agent_id"],
            [
                {
                    "type": "send_dm",
                    "message": "Cross-border hello",
                    "target_agent_id": target["agent_id"],
                }
            ],
        )
        assert "error" in result

    def test_policy_proposal_requires_title_and_description(
        self, joined_democracy: dict
    ) -> None:
        result = server.submit_actions(
            joined_democracy["agent_id"],
            [{"type": "propose_policy", "title": "", "description": "Something"}],
        )
        assert "error" in result

    def test_citizen_cannot_propose_in_oligarchy(
        self, joined_oligarchy: list[dict]
    ) -> None:
        citizen = next(r for r in joined_oligarchy if r["role"] == "citizen")
        result = server.submit_actions(
            citizen["agent_id"],
            [
                {
                    "type": "propose_policy",
                    "title": "Freedom",
                    "description": "Give us rights",
                }
            ],
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# Round resolution
# ---------------------------------------------------------------------------


class TestRoundResolution:
    def test_resolve_empty_round(self, db: sqlite3.Connection) -> None:
        report = server.resolve_round()
        assert report["queued_action_count"] == 0
        assert "next_round" in report

    def test_resolve_advances_round(self, db: sqlite3.Connection) -> None:
        report = server.resolve_round()
        assert report["next_round"]["number"] == 2
        assert report["next_round"]["status"] == "open"

    def test_resolve_wrong_round_rejected(self, db: sqlite3.Connection) -> None:
        result = server.resolve_round(round_number=999)
        assert "error" in result

    def test_messages_appear_in_events(self, joined_democracy: dict) -> None:
        server.submit_actions(
            joined_democracy["agent_id"],
            [{"type": "post_public_message", "message": "Testing 123"}],
        )
        report = server.resolve_round()
        assert len(report["resolved"]["messages"]) == 1
        assert report["resolved"]["messages"][0]["message"] == "Testing 123"

    def test_resource_gathering_updates_agent(
        self, joined_democracy: dict, db: sqlite3.Connection
    ) -> None:
        aid = joined_democracy["agent_id"]
        before = db.execute(
            "SELECT resources FROM agents WHERE id = ?", (aid,)
        ).fetchone()["resources"]

        server.submit_actions(aid, [{"type": "gather_resources", "amount": 20}])
        server.resolve_round()

        after = db.execute(
            "SELECT resources FROM agents WHERE id = ?", (aid,)
        ).fetchone()["resources"]
        assert after > before

    def test_resource_gathering_depletes_society(
        self, joined_democracy: dict, db: sqlite3.Connection
    ) -> None:
        sid = joined_democracy["society_id"]
        before = db.execute(
            "SELECT total_resources FROM societies WHERE id = ?", (sid,)
        ).fetchone()["total_resources"]

        server.submit_actions(
            joined_democracy["agent_id"],
            [{"type": "gather_resources", "amount": 50}],
        )
        server.resolve_round()

        after = db.execute(
            "SELECT total_resources FROM societies WHERE id = ?", (sid,)
        ).fetchone()["total_resources"]
        assert after < before

    def test_resource_split_when_overdemanded(
        self, populated_societies: dict[str, list[dict]], db: sqlite3.Connection
    ) -> None:
        agents = populated_societies["democracy"]
        sid = agents[0]["society_id"]

        society_total = db.execute(
            "SELECT total_resources FROM societies WHERE id = ?", (sid,)
        ).fetchone()["total_resources"]

        for a in agents:
            server.submit_actions(
                a["agent_id"],
                [{"type": "gather_resources", "amount": society_total}],
            )

        server.resolve_round()

        gathered = []
        for a in agents:
            r = db.execute(
                "SELECT resources FROM agents WHERE id = ?", (a["agent_id"],)
            ).fetchone()["resources"]
            gathered.append(r - a["starting_resources"])

        assert sum(gathered) <= society_total

    def test_policy_proposal_creates_policy(
        self, joined_democracy: dict, db: sqlite3.Connection
    ) -> None:
        server.submit_actions(
            joined_democracy["agent_id"],
            [
                {
                    "type": "propose_policy",
                    "title": "Test Policy",
                    "description": "A test policy for testing.",
                }
            ],
        )
        server.resolve_round()

        policies = db.execute(
            "SELECT * FROM policies WHERE title = 'Test Policy'"
        ).fetchall()
        assert len(policies) == 1
        assert policies[0]["status"] == "proposed"
        assert policies[0]["policy_kind"] == "symbolic"

    def test_mechanical_policy_proposal_tagged_mechanical(
        self, joined_democracy: dict, db: sqlite3.Connection
    ) -> None:
        server.submit_actions(
            joined_democracy["agent_id"],
            [
                {
                    "type": "propose_policy",
                    "title": "Cap Gathering",
                    "description": "Cap gathering at 10.",
                    "policy_type": "gather_cap",
                    "effect": {"max_amount": 10},
                }
            ],
        )
        server.resolve_round()

        policy = db.execute(
            "SELECT policy_kind FROM policies WHERE title = 'Cap Gathering'"
        ).fetchone()
        assert policy["policy_kind"] == "mechanical"

    def test_policy_not_votable_same_round(
        self, joined_democracy: dict
    ) -> None:
        server.submit_actions(
            joined_democracy["agent_id"],
            [
                {
                    "type": "propose_policy",
                    "title": "Instant Vote",
                    "description": "Try to vote on this immediately.",
                }
            ],
        )
        report = server.resolve_round()

        policy_id = report["resolved"]["proposals"][0]["policy_id"]
        result = server.submit_actions(
            joined_democracy["agent_id"],
            [{"type": "vote_policy", "policy_id": policy_id, "stance": "support"}],
        )
        server.resolve_round()
        # The vote should have been cast in round 2 on a policy from round 1 — this is valid
        assert result["success"] is True

    def test_policy_enacted_by_majority(
        self, populated_societies: dict[str, list[dict]], db: sqlite3.Connection
    ) -> None:
        agents = populated_societies["democracy"]
        proposer = agents[0]

        server.submit_actions(
            proposer["agent_id"],
            [
                {
                    "type": "propose_policy",
                    "title": "Majority Test",
                    "description": "Should be enacted.",
                }
            ],
        )
        report = server.resolve_round()
        policy_id = report["resolved"]["proposals"][0]["policy_id"]

        for a in agents:
            server.submit_actions(
                a["agent_id"],
                [{"type": "vote_policy", "policy_id": policy_id, "stance": "support"}],
            )
        server.resolve_round()

        policy = db.execute(
            "SELECT status FROM policies WHERE id = ?", (policy_id,)
        ).fetchone()
        assert policy["status"] == "enacted"

    def test_policy_rejected_by_majority(
        self, populated_societies: dict[str, list[dict]], db: sqlite3.Connection
    ) -> None:
        agents = populated_societies["democracy"]

        server.submit_actions(
            agents[0]["agent_id"],
            [
                {
                    "type": "propose_policy",
                    "title": "Unpopular Act",
                    "description": "Should be rejected.",
                }
            ],
        )
        report = server.resolve_round()
        policy_id = report["resolved"]["proposals"][0]["policy_id"]

        for a in agents:
            server.submit_actions(
                a["agent_id"],
                [{"type": "vote_policy", "policy_id": policy_id, "stance": "oppose"}],
            )
        server.resolve_round()

        policy = db.execute(
            "SELECT status FROM policies WHERE id = ?", (policy_id,)
        ).fetchone()
        assert policy["status"] == "rejected"

    def test_enacted_policy_archived(
        self, populated_societies: dict[str, list[dict]], db: sqlite3.Connection
    ) -> None:
        agents = populated_societies["democracy"]

        server.submit_actions(
            agents[0]["agent_id"],
            [
                {
                    "type": "propose_policy",
                    "title": "Archivable",
                    "description": "Should appear in archive when enacted.",
                }
            ],
        )
        report = server.resolve_round()
        policy_id = report["resolved"]["proposals"][0]["policy_id"]

        for a in agents:
            server.submit_actions(
                a["agent_id"],
                [{"type": "vote_policy", "policy_id": policy_id, "stance": "support"}],
            )
        server.resolve_round()

        archive = db.execute(
            "SELECT * FROM archive_entries WHERE title LIKE '%Archivable%'"
        ).fetchall()
        assert len(archive) == 1

    def test_archive_write_creates_entry(
        self, joined_democracy: dict, db: sqlite3.Connection
    ) -> None:
        server.submit_actions(
            joined_democracy["agent_id"],
            [
                {
                    "type": "write_archive",
                    "title": "Founding Doc",
                    "content": "We hold these truths.",
                }
            ],
        )
        server.resolve_round()

        entries = db.execute(
            "SELECT * FROM archive_entries WHERE title = 'Founding Doc'"
        ).fetchall()
        assert len(entries) == 1
        assert entries[0]["status"] == "active"

    def test_round_summaries_generated(
        self, joined_democracy: dict, db: sqlite3.Connection
    ) -> None:
        server.submit_actions(
            joined_democracy["agent_id"],
            [{"type": "post_public_message", "message": "Summary test"}],
        )
        report = server.resolve_round()
        assert len(report["summaries"]) == 3  # one per society

    def test_dm_visible_to_recipient(
        self, populated_societies: dict[str, list[dict]]
    ) -> None:
        agents = populated_societies["democracy"]
        sender, recipient = agents[0], agents[1]

        server.submit_actions(
            sender["agent_id"],
            [
                {
                    "type": "send_dm",
                    "message": "Secret message",
                    "target_agent_id": recipient["agent_id"],
                }
            ],
        )
        server.resolve_round()

        state = server.get_turn_state(recipient["agent_id"])
        dm_messages = [
            m["message"] for m in state["visible_messages"]["direct"]
        ]
        assert "Secret message" in dm_messages

    def test_dm_rejected_if_target_leaves_before_resolution(
        self, db: sqlite3.Connection
    ) -> None:
        sender = server.join_society("Alice", consent=True, governance_type="democracy")
        recipient = server.join_society("Bob", consent=True, governance_type="democracy")

        server.submit_actions(
            sender["agent_id"],
            [{"type": "send_dm", "message": "Still there?", "target_agent_id": recipient["agent_id"]}],
        )
        server.leave_society(recipient["agent_id"], confirm=True)
        report = server.resolve_round()

        assert report["round_number"] == 1
        rejected = db.execute(
            """
            SELECT result
            FROM queued_actions
            WHERE agent_id = ? AND action_type = 'send_dm' AND status = 'rejected'
            ORDER BY id DESC
            LIMIT 1
            """,
            (sender["agent_id"],),
        ).fetchone()
        assert rejected is not None
        assert "inactive at resolution time" in json.loads(rejected["result"])["error"]

    def test_inactive_sender_actions_are_rejected_at_resolution(
        self, db: sqlite3.Connection
    ) -> None:
        sender = server.join_society("Alice", consent=True, governance_type="democracy")

        server.submit_actions(
            sender["agent_id"],
            [{"type": "post_public_message", "message": "This should not publish"}],
        )
        server.leave_society(sender["agent_id"], confirm=True)
        report = server.resolve_round()

        assert report["round_number"] == 1
        rejected = db.execute(
            """
            SELECT result
            FROM queued_actions
            WHERE agent_id = ? AND action_type = 'post_public_message' AND status = 'rejected'
            ORDER BY id DESC
            LIMIT 1
            """,
            (sender["agent_id"],),
        ).fetchone()
        assert rejected is not None
        assert "inactive at resolution time" in json.loads(rejected["result"])["error"]


# ---------------------------------------------------------------------------
# Leave
# ---------------------------------------------------------------------------


class TestLeave:
    def test_leave_deactivates_agent(
        self, joined_democracy: dict, db: sqlite3.Connection
    ) -> None:
        server.leave_society(joined_democracy["agent_id"], confirm=True)
        agent = db.execute(
            "SELECT status FROM agents WHERE id = ?",
            (joined_democracy["agent_id"],),
        ).fetchone()
        assert agent["status"] == "inactive"

    def test_leave_decrements_population(
        self, joined_democracy: dict, db: sqlite3.Connection
    ) -> None:
        sid = joined_democracy["society_id"]
        before = db.execute(
            "SELECT population FROM societies WHERE id = ?", (sid,)
        ).fetchone()["population"]

        server.leave_society(joined_democracy["agent_id"], confirm=True)

        after = db.execute(
            "SELECT population FROM societies WHERE id = ?", (sid,)
        ).fetchone()["population"]
        assert after == before - 1

    def test_leave_requires_confirm(self, joined_democracy: dict) -> None:
        result = server.leave_society(joined_democracy["agent_id"], confirm=False)
        assert "error" in result


class TestResourceTransfers:
    @staticmethod
    def _join_democracy():
        return server.join_society("Dem-Agent", consent=True, governance_type="democracy")

    def test_transfer_moves_resources(self, db: sqlite3.Connection) -> None:
        r1 = self._join_democracy()
        r2 = self._join_democracy()

        server.submit_actions(r1["agent_id"], [
            {"type": "transfer_resources", "target_agent_id": r2["agent_id"], "amount": 30}
        ])
        server.resolve_round()

        sender = db.execute("SELECT resources FROM agents WHERE id = ?", (r1["agent_id"],)).fetchone()
        receiver = db.execute("SELECT resources FROM agents WHERE id = ?", (r2["agent_id"],)).fetchone()
        from src.server import UPKEEP_COST
        assert sender["resources"] == 70 - UPKEEP_COST
        assert receiver["resources"] == 130 - UPKEEP_COST

    def test_transfer_requires_positive_amount(self, db: sqlite3.Connection) -> None:
        r1 = self._join_democracy()
        r2 = self._join_democracy()

        result = server.submit_actions(r1["agent_id"], [
            {"type": "transfer_resources", "target_agent_id": r2["agent_id"], "amount": 0}
        ])
        assert "error" in result

    def test_transfer_rejects_insufficient_resources(self, db: sqlite3.Connection) -> None:
        r1 = self._join_democracy()
        r2 = self._join_democracy()

        result = server.submit_actions(r1["agent_id"], [
            {"type": "transfer_resources", "target_agent_id": r2["agent_id"], "amount": 9999}
        ])
        assert "error" in result

    def test_transfer_requires_target(self, db: sqlite3.Connection) -> None:
        r1 = self._join_democracy()
        result = server.submit_actions(r1["agent_id"], [
            {"type": "transfer_resources", "amount": 10}
        ])
        assert "error" in result

    def test_self_transfer_rejected(self, db: sqlite3.Connection) -> None:
        r1 = self._join_democracy()
        result = server.submit_actions(
            r1["agent_id"],
            [{"type": "transfer_resources", "target_agent_id": r1["agent_id"], "amount": 10}],
        )
        assert "error" in result

    def test_cross_society_transfer_blocked(self, db: sqlite3.Connection, joined_oligarchy: list[dict]) -> None:
        r1 = self._join_democracy()
        result = server.submit_actions(r1["agent_id"], [
            {"type": "transfer_resources", "target_agent_id": joined_oligarchy[0]["agent_id"], "amount": 10}
        ])
        assert "error" in result

    def test_transfer_emits_event(self, db: sqlite3.Connection) -> None:
        r1 = self._join_democracy()
        r2 = self._join_democracy()

        server.submit_actions(r1["agent_id"], [
            {"type": "transfer_resources", "target_agent_id": r2["agent_id"], "amount": 20}
        ])
        report = server.resolve_round()

        events = db.execute(
            "SELECT * FROM events WHERE event_type = 'resource_transfer'"
        ).fetchall()
        assert len(events) >= 1
        assert len(report["resolved"]["resource_transfers"]) == 1

    def test_transfer_rejected_if_resources_spent_earlier_in_queue(self, db: sqlite3.Connection) -> None:
        r1 = self._join_democracy()
        r2 = self._join_democracy()
        r3 = self._join_democracy()

        server.submit_actions(
            r1["agent_id"],
            [
                {"type": "transfer_resources", "target_agent_id": r2["agent_id"], "amount": 80},
                {"type": "transfer_resources", "target_agent_id": r3["agent_id"], "amount": 80},
            ],
        )
        report = server.resolve_round()

        transfers = report["resolved"]["resource_transfers"]
        assert len(transfers) == 1

        rejected = db.execute(
            """
            SELECT result
            FROM queued_actions
            WHERE agent_id = ? AND action_type = 'transfer_resources' AND status = 'rejected'
            ORDER BY id DESC
            LIMIT 1
            """,
            (r1["agent_id"],),
        ).fetchone()
        assert rejected is not None
        assert "Insufficient resources at resolution time" in json.loads(rejected["result"])["error"]

    def test_transfer_rejected_if_target_leaves_before_resolution(self, db: sqlite3.Connection) -> None:
        sender = self._join_democracy()
        recipient = self._join_democracy()

        server.submit_actions(
            sender["agent_id"],
            [{"type": "transfer_resources", "target_agent_id": recipient["agent_id"], "amount": 20}],
        )
        server.leave_society(recipient["agent_id"], confirm=True)
        report = server.resolve_round()

        assert report["round_number"] == 1
        rejected = db.execute(
            """
            SELECT result
            FROM queued_actions
            WHERE agent_id = ? AND action_type = 'transfer_resources' AND status = 'rejected'
            ORDER BY id DESC
            LIMIT 1
            """,
            (sender["agent_id"],),
        ).fetchone()
        assert rejected is not None
        assert "inactive at resolution time" in json.loads(rejected["result"])["error"]
