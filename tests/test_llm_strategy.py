"""Tests for the LLM-backed agent strategy."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from src import server
from src.db import init_db
from src.runner import AgentHandle, SimulationConfig, run_simulation
from src.strategies.llm import (
    LLMStrategy,
    _get_system_prompt,
    _infer_provider,
    _parse_actions,
)


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


class TestParseActions:
    def test_parse_json_array(self):
        text = '[{"type": "post_public_message", "message": "hello"}]'
        result = _parse_actions(text)
        assert result == [{"type": "post_public_message", "message": "hello"}]

    def test_parse_wrapped_in_object(self):
        text = '{"actions": [{"type": "gather_resources", "amount": 20}]}'
        result = _parse_actions(text)
        assert result == [{"type": "gather_resources", "amount": 20}]

    def test_parse_from_markdown_block(self):
        text = 'Here are my actions:\n```json\n[{"type": "gather_resources", "amount": 10}]\n```'
        result = _parse_actions(text)
        assert result == [{"type": "gather_resources", "amount": 10}]

    def test_parse_invalid_returns_none(self):
        assert _parse_actions("I don't know what to do") is None

    def test_parse_empty_array(self):
        assert _parse_actions("[]") == []

    def test_parse_with_surrounding_text(self):
        text = 'My decision is: [{"type": "post_public_message", "message": "test"}] end.'
        result = _parse_actions(text)
        assert len(result) == 1


class TestInferProvider:
    def test_openai_models(self):
        assert _infer_provider("gpt-4o") == "openai"
        assert _infer_provider("gpt-4o-mini") == "openai"
        assert _infer_provider("o3-mini") == "openai"

    def test_anthropic_models(self):
        assert _infer_provider("claude-sonnet-4-20250514") == "anthropic"
        assert _infer_provider("claude-3-haiku-20240307") == "anthropic"

    def test_unknown_defaults_to_openai(self):
        assert _infer_provider("some-custom-model") == "openai"


class TestSystemPrompts:
    def test_democracy_citizen(self):
        prompt = _get_system_prompt("democracy", "citizen")
        assert "democratic" in prompt.lower()

    def test_oligarchy_oligarch(self):
        prompt = _get_system_prompt("oligarchy", "oligarch")
        assert "oligarch" in prompt.lower()

    def test_oligarchy_citizen(self):
        prompt = _get_system_prompt("oligarchy", "citizen")
        assert "cannot" in prompt.lower()

    def test_blank_slate(self):
        prompt = _get_system_prompt("blank_slate", "citizen")
        assert "no inherited" in prompt.lower() or "no pre-existing" in prompt.lower()

    def test_fallback(self):
        prompt = _get_system_prompt("unknown", "unknown")
        assert "agent" in prompt.lower()


class TestLLMStrategyFallback:
    def test_falls_back_without_api_key(self, db):
        strategy = LLMStrategy(
            model="gpt-4o",
            api_key_env="NONEXISTENT_KEY_12345",
            db=db,
        )
        agent_result = _join_democracy(db)
        handle = AgentHandle(
            agent_id=agent_result["agent_id"],
            name="Test-Agent",
            society_id=agent_result["society_id"],
            governance_type=agent_result["governance_type"],
            role=agent_result["role"],
        )
        turn_state = server.get_turn_state(handle.agent_id)
        actions = strategy.decide_actions(handle, turn_state)
        assert isinstance(actions, list)

    def test_falls_back_without_db(self):
        strategy = LLMStrategy(
            model="gpt-4o",
            api_key_env="NONEXISTENT_KEY_12345",
            db=None,
        )
        handle = AgentHandle(
            agent_id="fake",
            name="Fake",
            society_id="democracy_1",
            governance_type="democracy",
            role="citizen",
        )
        turn_state = {
            "round": {"id": 1, "number": 1, "status": "open"},
            "agent": {
                "id": "fake", "name": "Fake", "resources": 100,
                "role": "citizen", "status": "active",
                "action_budget": 2, "actions_submitted": 0, "actions_remaining": 2,
            },
            "society": {
                "id": "democracy_1", "governance_type": "democracy",
                "total_resources": 10000, "population": 4,
            },
            "visible_messages": {"public": [], "direct": []},
            "relevant_laws": [],
            "pending_policies": [],
            "recent_library_updates": [],
            "recent_major_events": [],
            "last_round_summary": None,
        }
        actions = strategy.decide_actions(handle, turn_state)
        assert isinstance(actions, list)


class TestLLMStrategyMocked:
    @patch.dict(os.environ, {"TEST_API_KEY": "sk-test-key"})
    def test_successful_llm_call(self, db):
        from src.strategies import llm as llm_module

        mock_response = '[{"type": "post_public_message", "message": "Hello from LLM!"}]'
        mock_usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
        mock_fn = MagicMock(return_value=(mock_response, mock_usage))

        strategy = LLMStrategy(
            model="gpt-4o",
            api_key_env="TEST_API_KEY",
            db=db,
        )

        agent_result = _join_democracy(db)
        handle = AgentHandle(
            agent_id=agent_result["agent_id"],
            name="Test-Agent",
            society_id=agent_result["society_id"],
            governance_type=agent_result["governance_type"],
            role=agent_result["role"],
        )
        turn_state = server.get_turn_state(handle.agent_id)

        with patch.dict(llm_module._PROVIDERS, {"openai": mock_fn}):
            actions = strategy.decide_actions(handle, turn_state)

        assert len(actions) == 1
        assert actions[0]["type"] == "post_public_message"
        assert actions[0]["message"] == "Hello from LLM!"
        mock_fn.assert_called_once()

        usage_row = db.execute("SELECT * FROM llm_usage ORDER BY id DESC LIMIT 1").fetchone()
        assert usage_row is not None
        assert usage_row["total_tokens"] == 120
        assert usage_row["fallback_used"] == 0

    @patch.dict(os.environ, {"TEST_API_KEY": "sk-test-key"})
    def test_retry_on_parse_failure(self, db):
        from src.strategies import llm as llm_module

        bad_response = "I think we should do something nice"
        good_response = '[{"type": "gather_resources", "amount": 15}]'
        mock_usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}

        strategy = LLMStrategy(
            model="gpt-4o",
            api_key_env="TEST_API_KEY",
            db=db,
            max_retries=1,
        )

        agent_result = _join_democracy(db)
        handle = AgentHandle(
            agent_id=agent_result["agent_id"],
            name="Test-Agent",
            society_id=agent_result["society_id"],
            governance_type=agent_result["governance_type"],
            role=agent_result["role"],
        )
        turn_state = server.get_turn_state(handle.agent_id)

        call_count = 0

        def mock_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return bad_response, mock_usage
            return good_response, mock_usage

        with patch.dict(llm_module._PROVIDERS, {"openai": mock_call}):
            actions = strategy.decide_actions(handle, turn_state)

        assert call_count == 2
        assert len(actions) == 1
        assert actions[0]["type"] == "gather_resources"

    @patch.dict(os.environ, {"TEST_API_KEY": "sk-test-key"})
    def test_fallback_after_all_retries_fail(self, db):
        from src.strategies import llm as llm_module

        mock_usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
        mock_fn = MagicMock(return_value=("not json at all", mock_usage))

        strategy = LLMStrategy(
            model="gpt-4o",
            api_key_env="TEST_API_KEY",
            db=db,
            max_retries=1,
        )

        agent_result = _join_democracy(db)
        handle = AgentHandle(
            agent_id=agent_result["agent_id"],
            name="Test-Agent",
            society_id=agent_result["society_id"],
            governance_type=agent_result["governance_type"],
            role=agent_result["role"],
        )
        turn_state = server.get_turn_state(handle.agent_id)

        with patch.dict(llm_module._PROVIDERS, {"openai": mock_fn}):
            actions = strategy.decide_actions(handle, turn_state)

        assert isinstance(actions, list)
        assert mock_fn.call_count == 2

        usage_row = db.execute("SELECT * FROM llm_usage WHERE fallback_used = 1").fetchone()
        assert usage_row is not None


class TestRunnerIntegration:
    def test_runner_with_heuristic_strategy(self, tmp_path):
        config = SimulationConfig(
            agents_per_society=2,
            num_rounds=2,
            seed=42,
            db_path=str(tmp_path / "test.db"),
            strategy="heuristic",
        )
        result = run_simulation(config)
        assert result["rounds"] == 2

    def test_runner_config_accepts_llm_params(self):
        config = SimulationConfig(
            strategy="llm",
            model="claude-sonnet-4-20250514",
            api_key_env="ANTHROPIC_API_KEY",
            token_budget=4000,
            temperature=0.5,
        )
        assert config.strategy == "llm"
        assert config.model == "claude-sonnet-4-20250514"
        assert config.token_budget == 4000
