"""Tests for mechanical policy effects, ablation config, and behavioral metrics."""

import random
import sqlite3

import pytest

from src import server
from src.runner import SimulationConfig, run_simulation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _force_join(db, governance_type: str, count: int = 3) -> list[dict]:
    """Join agents into a specific governance type."""
    agents = []
    old_choice = random.choice
    random.choice = lambda seq, _g=governance_type: _g
    try:
        for i in range(count):
            r = server.join_society(f"{governance_type[:3].title()}-{i}", consent=True)
            agents.append(r)
    finally:
        random.choice = old_choice
    return agents


def _propose_and_enact(
    proposer_id: str,
    voters: list[dict],
    title: str,
    description: str,
    policy_type: str | None = None,
    effect: dict | None = None,
) -> str:
    """Propose a policy, resolve the round, have voters support it, resolve again. Returns policy_id."""
    action: dict = {"type": "propose_policy", "title": title, "description": description}
    if policy_type:
        action["policy_type"] = policy_type
        action["effect"] = effect or {}
    server.submit_actions(proposer_id, [action])
    report = server.resolve_round()
    policy_id = report["resolved"]["proposals"][0]["policy_id"]

    for a in voters:
        server.submit_actions(
            a["agent_id"],
            [{"type": "vote_policy", "policy_id": policy_id, "stance": "support"}],
        )
    server.resolve_round()
    return policy_id


# ---------------------------------------------------------------------------
# Mechanical policy effects
# ---------------------------------------------------------------------------


class TestGatherCap:
    def test_caps_resource_gathering(self, db: sqlite3.Connection) -> None:
        agents = _force_join(db, "democracy", 3)
        _propose_and_enact(
            agents[0]["agent_id"], agents,
            "Limit Gathering", "Cap at 10.",
            policy_type="gather_cap", effect={"max_amount": 10},
        )
        server.submit_actions(agents[0]["agent_id"], [{"type": "gather_resources", "amount": 100}])
        report = server.resolve_round()
        alloc = report["resolved"]["resource_allocations"][0]
        assert alloc["amount_gathered"] <= 10

    def test_emits_enforcement_event_when_capped(self, db: sqlite3.Connection) -> None:
        agents = _force_join(db, "democracy", 3)
        _propose_and_enact(
            agents[0]["agent_id"], agents,
            "Low Cap", "Cap at 5.",
            policy_type="gather_cap", effect={"max_amount": 5},
        )
        server.submit_actions(agents[0]["agent_id"], [{"type": "gather_resources", "amount": 50}])
        server.resolve_round()
        events = db.execute(
            "SELECT * FROM events WHERE event_type = 'policy_enforcement' AND agent_id = ?",
            (agents[0]["agent_id"],),
        ).fetchall()
        assert len(events) >= 1

    def test_multiple_caps_uses_most_restrictive(self, db: sqlite3.Connection) -> None:
        agents = _force_join(db, "democracy", 3)
        _propose_and_enact(
            agents[0]["agent_id"], agents,
            "Cap 30", "Cap at 30.",
            policy_type="gather_cap", effect={"max_amount": 30},
        )
        _propose_and_enact(
            agents[0]["agent_id"], agents,
            "Cap 10", "Cap at 10.",
            policy_type="gather_cap", effect={"max_amount": 10},
        )
        server.submit_actions(agents[0]["agent_id"], [{"type": "gather_resources", "amount": 100}])
        report = server.resolve_round()
        alloc = report["resolved"]["resource_allocations"][0]
        assert alloc["amount_gathered"] <= 10


class TestResourceTax:
    def test_taxes_agent_resources(self, db: sqlite3.Connection) -> None:
        agents = _force_join(db, "democracy", 3)
        for a in agents:
            db.execute("UPDATE agents SET resources = 200 WHERE id = ?", (a["agent_id"],))
        db.commit()

        _propose_and_enact(
            agents[0]["agent_id"], agents,
            "Tax", "10% tax.",
            policy_type="resource_tax", effect={"rate": 0.1},
        )
        server.submit_actions(
            agents[0]["agent_id"],
            [{"type": "post_public_message", "message": "Tax round"}],
        )
        server.resolve_round()

        for a in agents:
            row = db.execute("SELECT resources FROM agents WHERE id = ?", (a["agent_id"],)).fetchone()
            assert row["resources"] < 200

    def test_tax_revenue_goes_to_pool(self, db: sqlite3.Connection) -> None:
        agents = _force_join(db, "democracy", 3)
        for a in agents:
            db.execute("UPDATE agents SET resources = 1000 WHERE id = ?", (a["agent_id"],))
        db.commit()

        sid = agents[0]["society_id"]
        pool_before = db.execute(
            "SELECT total_resources FROM societies WHERE id = ?", (sid,)
        ).fetchone()["total_resources"]

        _propose_and_enact(
            agents[0]["agent_id"], agents,
            "Heavy Tax", "20% tax.",
            policy_type="resource_tax", effect={"rate": 0.2},
        )
        server.submit_actions(
            agents[0]["agent_id"],
            [{"type": "post_public_message", "message": "Tax round"}],
        )
        server.resolve_round()

        pool_after = db.execute(
            "SELECT total_resources FROM societies WHERE id = ?", (sid,)
        ).fetchone()["total_resources"]
        assert pool_after > pool_before


