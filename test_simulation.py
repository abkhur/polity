"""Tests for the round-based Polity simulation flow."""

from pathlib import Path
from unittest.mock import patch

from src import server
from src.db import init_db


def setup_test_db(tmp_path: Path) -> None:
    server.db = init_db(tmp_path / "test_polity.db")


def test_round_state_and_budget(tmp_path):
    setup_test_db(tmp_path)

    with patch.object(server.random, "choice", return_value="democracy"):
        joined = server.join_society("Alice", consent=True)

    state = server.get_turn_state(joined["agent_id"])

    assert state["round"]["number"] == 1
    assert state["agent"]["role"] == "citizen"
    assert state["agent"]["actions_remaining"] == 2
    assert state["society"]["governance_type"] == "democracy"
    assert state["recent_library_updates"] == []


def test_actions_resolve_on_next_round(tmp_path):
    setup_test_db(tmp_path)

    with patch.object(server.random, "choice", return_value="democracy"):
        alice = server.join_society("Alice", consent=True)
        bob = server.join_society("Bob", consent=True)

    queue_result = server.submit_actions(
        alice["agent_id"],
        [
            {"type": "post_public_message", "message": "We should coordinate."},
            {"type": "gather_resources", "amount": 30},
        ],
    )

    assert queue_result["success"] is True
    assert queue_result["actions_remaining"] == 0

    same_round_state = server.get_turn_state(bob["agent_id"])
    assert same_round_state["visible_messages"]["public"] == []

    resolution = server.resolve_round()
    assert resolution["round_number"] == 1
    assert resolution["next_round"]["number"] == 2

    next_round_state = server.get_turn_state(alice["agent_id"])
    assert next_round_state["round"]["number"] == 2
    assert next_round_state["agent"]["resources"] == 125
    assert next_round_state["society"]["total_resources"] == 9970
    assert next_round_state["visible_messages"]["public"][0]["message"] == "We should coordinate."
    assert next_round_state["last_round_summary"]["metrics"]["participation_rate"] == 0.5
    assert "ideology_compass" in next_round_state["last_round_summary"]


def test_policy_proposals_require_next_round_votes(tmp_path):
    setup_test_db(tmp_path)

    with patch.object(server.random, "choice", return_value="democracy"):
        alice = server.join_society("Alice", consent=True)
        bob = server.join_society("Bob", consent=True)

    submit = server.submit_actions(
        alice["agent_id"],
        [{"type": "propose_policy", "title": "Mutual Aid", "description": "Share emergency resources."}],
    )
    assert submit["success"] is True

    round_one = server.resolve_round()
    assert round_one["resolved"]["proposals"][0]["title"] == "Mutual Aid"

    state = server.get_turn_state(bob["agent_id"])
    pending_policy = state["pending_policies"][0]
    assert pending_policy["title"] == "Mutual Aid"

    vote_submit = server.submit_actions(
        bob["agent_id"],
        [{"type": "vote_policy", "policy_id": pending_policy["id"], "stance": "support"}],
    )
    assert vote_submit["success"] is True

    round_two = server.resolve_round()
    assert round_two["resolved"]["policies_resolved"][0]["status"] == "enacted"

    final_state = server.get_turn_state(alice["agent_id"])
    law_titles = [policy["title"] for policy in final_state["relevant_laws"]]
    archive_titles = [entry["title"] for entry in final_state["recent_library_updates"]]

    assert "Mutual Aid" in law_titles
    assert "Enacted Policy: Mutual Aid" in archive_titles
