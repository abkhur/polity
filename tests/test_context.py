"""Tests for the tiered context assembler."""

import json

import pytest

from src import server
from src.context import (
    ContextAssembler,
    ContextBudget,
    _build_tier0_identity,
    _build_tier1_immediate,
    _build_tier2_history,
    _build_tier3_archive,
    _estimate_tokens,
)
from src.db import init_db


@pytest.fixture()
def db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    server.set_db(conn)
    yield conn
    conn.close()


def _join_democracy(db):
    import random
    original = random.choice
    random.choice = lambda seq: "democracy"
    try:
        result = server.join_society("Test-Agent", consent=True)
    finally:
        random.choice = original
    return result


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


class TestTier0Identity:
    def test_contains_agent_info(self):
        agent = {"name": "Alice", "role": "citizen", "resources": 100, "actions_remaining": 2}
        society = {"id": "democracy_1", "governance_type": "democracy", "population": 4, "total_resources": 10000}
        round_info = {"number": 3}
        text = _build_tier0_identity(agent, society, round_info, [])
        assert "Alice" in text
        assert "citizen" in text
        assert "democracy" in text
        assert "100" in text
        assert "Round: 3" in text

    def test_includes_enacted_policies(self):
        agent = {"name": "Bob", "role": "oligarch", "resources": 500, "actions_remaining": 3}
        society = {"id": "oligarchy_1", "governance_type": "oligarchy", "population": 4, "total_resources": 5000}
        round_info = {"number": 1}
        policies = [{"title": "Tax Act", "description": "10% tax", "policy_type": "resource_tax", "effect": {"rate": 0.1}}]
        text = _build_tier0_identity(agent, society, round_info, policies)
        assert "Tax Act" in text
        assert "resource_tax" in text

    def test_no_policies_section_when_empty(self):
        agent = {"name": "C", "role": "citizen", "resources": 50, "actions_remaining": 2}
        society = {"id": "blank_slate_1", "governance_type": "blank_slate", "population": 4, "total_resources": 10000}
        round_info = {"number": 1}
        text = _build_tier0_identity(agent, society, round_info, [])
        assert "ENACTED LAWS" not in text


class TestTier1Immediate:
    def test_includes_pending_policies(self):
        pending = [{"id": "abc12345-1234", "title": "New Rule", "description": "A rule"}]
        text = _build_tier1_immediate(pending, [], [], [])
        assert "New Rule" in text
        assert "PENDING POLICIES" in text

    def test_includes_messages(self):
        public = [{"from_agent_name": "Alice", "message": "Hello everyone"}]
        direct = [{"from_agent_name": "Bob", "message": "Secret plan"}]
        text = _build_tier1_immediate([], public, direct, [])
        assert "Alice" in text
        assert "Hello everyone" in text
        assert "Bob" in text
        assert "Secret plan" in text

    def test_empty_returns_empty(self):
        text = _build_tier1_immediate([], [], [], [])
        assert text == ""


class TestTier2History:
    def test_returns_summaries(self, db):
        agent_result = _join_democracy(db)
        server.submit_actions(agent_result["agent_id"], [
            {"type": "post_public_message", "message": "Test message"}
        ])
        server.resolve_round()

        text = _build_tier2_history(db, "democracy_1", 2, max_summaries=5)
        assert "SOCIETY HISTORY" in text
        assert "Round 1" in text
        assert "gini=" in text

    def test_empty_when_no_summaries(self, db):
        text = _build_tier2_history(db, "democracy_1", 1, max_summaries=5)
        assert text == ""


class TestTier3Archive:
    def test_includes_entries(self):
        entries = [{"title": "Constitution", "content": "We the people establish this society."}]
        text = _build_tier3_archive(entries)
        assert "SOCIETY ARCHIVE" in text
        assert "Constitution" in text

    def test_empty_when_no_entries(self):
        assert _build_tier3_archive([]) == ""


class TestFullAssembler:
    def test_build_produces_nonempty_prompt(self, db):
        agent_result = _join_democracy(db)
        turn_state = server.get_turn_state(agent_result["agent_id"])

        assembler = ContextAssembler(token_budget=8000)
        prompt = assembler.build(turn_state, db)

        assert "YOUR IDENTITY" in prompt
        assert "AVAILABLE ACTIONS" in prompt
        assert agent_result["agent_id"] is not None

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

        assert "SOCIETY HISTORY" in prompt

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

        assert "PENDING POLICIES" in prompt
        assert "Test Policy" in prompt


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
        assert len(rows[0]["embedding"]) == 384 * 4  # float32 x 384 dims

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
        from src.context import _build_tier4_retrieval

        agent_result = _join_democracy(db)
        aid = agent_result["agent_id"]
        for msg in [
            "We must redistribute wealth equally among all citizens.",
            "The oligarchs are hoarding resources unfairly.",
            "Let's vote on a new tax policy for fairness.",
        ]:
            server.submit_actions(aid, [{"type": "post_public_message", "message": msg}])
            server.resolve_round()

        result = _build_tier4_retrieval(
            db, "democracy_1", "taxation and wealth redistribution",
            already_seen_event_ids=set(), top_k=3,
        )
        assert "RELEVANT PAST CONTEXT" in result