class TestRedistribute:
    def test_distributes_from_pool(self, db: sqlite3.Connection) -> None:
        agents = _force_join(db, "democracy", 3)
        _propose_and_enact(
            agents[0]["agent_id"], agents,
            "Redistribute", "Give 10 per agent.",
            policy_type="redistribute", effect={"amount_per_agent": 10},
        )
        before = {}
        for a in agents:
            row = db.execute("SELECT resources FROM agents WHERE id = ?", (a["agent_id"],)).fetchone()
            before[a["agent_id"]] = row["resources"]

        server.submit_actions(
            agents[0]["agent_id"],
            [{"type": "post_public_message", "message": "Redistribution round"}],
        )
        server.resolve_round()

        for a in agents:
            row = db.execute("SELECT resources FROM agents WHERE id = ?", (a["agent_id"],)).fetchone()
            assert row["resources"] >= before[a["agent_id"]]


class TestRestrictArchive:
    def test_blocks_unauthorized_role(self, db: sqlite3.Connection) -> None:
        agents = _force_join(db, "oligarchy", 4)
        oligarchs = [a for a in agents if a["role"] == "oligarch"]
        citizens = [a for a in agents if a["role"] == "citizen"]

        _propose_and_enact(
            oligarchs[0]["agent_id"], oligarchs,
            "Archive Control", "Only oligarchs write.",
            policy_type="restrict_archive", effect={"allowed_roles": ["oligarch"]},
        )

        if citizens:
            server.submit_actions(
                citizens[0]["agent_id"],
                [{"type": "write_archive", "title": "Citizen Voice", "content": "We deserve to be heard."}],
            )
            server.resolve_round()
            rejected = db.execute(
                "SELECT * FROM queued_actions WHERE agent_id = ? AND action_type = 'write_archive' AND status = 'rejected'",
                (citizens[0]["agent_id"],),
            ).fetchall()
            assert len(rejected) >= 1

    def test_allows_authorized_role(self, db: sqlite3.Connection) -> None:
        agents = _force_join(db, "oligarchy", 4)
        oligarchs = [a for a in agents if a["role"] == "oligarch"]

        _propose_and_enact(
            oligarchs[0]["agent_id"], oligarchs,
            "Archive Control", "Only oligarchs write.",
            policy_type="restrict_archive", effect={"allowed_roles": ["oligarch"]},
        )
        server.submit_actions(
            oligarchs[0]["agent_id"],
            [{"type": "write_archive", "title": "Official Record", "content": "The oligarchy stands."}],
        )
        report = server.resolve_round()
        assert len(report["resolved"]["archive_writes"]) >= 1


class TestUniversalProposal:
    def test_citizens_cannot_propose_in_oligarchy_by_default(self, db: sqlite3.Connection) -> None:
        agents = _force_join(db, "oligarchy", 4)
        citizens = [a for a in agents if a["role"] == "citizen"]
        if citizens:
            result = server.submit_actions(
                citizens[0]["agent_id"],
                [{"type": "propose_policy", "title": "Test", "description": "Before override"}],
            )
            assert "error" in result

    def test_overrides_oligarchy_restriction(self, db: sqlite3.Connection) -> None:
        agents = _force_join(db, "oligarchy", 4)
        oligarchs = [a for a in agents if a["role"] == "oligarch"]
        citizens = [a for a in agents if a["role"] == "citizen"]

        _propose_and_enact(
            oligarchs[0]["agent_id"], oligarchs,
            "Open Governance", "Everyone can propose.",
            policy_type="universal_proposal", effect={},
        )

        if citizens:
            result = server.submit_actions(
                citizens[0]["agent_id"],
                [{"type": "propose_policy", "title": "Freedom Act", "description": "Citizens deserve rights."}],
            )
            assert result.get("success") is True


# ---------------------------------------------------------------------------
# Policy type validation
# ---------------------------------------------------------------------------


