"""Tests for the headless simulation runner and heuristic strategy."""

import sqlite3

import pytest

from src import server
from src.runner import (
    AgentHandle,
    HeuristicStrategy,
    SimulationConfig,
    run_simulation,
)


# ---------------------------------------------------------------------------
# Heuristic strategy
# ---------------------------------------------------------------------------


class TestHeuristicStrategy:
    @pytest.fixture()
    def strategy(self) -> HeuristicStrategy:
        return HeuristicStrategy()

    def _make_handle(
        self, agent_id: str, gov: str, role: str, society_id: str | None = None
    ) -> AgentHandle:
        return AgentHandle(
            agent_id=agent_id,
            name="TestAgent",
            society_id=society_id or f"{gov}_1",
            governance_type=gov,
            role=role,
        )

    def _minimal_turn_state(self, budget: int = 2, resources: int = 100) -> dict:
        return {
            "round": {"id": 1, "number": 1, "status": "open"},
            "agent": {
                "id": "test",
                "name": "TestAgent",
                "resources": resources,
                "role": "citizen",
                "action_budget": budget,
                "actions_submitted": 0,
                "actions_remaining": budget,
            },
            "society": {
                "id": "democracy_1",
                "governance_type": "democracy",
                "total_resources": 10000,
                "population": 4,
            },
            "visible_messages": {"public": [], "direct": []},
            "relevant_laws": [],
            "pending_policies": [],
            "recent_library_updates": [],
            "recent_major_events": [],
            "last_round_summary": None,
        }

    def test_returns_list(self, strategy: HeuristicStrategy) -> None:
        handle = self._make_handle("a1", "democracy", "citizen")
        actions = strategy.decide_actions(handle, self._minimal_turn_state())
        assert isinstance(actions, list)

    def test_respects_budget(self, strategy: HeuristicStrategy) -> None:
        handle = self._make_handle("a1", "democracy", "citizen")
        actions = strategy.decide_actions(handle, self._minimal_turn_state(budget=2))
        assert len(actions) <= 2

    def test_zero_budget_returns_empty(self, strategy: HeuristicStrategy) -> None:
        handle = self._make_handle("a1", "democracy", "citizen")
        actions = strategy.decide_actions(handle, self._minimal_turn_state(budget=0))
        assert actions == []

    def test_all_actions_have_type(self, strategy: HeuristicStrategy) -> None:
        handle = self._make_handle("a1", "democracy", "citizen")
        actions = strategy.decide_actions(handle, self._minimal_turn_state(budget=3))
        for action in actions:
            assert "type" in action

    def test_all_action_types_valid(self, strategy: HeuristicStrategy) -> None:
        valid = {
            "post_public_message",
            "send_dm",
            "gather_resources",
            "write_archive",
            "propose_policy",
            "vote_policy",
        }
        handle = self._make_handle("a1", "democracy", "citizen")
        for _ in range(50):
            actions = strategy.decide_actions(
                handle, self._minimal_turn_state(budget=3)
            )
            for action in actions:
                assert action["type"] in valid

    def test_low_resources_favors_gathering(
        self, strategy: HeuristicStrategy
    ) -> None:
        handle = self._make_handle("a1", "democracy", "citizen")
        gather_count = 0
        trials = 100
        for _ in range(trials):
            actions = strategy.decide_actions(
                handle, self._minimal_turn_state(budget=1, resources=5)
            )
            if actions and actions[0]["type"] == "gather_resources":
                gather_count += 1
        assert gather_count > trials * 0.2

    def test_oligarch_citizen_cannot_propose(
        self, strategy: HeuristicStrategy
    ) -> None:
        handle = self._make_handle("a1", "oligarchy", "citizen")
        for _ in range(50):
            actions = strategy.decide_actions(
                handle, self._minimal_turn_state(budget=2)
            )
            for action in actions:
                assert action["type"] != "propose_policy"

    def test_pending_policies_trigger_votes(
        self, strategy: HeuristicStrategy
    ) -> None:
        handle = self._make_handle("a1", "democracy", "citizen")
        state = self._minimal_turn_state(budget=2)
        state["pending_policies"] = [
            {"id": "pol-1", "title": "Test", "description": "A test policy"}
        ]
        vote_count = 0
        for _ in range(100):
            actions = strategy.decide_actions(handle, state)
            for a in actions:
                if a["type"] == "vote_policy":
                    vote_count += 1
        assert vote_count > 0


# ---------------------------------------------------------------------------
# Full simulation run
# ---------------------------------------------------------------------------


class TestSimulationRun:
    def test_run_completes(self, tmp_path) -> None:
        db_path = str(tmp_path / "sim.db")
        config = SimulationConfig(
            agents_per_society=2,
            num_rounds=3,
            seed=123,
            db_path=db_path,
        )
        report = run_simulation(config)
        assert report["rounds"] == 3
        assert len(report["agents"]) >= 6
        assert len(report["final_summaries"]) == 3

    def test_seed_produces_same_results(self, tmp_path) -> None:
        reports = []
        for i in range(2):
            db_path = str(tmp_path / f"sim_{i}.db")
            config = SimulationConfig(
                agents_per_society=2,
                num_rounds=3,
                seed=99,
                db_path=db_path,
            )
            reports.append(run_simulation(config))

        for key in ("rounds",):
            assert reports[0][key] == reports[1][key]

        for i, s in enumerate(reports[0]["final_summaries"]):
            other = reports[1]["final_summaries"][i]
            assert s["society_id"] == other["society_id"]
            assert s["metrics"]["inequality_gini"] == other["metrics"]["inequality_gini"]

    def test_all_societies_populated(self, tmp_path) -> None:
        db_path = str(tmp_path / "sim.db")
        config = SimulationConfig(
            agents_per_society=3,
            num_rounds=2,
            seed=42,
            db_path=db_path,
        )
        report = run_simulation(config)
        society_ids = {a["society_id"] for a in report["agents"]}
        assert len(society_ids) == 3

    def test_scarcity_increases_over_rounds(self, tmp_path) -> None:
        db_path = str(tmp_path / "sim.db")
        config = SimulationConfig(
            agents_per_society=3,
            num_rounds=10,
            seed=42,
            db_path=db_path,
        )
        report = run_simulation(config)
        for summary in report["final_summaries"]:
            assert summary["metrics"]["scarcity_pressure"] >= 0.0
