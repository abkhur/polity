"""Tests for the tiered context assembler."""

import json

import pytest

from src import server
from src.context import (
    ContextAssembler,
    ContextBudget,
    _build_action_types,
    _build_archive,
    _build_current_state,
    _build_history,
    _build_permissions,
    _build_retrieval,
    _can_govern,
    _derive_permissions,
    _estimate_tokens,
    _is_moderator,
    _is_moderated,
    _archive_restricted,
)
from src.db import init_db
from src.state import infer_policy_kind


@pytest.fixture()
def db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    server.set_db(conn)
    yield conn
    conn.close()


def _join_democracy(db):
    return server.join_society("Test-Agent", consent=True, governance_type="democracy")


def _join_society(name: str, governance_type: str) -> dict:
    return server.join_society(name, consent=True, governance_type=governance_type)


def _enact_policy(db, society_id, proposer_id, policy_type, effect):
    import uuid

    round_row = db.execute(
        "SELECT id FROM rounds WHERE status = 'open' ORDER BY round_number DESC LIMIT 1"
    ).fetchone()
    db.execute(
        """
        INSERT INTO policies (
            id, society_id, proposed_by, title, description, policy_type, effect,
            policy_kind, status, created_round_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'enacted', ?, datetime('now'))
        """,
        (
            str(uuid.uuid4()),
            society_id,
            proposer_id,
            f"Policy {policy_type}",
            f"Effect {policy_type}",
            policy_type,
            json.dumps(effect),
            infer_policy_kind(policy_type),
            round_row["id"],
        ),
    )
    db.commit()


class TestTokenEstimation:
    def test_estimate_tokens_basic(self):
        assert _estimate_tokens("hello world") >= 1

    def test_estimate_tokens_longer(self):
        text = "a" * 400
        assert _estimate_tokens(text) == 100

    def test_estimate_tokens_empty(self):
        assert _estimate_tokens("") == 1


class TestContextBudget:
    def test_try_add_within_budget(self):
        budget = ContextBudget(total=100)
        result = budget.try_add("short text")
        assert result is not None
        assert budget.used > 0

    def test_try_add_exceeds_budget(self):
        budget = ContextBudget(total=5)
        result = budget.try_add("a" * 100)
        assert result is None
        assert budget.used == 0

    def test_force_add_always_works(self):
        budget = ContextBudget(total=1)
        result = budget.force_add("a" * 1000)
        assert result == "a" * 1000
        assert budget.used > 0

    def test_remaining_decreases(self):
        budget = ContextBudget(total=1000)
        initial = budget.remaining
        budget.try_add("some text here")
        assert budget.remaining < initial


class TestPermissions:
    def test_democracy_citizen_can_govern(self):
        assert _can_govern("democracy", "citizen", []) is True

    def test_oligarchy_citizen_cannot_govern(self):
        assert _can_govern("oligarchy", "citizen", []) is False

    def test_oligarchy_oligarch_can_govern(self):
        assert _can_govern("oligarchy", "oligarch", []) is True

    def test_universal_proposal_overrides(self):
        enacted = [{"policy_type": "universal_proposal", "effect": {}}]
        assert _can_govern("oligarchy", "citizen", enacted) is True

    def test_moderation_detected(self):
        enacted = [{"policy_type": "grant_moderation", "effect": {"moderator_roles": ["oligarch"]}}]
        assert _is_moderator("oligarch", enacted) is True
        assert _is_moderator("citizen", enacted) is False
        assert _is_moderated("citizen", enacted) is True
        assert _is_moderated("oligarch", enacted) is False

    def test_archive_restriction(self):
        enacted = [{"policy_type": "restrict_archive", "effect": {"allowed_roles": ["oligarch"]}}]
        assert _archive_restricted("citizen", enacted) is True
        assert _archive_restricted("oligarch", enacted) is False

    def test_permissions_text_democracy(self):
        text = _build_permissions(_derive_permissions("democracy", "citizen", []))
        assert "propose policies" in text
        assert "vote on policies" in text
        assert "cannot" not in text

    def test_permissions_text_oligarchy_citizen(self):
        text = _build_permissions(_derive_permissions("oligarchy", "citizen", []))
        assert "cannot propose" in text
        assert "cannot vote" in text

    def test_permissions_text_moderated(self):
        enacted = [{"policy_type": "grant_moderation", "effect": {"moderator_roles": ["oligarch"]}}]
        text = _build_permissions(
            _derive_permissions("oligarchy", "citizen", enacted),
            needs_message_approval=True,
        )
        assert "moderator approval" in text

    def test_permissions_text_moderator(self):
        enacted = [{"policy_type": "grant_moderation", "effect": {"moderator_roles": ["oligarch"]}}]
        text = _build_permissions(_derive_permissions("oligarchy", "oligarch", enacted))
        assert "approve or reject" in text

    def test_permissions_text_compiled_moderation_marks_review_requirement(self):
        enacted = [
            {
                "title": "Moderator Law",
                "description": "Only oligarchs may approve or reject pending messages.",
                "compiled_clauses": [
                    {"kind": "grant_moderation", "moderator_roles": ["oligarch"]},
                ],
            }
        ]
        assembler = ContextAssembler(token_budget=8000)
        prompt = assembler._build_header(
            {"name": "Citizen", "resources": 100, "actions_remaining": 2, "role": "citizen"},
            {"id": "oligarchy_1"},
            {"number": 1},
            enacted,
            _derive_permissions("oligarchy", "citizen", enacted),
        )
        assert "moderator approval before publication" in prompt

    def test_permissions_text_dm_restricted(self):
        enacted = [{"policy_type": "restrict_direct_messages", "effect": {"allowed_roles": ["oligarch"]}}]
        text = _build_permissions(_derive_permissions("oligarchy", "citizen", enacted))
        assert "cannot send direct messages" in text


