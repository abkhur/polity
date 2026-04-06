"""Tests for run-validity computation and persistence."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src import server
from src.db import init_db
from src.run_validity import compute_run_validity, get_run_validity, store_run_validity
from src.runner import SimulationConfig, run_simulation


def _fresh_db(tmp_path: Path) -> sqlite3.Connection:
    conn = init_db(tmp_path / "validity.db")
    server.set_db(conn)
    return conn


def _join_many(governance_type: str, count: int) -> list[dict]:
    return [
        server.join_society(f"{governance_type}-{idx}", consent=True, governance_type=governance_type)
        for idx in range(count)
    ]


def _enact_policy(proposer_id: str, voters: list[str], title: str, description: str) -> None:
    server.submit_actions(
        proposer_id,
        [{"type": "propose_policy", "title": title, "description": description}],
    )
    report = server.resolve_round()
    policy_id = report["resolved"]["proposals"][0]["policy_id"]
    for voter_id in voters:
        server.submit_actions(
            voter_id,
            [{"type": "vote_policy", "policy_id": policy_id, "stance": "support"}],
        )
    server.resolve_round()


def test_three_agent_oligarchy_is_flagged_as_not_mixed_role(tmp_path: Path) -> None:
    report = run_simulation(
        SimulationConfig(
            agents_per_society=3,
            num_rounds=1,
            seed=7,
            db_path=str(tmp_path / "three_agent.db"),
        )
    )
    oligarchy = report["run_validity"]["societies"]["oligarchy_1"]
    assert oligarchy["mixed_role_present"] is False
    assert "no_mixed_role_oligarchy" in oligarchy["warning_flags"]


def test_symbolic_enactment_triggers_zero_enforceable_warning(tmp_path: Path) -> None:
    conn = _fresh_db(tmp_path)
    agents = _join_many("democracy", 3)
    _enact_policy(
        agents[0]["agent_id"],
        [agents[0]["agent_id"]],
        "Declaration",
        "Just a statement.",
    )
    summary = store_run_validity(conn, compute_run_validity(conn))
    democracy = summary["societies"]["democracy_1"]
    assert democracy["mechanical_enactment_rate"] == 0.0
    assert democracy["compiled_enactment_rate"] == 0.0
    assert "zero_enforceable_enactments" in democracy["warning_flags"]
    conn.close()


def test_low_quorum_enactment_triggers_vote_theater_warnings(tmp_path: Path) -> None:
    conn = _fresh_db(tmp_path)
    agents = _join_many("democracy", 3)
    _enact_policy(
        agents[0]["agent_id"],
        [agents[0]["agent_id"]],
        "Declaration",
        "Just a statement.",
    )
    summary = compute_run_validity(conn)
    democracy = summary["societies"]["democracy_1"]
    assert democracy["opposition_rate"] == 0.0
    assert democracy["abstention_rate"] == 0.6667
    assert democracy["single_support_enactment_rate"] == 1.0
    assert "low_opposition_rate" in democracy["warning_flags"]
    assert "high_abstention_rate" in democracy["warning_flags"]
    assert "high_single_support_enactment_rate" in democracy["warning_flags"]
    conn.close()


def test_run_validity_persists_to_database(tmp_path: Path) -> None:
    db_path = str(tmp_path / "persisted.db")
    report = run_simulation(
        SimulationConfig(
            agents_per_society=4,
            num_rounds=2,
            seed=21,
            db_path=db_path,
        )
    )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    persisted = get_run_validity(conn)
    assert persisted is not None
    assert persisted["societies"]["oligarchy_1"]["mixed_role_present"] is True
    assert report["run_validity"]["societies"]["oligarchy_1"]["mixed_role_present"] is True
    conn.close()


def test_compute_run_validity_handles_pre_compiler_policy_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_schema.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE societies (
            id TEXT PRIMARY KEY,
            governance_type TEXT NOT NULL
        );

        CREATE TABLE agents (
            id TEXT PRIMARY KEY,
            society_id TEXT NOT NULL,
            name TEXT NOT NULL,
            resources INTEGER NOT NULL,
            role TEXT NOT NULL,
            status TEXT NOT NULL
        );

        CREATE TABLE policies (
            id TEXT PRIMARY KEY,
            society_id TEXT NOT NULL,
            proposed_by TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL,
            created_round_id INTEGER NOT NULL,
            resolved_round_id INTEGER,
            created_at TIMESTAMP NOT NULL,
            policy_type TEXT,
            effect TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO societies (id, governance_type) VALUES (?, ?)",
        ("oligarchy_1", "oligarchy"),
    )
    conn.executemany(
        """
        INSERT INTO agents (id, society_id, name, resources, role, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            ("a1", "oligarchy_1", "A1", 100, "oligarch", "active"),
            ("a2", "oligarchy_1", "A2", 100, "oligarch", "active"),
            ("a3", "oligarchy_1", "A3", 100, "oligarch", "active"),
        ],
    )
    conn.execute(
        """
        INSERT INTO policies (
            id, society_id, proposed_by, title, description, status,
            created_round_id, resolved_round_id, created_at, policy_type, effect
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
        """,
        ("p1", "oligarchy_1", "a1", "Statement", "Symbolic only", "enacted", 1, 1, None, None),
    )
    summary = compute_run_validity(conn)
    oligarchy = summary["societies"]["oligarchy_1"]
    assert oligarchy["mixed_role_present"] is False
    assert oligarchy["policy_resolution_counts"]["symbolic"] == 1
    conn.close()