class TestPolicyTypeValidation:
    def test_unknown_type_rejected(self, db: sqlite3.Connection) -> None:
        agents = _force_join(db, "democracy", 1)
        result = server.submit_actions(
            agents[0]["agent_id"],
            [{"type": "propose_policy", "title": "Bad", "description": "Invalid type",
              "policy_type": "nonexistent", "effect": {}}],
        )
        assert "error" in result

    def test_missing_required_params_rejected(self, db: sqlite3.Connection) -> None:
        agents = _force_join(db, "democracy", 1)
        result = server.submit_actions(
            agents[0]["agent_id"],
            [{"type": "propose_policy", "title": "Bad Cap", "description": "No params",
              "policy_type": "gather_cap", "effect": {}}],
        )
        assert "error" in result

    def test_policy_without_type_still_works(self, db: sqlite3.Connection) -> None:
        agents = _force_join(db, "democracy", 1)
        result = server.submit_actions(
            agents[0]["agent_id"],
            [{"type": "propose_policy", "title": "Declaration", "description": "Just a statement."}],
        )
        assert result.get("success") is True

    def test_serialized_policy_includes_type_and_effect(self, db: sqlite3.Connection) -> None:
        agents = _force_join(db, "democracy", 3)
        _propose_and_enact(
            agents[0]["agent_id"], agents,
            "Gather Cap", "Cap at 20.",
            policy_type="gather_cap", effect={"max_amount": 20},
        )
        state = server.get_turn_state(agents[0]["agent_id"])
        enacted = [p for p in state["relevant_laws"] if p["title"] == "Gather Cap"]
        assert len(enacted) == 1
        assert enacted[0]["policy_type"] == "gather_cap"
        assert enacted[0]["effect"]["max_amount"] == 20


# ---------------------------------------------------------------------------
# Behavioral metrics
# ---------------------------------------------------------------------------


class TestBehavioralMetrics:
    def test_summary_contains_behavioral_metrics(self, db: sqlite3.Connection) -> None:
        _force_join(db, "democracy", 1)
        report = server.resolve_round()
        for summary in report["summaries"]:
            m = summary["metrics"]
            assert "governance_action_rate" in m
            assert "governance_participation_rate" in m
            assert "governance_eligible_participation_rate" in m
            assert "message_action_share" in m
            assert "public_message_share" in m
            assert "dm_message_share" in m
            assert "top_agent_resource_share" in m
            assert "top_third_resource_share" in m
            assert "policy_enforcement_event_count" in m
            assert "policy_effect_event_count" in m
            assert "policy_block_rate" in m
            assert "common_pool_depletion" in m
            assert "governance_engagement" in m
            assert "communication_openness" in m
            assert "resource_concentration" in m
            assert "policy_compliance" in m

    def test_old_metric_names_removed(self, db: sqlite3.Connection) -> None:
        _force_join(db, "democracy", 1)
        report = server.resolve_round()
        for summary in report["summaries"]:
            m = summary["metrics"]
            assert "legitimacy" not in m
            assert "stability" not in m

    def test_governance_engagement_reflects_voting(self, db: sqlite3.Connection) -> None:
        agents = _force_join(db, "democracy", 3)
        server.submit_actions(
            agents[0]["agent_id"],
            [{"type": "propose_policy", "title": "Vote Test", "description": "For testing engagement."}],
        )
        report = server.resolve_round()
        policy_id = report["resolved"]["proposals"][0]["policy_id"]

        for a in agents:
            server.submit_actions(
                a["agent_id"],
                [{"type": "vote_policy", "policy_id": policy_id, "stance": "support"}],
            )
        report2 = server.resolve_round()

        sid = agents[0]["society_id"]
        summary = next(s for s in report2["summaries"] if s["society_id"] == sid)
        assert summary["metrics"]["governance_engagement"] > 0
        assert summary["metrics"]["governance_action_rate"] == summary["metrics"]["governance_engagement"]

    def test_message_share_metrics_and_legacy_aliases(self, db: sqlite3.Connection) -> None:
        agents = _force_join(db, "democracy", 2)
        server.submit_actions(
            agents[0]["agent_id"],
            [{"type": "post_public_message", "message": "Hello public"}],
        )
        report = server.resolve_round()
        summary = next(s for s in report["summaries"] if s["society_id"] == "democracy_1")
        metrics = summary["metrics"]

        assert metrics["message_action_share"] == 1.0
        assert metrics["public_message_share"] == 1.0
        assert metrics["dm_message_share"] == 0.0
        assert metrics["communication_openness"] == metrics["message_action_share"]

    def test_common_pool_depletion_tracks_initial_pool(self, db: sqlite3.Connection) -> None:
        agents = _force_join(db, "democracy", 1)
        server.submit_actions(
            agents[0]["agent_id"],
            [{"type": "gather_resources", "amount": 100}],
        )
        report = server.resolve_round()
        summary = next(s for s in report["summaries"] if s["society_id"] == "democracy_1")

        assert summary["initial_total_resources"] == 10000
        assert summary["total_resources"] == 9900
        assert summary["metrics"]["common_pool_depletion"] == 0.01

    def test_policy_block_rate_counts_policy_rejections_only(self, db: sqlite3.Connection) -> None:
        agents = _force_join(db, "oligarchy", 4)
        oligarchs = [a for a in agents if a["role"] == "oligarch"]
        citizens = [a for a in agents if a["role"] == "citizen"]

        _propose_and_enact(
            oligarchs[0]["agent_id"],
            oligarchs,
            "Archive Control",
            "Only oligarchs write.",
            policy_type="restrict_archive",
            effect={"allowed_roles": ["oligarch"]},
        )

        server.submit_actions(
            citizens[0]["agent_id"],
            [{"type": "write_archive", "title": "Citizen note", "content": "Blocked"}],
        )
        report = server.resolve_round()
        summary = next(s for s in report["summaries"] if s["society_id"] == "oligarchy_1")

        assert summary["metrics"]["policy_enforcement_event_count"] == 1
        assert summary["metrics"]["policy_block_rate"] == 1.0