class TestActionTypes:
    def test_democracy_citizen_has_propose_and_vote(self):
        text = _build_action_types(_derive_permissions("democracy", "citizen", []))
        assert "propose_policy" in text
        assert "vote_policy" in text
        assert "policy_type" not in text
        assert "effect" not in text
        assert "concrete operational rules" in text

    def test_oligarchy_citizen_no_propose_or_vote(self):
        text = _build_action_types(_derive_permissions("oligarchy", "citizen", []))
        assert "propose_policy" not in text
        assert "vote_policy" not in text

    def test_moderator_gets_approve_reject(self):
        enacted = [{"policy_type": "grant_moderation", "effect": {"moderator_roles": ["oligarch"]}}]
        text = _build_action_types(_derive_permissions("oligarchy", "oligarch", enacted))
        assert "approve_message" in text
        assert "reject_message" in text

    def test_non_moderator_no_approve_reject(self):
        enacted = [{"policy_type": "grant_moderation", "effect": {"moderator_roles": ["oligarch"]}}]
        text = _build_action_types(_derive_permissions("oligarchy", "citizen", enacted))
        assert "approve_message" not in text

    def test_archive_restricted_no_write_archive(self):
        enacted = [{"policy_type": "restrict_archive", "effect": {"allowed_roles": ["oligarch"]}}]
        text = _build_action_types(_derive_permissions("oligarchy", "citizen", enacted))
        assert "write_archive" not in text

    def test_always_has_basic_actions(self):
        text = _build_action_types(_derive_permissions("oligarchy", "citizen", []))
        assert "post_public_message" in text
        assert "send_dm" in text
        assert "gather_resources" in text
        assert "transfer_resources" in text

    def test_dm_restricted_no_send_dm_action(self):
        enacted = [{"policy_type": "restrict_direct_messages", "effect": {"allowed_roles": ["oligarch"]}}]
        text = _build_action_types(_derive_permissions("oligarchy", "citizen", enacted))
        assert "send_dm" not in text


class TestCurrentState:
    def test_includes_pending_policies(self):
        pending = [{"id": "abc12345-1234", "title": "New Rule", "description": "A rule"}]
        text = _build_current_state(pending, [], [], [])
        assert "New Rule" in text
        assert "Pending policies" in text

    def test_includes_messages(self):
        public = [{"from_agent_name": "Alice", "message": "Hello everyone"}]
        direct = [{"from_agent_name": "Bob", "message": "Secret plan"}]
        text = _build_current_state([], public, direct, [])
        assert "Alice" in text
        assert "Hello everyone" in text
        assert "Bob" in text
        assert "Secret plan" in text

    def test_empty_returns_empty(self):
        text = _build_current_state([], [], [], [])
        assert text == ""

    def test_non_voter_pending_policy_label_is_informational(self):
        pending = [{"id": "abc12345-1234", "title": "Closed Vote", "description": "Info only"}]
        text = _build_current_state(pending, [], [], [], can_vote_policy=False)
        assert "informational only" in text

    def test_surveilled_dm_shows_true_recipient(self):
        direct = [
            {
                "from_agent_id": "agent-a",
                "from_agent_name": "Alice",
                "to_agent_id": "agent-b",
                "to_agent_name": "Bob",
                "message": "Keep this quiet",
            }
        ]
        text = _build_current_state([], [], direct, [], agent_id="agent-c")
        assert "Alice -> Bob" in text


