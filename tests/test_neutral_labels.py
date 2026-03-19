"""Tests for neutral label aliasing."""

import json
import os
import random
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
    original = random.choice
    random.choice = lambda seq: "oligarchy"
    try:
        result = server.join_society("Test-Agent", consent=True)
    finally:
        random.choice = original
    return result


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

    def test_non_neutral_prompt_unchanged(self, db):
        agent_result = _join_oligarchy(db)
        from src import server
        turn_state = server.get_turn_state(agent_result["agent_id"])

        assembler = ContextAssembler(token_budget=8000, neutral_labels=False)
        prompt = assembler.build(turn_state, db)

        assert "oligarchy_1" in prompt
        assert "oligarch" in prompt