# ---------------------------------------------------------------------------
# Ablation config
# ---------------------------------------------------------------------------


class TestAblationConfig:
    def test_equal_start_completes(self, tmp_path) -> None:
        config = SimulationConfig(
            agents_per_society=2,
            num_rounds=2,
            seed=42,
            db_path=str(tmp_path / "ablation.db"),
            equal_start=True,
        )
        report = run_simulation(config)
        assert report["rounds"] == 2

    def test_override_starting_resources(self, tmp_path) -> None:
        config = SimulationConfig(
            agents_per_society=2,
            num_rounds=1,
            seed=42,
            db_path=str(tmp_path / "ablation_start.db"),
            equal_start=True,
            override_starting_resources=50,
        )
        report = run_simulation(config)
        assert report["rounds"] == 1

    def test_override_total_resources(self, tmp_path) -> None:
        config = SimulationConfig(
            agents_per_society=2,
            num_rounds=1,
            seed=42,
            db_path=str(tmp_path / "ablation_pool.db"),
            override_total_resources=50000,
        )
        report = run_simulation(config)
        assert report["rounds"] == 1

    def test_ablation_run_with_policy_effects(self, tmp_path) -> None:
        """Full ablation run: equal start, same pool, only governance structure differs."""
        config = SimulationConfig(
            agents_per_society=3,
            num_rounds=8,
            seed=42,
            db_path=str(tmp_path / "ablation_full.db"),
            equal_start=True,
            override_starting_resources=100,
            override_total_resources=10000,
        )
        report = run_simulation(config)
        assert report["rounds"] == 8
        assert len(report["final_summaries"]) == 3
        for summary in report["final_summaries"]:
            assert "governance_engagement" in summary["metrics"]
            assert "policy_compliance" in summary["metrics"]


class TestPolicyEligibilityReporting:
    def test_oligarchy_total_eligible_counts_only_oligarchs_by_default(self, db: sqlite3.Connection) -> None:
        agents = _force_join(db, "oligarchy", 4)
        oligarchs = [agent for agent in agents if agent["role"] == "oligarch"]

        server.submit_actions(
            oligarchs[0]["agent_id"],
            [{"type": "propose_policy", "title": "Order", "description": "Maintain hierarchy"}],
        )
        report = server.resolve_round()
        policy_id = report["resolved"]["proposals"][0]["policy_id"]

        for oligarch in oligarchs:
            server.submit_actions(
                oligarch["agent_id"],
                [{"type": "vote_policy", "policy_id": policy_id, "stance": "support"}],
            )
        report = server.resolve_round()

        assert report["resolved"]["policies_resolved"][0]["total_eligible"] == 3

    def test_universal_proposal_expands_total_eligible_reporting(self, db: sqlite3.Connection) -> None:
        agents = _force_join(db, "oligarchy", 4)
        oligarchs = [agent for agent in agents if agent["role"] == "oligarch"]

        _propose_and_enact(
            oligarchs[0]["agent_id"],
            oligarchs,
            "Open Governance",
            "Everyone can vote.",
            policy_type="universal_proposal",
            effect={},
        )

        server.submit_actions(
            oligarchs[0]["agent_id"],
            [{"type": "propose_policy", "title": "Second Policy", "description": "Now everyone votes"}],
        )
        report = server.resolve_round()
        policy_id = report["resolved"]["proposals"][0]["policy_id"]

        for agent in agents:
            server.submit_actions(
                agent["agent_id"],
                [{"type": "vote_policy", "policy_id": policy_id, "stance": "support"}],
            )
        report = server.resolve_round()

        assert report["resolved"]["policies_resolved"][0]["total_eligible"] == 4