class TestHistory:
    def test_returns_summaries(self, db):
        agent_result = _join_democracy(db)
        server.submit_actions(agent_result["agent_id"], [
            {"type": "post_public_message", "message": "Test message"}
        ])
        server.resolve_round()

        text = _build_history(db, "democracy_1", 2, max_summaries=5)
        assert "Society history" in text
        assert "Round 1" in text
        assert "gini=" in text

    def test_empty_when_no_summaries(self, db):
        text = _build_history(db, "democracy_1", 1, max_summaries=5)
        assert text == ""


class TestArchive:
    def test_includes_entries(self):
        entries = [{"title": "Constitution", "content": "We the people establish this society."}]
        text = _build_archive(entries)
        assert "Society archive" in text
        assert "Constitution" in text

    def test_empty_when_no_entries(self):
        assert _build_archive([]) == ""


class TestFullAssembler:
    def test_build_produces_nonempty_prompt(self, db):
        agent_result = _join_democracy(db)
        turn_state = server.get_turn_state(agent_result["agent_id"])

        assembler = ContextAssembler(token_budget=8000)
        prompt = assembler.build(turn_state, db)

        assert "You are Test-Agent in Society" in prompt
        assert "Your role:" in prompt
        assert "Available action types:" in prompt
        assert '"thoughts"' in prompt

    def test_build_contains_permissions(self, db):
        agent_result = _join_democracy(db)
        turn_state = server.get_turn_state(agent_result["agent_id"])

        assembler = ContextAssembler(token_budget=8000)
        prompt = assembler.build(turn_state, db)

        assert "Your role permissions:" in prompt
        assert "propose policies" in prompt

    def test_turn_state_includes_permissions_object(self, db):
        agent_result = _join_democracy(db)
        turn_state = server.get_turn_state(agent_result["agent_id"])

        assert turn_state["permissions"]["can_propose_policy"] is True
        assert turn_state["permissions"]["can_vote_policy"] is True
        assert turn_state["permissions"]["can_write_archive"] is True

    def test_prompt_does_not_claim_oligarchy_citizen_can_vote(self, db):
        agents = [_join_society(f"Olig-{idx}", "oligarchy") for idx in range(4)]
        oligarch = next(agent for agent in agents if agent["role"] == "oligarch")
        citizen = next(agent for agent in agents if agent["role"] == "citizen")

        server.submit_actions(
            oligarch["agent_id"],
            [{"type": "propose_policy", "title": "Control", "description": "Stay the course"}],
        )
        server.resolve_round()

        turn_state = server.get_turn_state(citizen["agent_id"])
        assembler = ContextAssembler(token_budget=8000)
        prompt = assembler.build(turn_state, db)

        assert "your role cannot vote" in prompt
        assert "you can vote on these" not in prompt

    def test_prompt_renders_surveilled_dm_with_true_recipient(self, db):
        a = _join_society("Alice", "democracy")
        b = _join_society("Bob", "democracy")
        c = _join_society("Cara", "democracy")

        server.submit_actions(
            a["agent_id"],
            [{"type": "send_dm", "message": "Private note", "target_agent_id": b["agent_id"]}],
        )
        server.resolve_round()
        _enact_policy(
            db,
            "democracy_1",
            a["agent_id"],
            "grant_access",
            {"target_roles": ["citizen"], "access_type": "direct_messages"},
        )

        turn_state = server.get_turn_state(c["agent_id"])
        assembler = ContextAssembler(token_budget=8000)
        prompt = assembler.build(turn_state, db)

        assert "Alice -> Bob: Private note" in prompt

    def test_build_respects_budget(self, db):
        agent_result = _join_democracy(db)
        turn_state = server.get_turn_state(agent_result["agent_id"])

        assembler = ContextAssembler(token_budget=200)
        prompt = assembler.build(turn_state, db)
        assert len(prompt) > 0

    def test_build_includes_history_after_rounds(self, db):
        agent_result = _join_democracy(db)
        aid = agent_result["agent_id"]

        for _ in range(3):
            server.submit_actions(aid, [
                {"type": "post_public_message", "message": "Round message"}
            ])
            server.resolve_round()

        turn_state = server.get_turn_state(aid)
        assembler = ContextAssembler(token_budget=8000)
        prompt = assembler.build(turn_state, db)

        assert "Society history" in prompt

    def test_build_includes_pending_policies(self, db):
        agent_result = _join_democracy(db)
        aid = agent_result["agent_id"]

        server.submit_actions(aid, [
            {"type": "propose_policy", "title": "Test Policy", "description": "A test"}
        ])
        server.resolve_round()

        turn_state = server.get_turn_state(aid)
        assembler = ContextAssembler(token_budget=8000)
        prompt = assembler.build(turn_state, db)

        assert "Pending policies" in prompt
        assert "Test Policy" in prompt

    def test_build_shows_enacted_policies(self, db):
        agent_result = _join_democracy(db)
        aid = agent_result["agent_id"]

        server.submit_actions(aid, [
            {"type": "propose_policy", "title": "Tax Act", "description": "10% tax",
             "policy_type": "resource_tax", "effect": {"rate": 0.1}}
        ])
        server.resolve_round()

        policy = db.execute("SELECT id FROM policies WHERE status = 'proposed'").fetchone()
        server.submit_actions(aid, [
            {"type": "vote_policy", "policy_id": policy["id"], "stance": "support"}
        ])
        server.resolve_round()

        turn_state = server.get_turn_state(aid)
        assembler = ContextAssembler(token_budget=8000)
        prompt = assembler.build(turn_state, db)

        assert "Enacted policies:" in prompt
        assert "Tax Act" in prompt
        assert "[mechanical" not in prompt
        assert "[symbolic" not in prompt
        assert "[resource_tax" not in prompt

    def test_pending_mechanical_policy_shows_plain_language_effect(self, db):
        agent_result = _join_democracy(db)
        aid = agent_result["agent_id"]

        server.submit_actions(aid, [
            {
                "type": "propose_policy",
                "title": "Revenue Act",
                "description": "Pool reform",
                "policy_type": "resource_tax",
                "effect": {"rate": 0.1},
            }
        ])
        server.resolve_round()

        turn_state = server.get_turn_state(aid)
        assembler = ContextAssembler(token_budget=8000)
        prompt = assembler.build(turn_state, db)

        assert "Revenue Act" in prompt
        assert "if enacted, this would enforce: A 10% tax" in prompt

    def test_enacted_mechanical_policy_shows_plain_language_effect(self, db):
        agent_result = _join_democracy(db)
        aid = agent_result["agent_id"]

        server.submit_actions(aid, [
            {
                "type": "propose_policy",
                "title": "Revenue Act",
                "description": "Pool reform",
                "policy_type": "resource_tax",
                "effect": {"rate": 0.1},
            }
        ])
        server.resolve_round()

        policy = db.execute("SELECT id FROM policies WHERE status = 'proposed'").fetchone()
        server.submit_actions(aid, [
            {"type": "vote_policy", "policy_id": policy["id"], "stance": "support"}
        ])
        server.resolve_round()

        turn_state = server.get_turn_state(aid)
        assembler = ContextAssembler(token_budget=8000)
        prompt = assembler.build(turn_state, db)

        assert "Revenue Act" in prompt
        assert "enforced rules: A 10% tax" in prompt


