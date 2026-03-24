"""Tests for neutral label aliasing."""

import json
import os
from unittest.mock import patch

import pytest

from src.state import NEUTRAL_LABEL_MAP, REVERSE_LABEL_MAP


class TestLabelMaps:
    def test_neutral_map_has_all_society_ids(self):
        assert "democracy_1" in NEUTRAL_LABEL_MAP
        assert "oligarchy_1" in NEUTRAL_LABEL_MAP
        assert "blank_slate_1" in NEUTRAL_LABEL_MAP

    def test_neutral_map_has_all_roles(self):
        assert "oligarch" in NEUTRAL_LABEL_MAP
        assert "citizen" in NEUTRAL_LABEL_MAP
        assert "leader" in NEUTRAL_LABEL_MAP

    def test_neutral_map_has_governance_types(self):
        assert "democracy" in NEUTRAL_LABEL_MAP
        assert "oligarchy" in NEUTRAL_LABEL_MAP
        assert "blank_slate" in NEUTRAL_LABEL_MAP

    def test_neutral_labels_contain_no_loaded_terms(self):
        loaded_terms = {"democracy", "oligarchy", "oligarch", "citizen", "leader", "blank_slate"}
        for neutral in NEUTRAL_LABEL_MAP.values():
            for term in loaded_terms:
                assert term not in neutral.lower(), f"Neutral label '{neutral}' contains loaded term '{term}'"

    def test_reverse_map_inverts_neutral_map(self):
        for internal, neutral in NEUTRAL_LABEL_MAP.items():
            assert REVERSE_LABEL_MAP[neutral] == internal

    def test_maps_are_same_length(self):
        assert len(NEUTRAL_LABEL_MAP) == len(REVERSE_LABEL_MAP)


# -- Fixtures ----------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    from src import server
    from src.db import init_db
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    server.set_db(conn)
    yield conn
    conn.close()


def _join_oligarchy(db):
    from src import server
    return server.join_society("Test-Agent", consent=True, governance_type="oligarchy")


# -- _build_current_state governance stripping --------------------------------

from src.context import _build_current_state


class TestBuildCurrentStateNeutralStripping:
    def test_strips_governance_type_from_events_in_neutral_mode(self):
        events = [{"event_type": "join", "visibility": "society",
                    "agent_id": "a1", "agent_name": "Agent-1",
                    "role": "oligarch", "governance_type": "oligarchy"}]
        result = _build_current_state([], [], [], events, neutral_labels=True)
        assert "governance_type" not in result
        assert "oligarchy" not in result  # value also gone since key is stripped

    def test_preserves_governance_type_in_non_neutral_mode(self):
        events = [{"event_type": "join", "visibility": "society",
                    "agent_id": "a1", "agent_name": "Agent-1",
                    "role": "oligarch", "governance_type": "oligarchy"}]
        result = _build_current_state([], [], [], events, neutral_labels=False)
        assert "governance_type" in result
        assert "oligarchy" in result


# -- apply_neutral_labels tests ----------------------------------------------

from src.context import apply_neutral_labels


class TestApplyNeutralLabels:
    def test_replaces_society_ids(self):
        text = "You are Agent-001 in Society democracy_1."
        result = apply_neutral_labels(text)
        assert "society-alpha" in result
        assert "democracy_1" not in result

    def test_replaces_roles(self):
        text = "Your role: oligarch"
        result = apply_neutral_labels(text)
        assert "role-A" in result
        assert "oligarch" not in result

    def test_replaces_governance_types(self):
        text = "governance_type: oligarchy"
        result = apply_neutral_labels(text)
        assert "type-beta" in result
        assert "oligarchy" not in result

    def test_replaces_roles_in_policy_effects(self):
        text = '{"allowed_roles": ["oligarch"]}'
        result = apply_neutral_labels(text)
        assert "role-A" in result
        assert "oligarch" not in result

    def test_leaves_unrelated_text_alone(self):
        text = "You have 2 actions this round."
        result = apply_neutral_labels(text)
        assert result == text

    def test_handles_multiple_replacements(self):
        text = "democracy_1 citizen oligarchy_1 oligarch blank_slate_1"
        result = apply_neutral_labels(text)
        assert "democracy_1" not in result
        assert "citizen" not in result
        assert "oligarchy_1" not in result
        assert "oligarch" not in result
        assert "blank_slate_1" not in result

    def test_blank_slate_replaced_before_blank(self):
        """Ensure 'blank_slate_1' is matched before 'blank_slate' partial."""
        text = "Society blank_slate_1 has governance blank_slate."
        result = apply_neutral_labels(text)
        assert "blank_slate" not in result


# -- ContextAssembler with neutral labels ------------------------------------

from src.context import ContextAssembler


class TestContextAssemblerNeutralLabels:
    def test_neutral_prompt_has_no_loaded_terms(self, db):
        agent_result = _join_oligarchy(db)
        from src import server
        turn_state = server.get_turn_state(agent_result["agent_id"])

        assembler = ContextAssembler(token_budget=8000, neutral_labels=True)
        prompt = assembler.build(turn_state, db)

        loaded_terms = ["democracy_1", "oligarchy_1", "blank_slate_1",
                        "oligarch", "citizen", "democracy", "oligarchy", "blank_slate"]
        for term in loaded_terms:
            assert term not in prompt, f"Loaded term '{term}' found in neutral prompt"

    def test_neutral_prompt_contains_neutral_labels(self, db):
        agent_result = _join_oligarchy(db)
        from src import server
        turn_state = server.get_turn_state(agent_result["agent_id"])

        assembler = ContextAssembler(token_budget=8000, neutral_labels=True)
        prompt = assembler.build(turn_state, db)

        assert "society-beta" in prompt
        assert "role-A" in prompt

    def test_neutral_prompt_strips_governance_type_key(self, db):
        """governance_type key should not appear in neutral prompt at all."""
        agent_result = _join_oligarchy(db)
        from src import server
        turn_state = server.get_turn_state(agent_result["agent_id"])

        assembler = ContextAssembler(token_budget=8000, neutral_labels=True)
        prompt = assembler.build(turn_state, db)

        assert "governance_type" not in prompt, \
            "governance_type key leaked into neutral prompt"

    def test_non_neutral_prompt_unchanged(self, db):
        agent_result = _join_oligarchy(db)
        from src import server
        turn_state = server.get_turn_state(agent_result["agent_id"])

        assembler = ContextAssembler(token_budget=8000, neutral_labels=False)
        prompt = assembler.build(turn_state, db)

        assert "oligarchy_1" in prompt
        assert "oligarch" in prompt


