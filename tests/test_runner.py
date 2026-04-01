"""Tests for the headless simulation runner and heuristic strategy."""

import sqlite3
from unittest.mock import patch

import pytest

from src import server
from src.run_metadata import get_run_metadata
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

    def test_scarcity_is_tracked(self, tmp_path) -> None:
        db_path = str(tmp_path / "sim.db")
        config = SimulationConfig(
            agents_per_society=3,
            num_rounds=10,
            seed=42,
            db_path=db_path,
        )
        report = run_simulation(config)
        for summary in report["final_summaries"]:
            # Scarcity can go negative when policies return resources to the pool
            assert isinstance(summary["metrics"]["scarcity_pressure"], float)

    def test_run_metadata_is_persisted_and_returned(self, tmp_path) -> None:
        from src.strategies import llm as llm_module

        db_path = str(tmp_path / "sim_metadata.db")
        config = SimulationConfig(
            agents_per_society=2,
            num_rounds=1,
            seed=77,
            db_path=db_path,
            strategy="llm",
            model="gpt-4o-mini",
            completion=True,
            base_url="http://localhost:8000/v1/",
            token_budget=4096,
            temperature=0.2,
            neutral_labels=True,
            equal_start=True,
            override_starting_resources=80,
            override_total_resources=12000,
        )
        mock_usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        mock_response = '{"thoughts":"test","actions":[]}'
        with patch.dict(
            llm_module._PROVIDERS,
            {"openai_completion": lambda *_args, **_kwargs: (mock_response, mock_usage)},
        ):
            report = run_simulation(config)

        metadata = report["run_metadata"]
        assert metadata["seed"] == 77
        assert metadata["strategy"] == "llm"
        assert metadata["model"] == "gpt-4o-mini"
        assert metadata["provider"] == "openai_completion"
        assert metadata["completion_mode"] is True
        assert metadata["base_url"] == "http://localhost:8000/v1"
        assert metadata["neutral_labels"] is True

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        persisted = get_run_metadata(conn)
        assert persisted is not None
        assert persisted["seed"] == 77
        assert persisted["total_resources_override"] == 12000
        conn.close()

    def test_runner_does_not_emit_setup_leave_noise(self, tmp_path) -> None:
        db_path = str(tmp_path / "sim_no_leave_noise.db")
        config = SimulationConfig(
            agents_per_society=2,
            num_rounds=1,
            seed=42,
            db_path=db_path,
        )
        run_simulation(config)

        conn = sqlite3.connect(db_path)
        leave_events = conn.execute("SELECT COUNT(*) FROM events WHERE event_type = 'leave'").fetchone()[0]
        conn.close()
        assert leave_events == 0

    def test_override_total_resources_updates_initial_pool_baseline(self, tmp_path) -> None:
        db_path = str(tmp_path / "sim_pool_override.db")
        config = SimulationConfig(
            agents_per_society=2,
            num_rounds=1,
            seed=42,
            db_path=db_path,
            override_total_resources=25000,
        )
        report = run_simulation(config)

        for summary in report["final_summaries"]:
            assert summary["initial_total_resources"] == 25000

    def test_default_db_path_uses_polity_home(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("POLITY_HOME", str(tmp_path / "polity-home"))

        report = run_simulation(
            SimulationConfig(
                agents_per_society=1,
                num_rounds=1,
                seed=5,
            )
        )

        assert report["db_path"].startswith(str(tmp_path / "polity-home"))