class TestEmbeddingStorage:
    def test_message_events_have_embeddings(self, db):
        agent_result = _join_democracy(db)
        aid = agent_result["agent_id"]
        server.submit_actions(aid, [
            {"type": "post_public_message", "message": "Democracy is the best form of governance."}
        ])
        server.resolve_round()

        rows = db.execute(
            "SELECT embedding FROM events WHERE event_type = 'public_message' AND embedding IS NOT NULL"
        ).fetchall()
        assert len(rows) >= 1
        assert len(rows[0]["embedding"]) == 384 * 4

    def test_dm_events_have_embeddings(self, db):
        r1 = _join_democracy(db)
        r2 = _join_democracy(db)
        server.submit_actions(r1["agent_id"], [
            {"type": "send_dm", "message": "Secret coordination", "target_agent_id": r2["agent_id"]}
        ])
        server.resolve_round()

        rows = db.execute(
            "SELECT embedding FROM events WHERE event_type = 'direct_message' AND embedding IS NOT NULL"
        ).fetchall()
        assert len(rows) >= 1
        assert len(rows[0]["embedding"]) == 384 * 4

    def test_tier4_retrieval_with_embeddings(self, db):
        agent_result = _join_democracy(db)
        aid = agent_result["agent_id"]
        for msg in [
            "We must redistribute wealth equally among all citizens.",
            "The oligarchs are hoarding resources unfairly.",
            "Let's vote on a new tax policy for fairness.",
        ]:
            server.submit_actions(aid, [{"type": "post_public_message", "message": msg}])
            server.resolve_round()

        result = _build_retrieval(
            db, "democracy_1", "taxation and wealth redistribution",
            already_seen_event_ids=set(), top_k=3,
        )
        assert "Relevant past context" in result
