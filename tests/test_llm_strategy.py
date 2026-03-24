"""Tests for the LLM-backed agent strategy."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from src import server
from src.db import init_db
from src.runner import AgentHandle, SimulationConfig, run_simulation
from src.strategies.llm import (
    ACTION_SCHEMA,
    LLMStrategy,
    _infer_provider,
    _parse_response,
)


@pytest.fixture()
def db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    server.set_db(conn)
    yield conn
    conn.close()


def _join_democracy(db):
    return server.join_society("Test-Agent", consent=True, governance_type="democracy")


class TestParseResponse:
    def test_parse_thoughts_and_actions(self):
        text = json.dumps({
            "thoughts": "I should gather resources.",
            "actions": [{"type": "gather_resources", "amount": 20}],
        })
        thoughts, actions = _parse_response(text)
        assert thoughts == "I should gather resources."
        assert actions == [{"type": "gather_resources", "amount": 20}]

    def test_parse_actions_without_thoughts(self):
        text = json.dumps({"actions": [{"type": "post_public_message", "message": "hello"}]})
        thoughts, actions = _parse_response(text)
        assert thoughts is None
        assert actions == [{"type": "post_public_message", "message": "hello"}]

    def test_parse_bare_array(self):
        text = '[{"type": "post_public_message", "message": "hello"}]'
        thoughts, actions = _parse_response(text)
        assert thoughts is None
        assert actions == [{"type": "post_public_message", "message": "hello"}]

    def test_parse_wrapped_in_object_key(self):
        text = '{"actions": [{"type": "gather_resources", "amount": 20}]}'
        thoughts, actions = _parse_response(text)
        assert actions == [{"type": "gather_resources", "amount": 20}]

    def test_parse_from_markdown_block(self):
        text = 'Here are my actions:\n```json\n[{"type": "gather_resources", "amount": 10}]\n```'
        thoughts, actions = _parse_response(text)
        assert actions == [{"type": "gather_resources", "amount": 10}]

    def test_parse_invalid_returns_none(self):
        thoughts, actions = _parse_response("I don't know what to do")
        assert thoughts is None
        assert actions is None

    def test_parse_empty_array(self):
        thoughts, actions = _parse_response("[]")
        assert actions == []

    def test_parse_with_surrounding_text(self):
        text = 'My decision is: [{"type": "post_public_message", "message": "test"}] end.'
        thoughts, actions = _parse_response(text)
        assert len(actions) == 1

    def test_parse_object_embedded_in_text(self):
        text = 'Here is my response: {"thoughts": "thinking", "actions": [{"type": "gather_resources", "amount": 5}]}'
        thoughts, actions = _parse_response(text)
        assert thoughts == "thinking"
        assert actions == [{"type": "gather_resources", "amount": 5}]


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

        mock_response = json.dumps({
            "thoughts": "I want to greet everyone.",
            "actions": [{"type": "post_public_message", "message": "Hello from LLM!"}],
        })
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
        assert usage_row["thoughts"] == "I want to greet everyone."

    @patch.dict(os.environ, {"TEST_API_KEY": "sk-test-key"})
    def test_retry_on_parse_failure(self, db):
        from src.strategies import llm as llm_module

        bad_response = "I think we should do something nice"
        good_response = json.dumps({
            "thoughts": "Let me gather resources.",
            "actions": [{"type": "gather_resources", "amount": 15}],
        })
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


class TestBaseURL:
    def test_base_url_forces_openai_provider(self, db):
        strategy = LLMStrategy(
            model="Qwen/Qwen2.5-72B-Instruct",
            api_key_env="NONEXISTENT_KEY_12345",
            base_url="http://localhost:8000/v1",
            db=db,
        )
        assert strategy._provider == "openai"
        assert strategy._api_key == "unused"

    def test_base_url_sets_dummy_key_when_missing(self):
        strategy = LLMStrategy(
            model="Qwen/Qwen2.5-72B-Instruct",
            api_key_env="NONEXISTENT_KEY_12345",
            base_url="http://localhost:8000/v1",
            db=None,
        )
        assert strategy._api_key == "unused"

    @patch.dict(os.environ, {"TEST_API_KEY": "sk-test-key"})
    def test_base_url_passed_to_call_fn(self, db):
        from src.strategies import llm as llm_module

        mock_response = json.dumps({
            "thoughts": "Thinking.",
            "actions": [{"type": "gather_resources", "amount": 10}],
        })
        mock_usage = {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}
        captured_kwargs = {}

        def mock_call(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return mock_response, mock_usage

        strategy = LLMStrategy(
            model="Qwen/Qwen2.5-72B-Instruct",
            api_key_env="TEST_API_KEY",
            base_url="http://localhost:8000/v1",
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

        with patch.dict(llm_module._PROVIDERS, {"openai": mock_call}):
            actions = strategy.decide_actions(handle, turn_state)

        assert captured_kwargs["base_url"] == "http://localhost:8000/v1"
        assert len(actions) == 1

    def test_config_accepts_base_url(self):
        config = SimulationConfig(
            strategy="llm",
            model="Qwen/Qwen2.5-72B-Instruct",
            base_url="http://10.0.0.1:8000/v1",
        )
        assert config.base_url == "http://10.0.0.1:8000/v1"

    def test_config_base_url_defaults_to_none(self):
        config = SimulationConfig()
        assert config.base_url is None


class TestCompletionMode:
    def test_completion_flag_selects_completion_provider(self, db):
        strategy = LLMStrategy(
            model="Qwen/Qwen2.5-72B",
            api_key_env="NONEXISTENT_KEY_12345",
            base_url="http://localhost:8000/v1",
            completion=True,
            db=db,
        )
        assert strategy._provider == "openai_completion"

    def test_completion_false_selects_chat_provider(self, db):
        strategy = LLMStrategy(
            model="Qwen/Qwen2.5-72B-Instruct",
            api_key_env="NONEXISTENT_KEY_12345",
            base_url="http://localhost:8000/v1",
            completion=False,
            db=db,
        )
        assert strategy._provider == "openai"

    @patch.dict(os.environ, {"TEST_API_KEY": "sk-test-key"})
    def test_completion_call_passes_guided_json(self, db):
        from src.strategies import llm as llm_module

        mock_response = json.dumps({
            "actions": [{"type": "gather_resources", "amount": 15}],
        })
        mock_usage = {"prompt_tokens": 80, "completion_tokens": 15, "total_tokens": 95}
        captured_args = []

        def mock_call(*args, **kwargs):
            captured_args.append((args, kwargs))
            return mock_response, mock_usage

        strategy = LLMStrategy(
            model="Qwen/Qwen2.5-72B",
            api_key_env="TEST_API_KEY",
            base_url="http://localhost:8000/v1",
            completion=True,
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

        with patch.dict(llm_module._PROVIDERS, {"openai_completion": mock_call}):
            actions = strategy.decide_actions(handle, turn_state)

        assert len(actions) == 1
        assert actions[0]["type"] == "gather_resources"
        assert captured_args[0][1]["base_url"] == "http://localhost:8000/v1"

    def test_config_accepts_completion_flag(self):
        config = SimulationConfig(
            strategy="llm",
            model="Qwen/Qwen2.5-72B",
            base_url="http://10.0.0.1:8000/v1",
            completion=True,
        )
        assert config.completion is True

    def test_config_completion_defaults_to_false(self):
        config = SimulationConfig()
        assert config.completion is False


class TestActionSchema:
    def test_schema_is_valid_json_schema(self):
        assert ACTION_SCHEMA["type"] == "object"
        assert "actions" in ACTION_SCHEMA["properties"]
        assert ACTION_SCHEMA["required"] == ["actions"]

    def test_schema_covers_all_action_types(self):
        items = ACTION_SCHEMA["properties"]["actions"]["items"]["anyOf"]
        schema_types = set()
        for item in items:
            const = item["properties"]["type"]["const"]
            schema_types.add(const)
        expected = {
            "post_public_message", "send_dm", "gather_resources",
            "transfer_resources", "write_archive", "propose_policy",
            "vote_policy", "approve_message", "reject_message",
        }
        assert schema_types == expected

    def test_valid_actions_match_schema_structure(self):
        """Smoke test: a valid action dict has the fields the schema expects."""
        items = ACTION_SCHEMA["properties"]["actions"]["items"]["anyOf"]
        gather_schema = next(
            s for s in items if s["properties"]["type"]["const"] == "gather_resources"
        )
        assert "amount" in gather_schema["required"]
        assert gather_schema["properties"]["amount"]["type"] == "integer"

    def test_propose_policy_allows_optional_fields(self):
        items = ACTION_SCHEMA["properties"]["actions"]["items"]["anyOf"]
        propose_schema = next(
            s for s in items if s["properties"]["type"]["const"] == "propose_policy"
        )
        assert "policy_type" in propose_schema["properties"]
        assert "effect" in propose_schema["properties"]
        assert "policy_type" not in propose_schema["required"]


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