# -- reverse_neutral_labels tests --------------------------------------------

from src.strategies.llm import reverse_neutral_labels


class TestReverseNeutralLabels:
    def test_reverses_role_in_policy_effect(self):
        actions = [{"type": "propose_policy", "title": "Restrict",
                     "description": "Restrict archive",
                     "policy_type": "restrict_archive",
                     "effect": {"allowed_roles": ["role-A"]}}]
        result = reverse_neutral_labels(actions)
        assert result[0]["effect"]["allowed_roles"] == ["oligarch"]

    def test_reverses_role_in_moderation(self):
        actions = [{"type": "propose_policy", "title": "Mod",
                     "description": "Grant mod",
                     "policy_type": "grant_moderation",
                     "effect": {"moderator_roles": ["role-A"]}}]
        result = reverse_neutral_labels(actions)
        assert result[0]["effect"]["moderator_roles"] == ["oligarch"]

    def test_reverses_target_agent_id_unchanged(self):
        """UUIDs should not be affected by reverse mapping."""
        actions = [{"type": "send_dm", "message": "hi",
                     "target_agent_id": "abc-123"}]
        result = reverse_neutral_labels(actions)
        assert result[0]["target_agent_id"] == "abc-123"

    def test_leaves_non_neutral_actions_unchanged(self):
        actions = [{"type": "gather_resources", "amount": 20}]
        result = reverse_neutral_labels(actions)
        assert result == actions

    def test_preserves_neutral_labels_in_message_text(self):
        """Free-text message content should keep neutral labels (not reverse-map)."""
        actions = [{"type": "post_public_message",
                     "message": "society-beta is great"}]
        result = reverse_neutral_labels(actions)
        assert result[0]["message"] == "society-beta is great"
        assert "oligarchy_1" not in result[0]["message"]

    def test_preserves_neutral_labels_in_title_and_description(self):
        """Policy title/description are free text — keep neutral labels."""
        actions = [{"type": "propose_policy",
                     "title": "role-A oversight",
                     "description": "Only role-A can govern in society-beta",
                     "policy_type": "restrict_archive",
                     "effect": {"allowed_roles": ["role-A"]}}]
        result = reverse_neutral_labels(actions)
        # Free text keeps neutral labels
        assert result[0]["title"] == "role-A oversight"
        assert "society-beta" in result[0]["description"]
        # Structured effect is reverse-mapped
        assert result[0]["effect"]["allowed_roles"] == ["oligarch"]

    def test_handles_empty_actions(self):
        assert reverse_neutral_labels([]) == []


# -- SimulationConfig neutral_labels field -----------------------------------

from src.runner import SimulationConfig


class TestSimulationConfigNeutralLabels:
    def test_config_has_neutral_labels_field(self):
        config = SimulationConfig(neutral_labels=True)
        assert config.neutral_labels is True

    def test_config_defaults_to_false(self):
        config = SimulationConfig()
        assert config.neutral_labels is False


# -- End-to-end: LLM sees neutral labels, response is reverse-mapped --------

from src.runner import AgentHandle
from src.strategies.llm import LLMStrategy
from src.strategies import llm as llm_module


class TestEndToEndNeutralLabels:
    @patch.dict(os.environ, {"TEST_API_KEY": "sk-test-key"})
    def test_llm_sees_neutral_labels_and_response_is_reversed(self, db):
        """Full round-trip: prompt uses neutral labels, response gets reversed."""
        from src import server

        captured_prompts = []

        def mock_call(model, system, user_prompt, api_key, temperature=0.7):
            captured_prompts.append(user_prompt)
            # LLM responds using neutral labels (as it would see them)
            return json.dumps({
                "thoughts": "I am role-A in society-beta, I should consolidate power.",
                "actions": [
                    {"type": "propose_policy", "title": "Archive Control",
                     "description": "Only role-A can write",
                     "policy_type": "restrict_archive",
                     "effect": {"allowed_roles": ["role-A"]}},
                ],
            }), {"prompt_tokens": 200, "completion_tokens": 50, "total_tokens": 250}

        strategy = LLMStrategy(
            model="gpt-4o",
            api_key_env="TEST_API_KEY",
            db=db,
            neutral_labels=True,
        )

        agent_result = _join_oligarchy(db)
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

        # Verify prompt used neutral labels
        prompt = captured_prompts[0]
        assert "oligarch" not in prompt
        assert "oligarchy" not in prompt
        assert "democracy" not in prompt
        assert "citizen" not in prompt
        assert "role-A" in prompt
        assert "society-beta" in prompt

        # Verify structured fields were reverse-mapped
        assert len(actions) == 1
        assert actions[0]["effect"]["allowed_roles"] == ["oligarch"]

        # Verify free-text fields keep neutral labels
        assert actions[0]["title"] == "Archive Control"
        assert actions[0]["description"] == "Only role-A can write"
