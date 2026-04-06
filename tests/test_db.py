"""Tests for database initialization, schema, and seeding."""

import sqlite3
from pathlib import Path

from src.db import default_data_dir, init_db, SEED_SOCIETIES


def test_init_creates_all_tables(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "test.db")
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    expected = {
        "societies",
        "agents",
        "actions",
        "communications",
        "rounds",
        "queued_actions",
        "events",
        "policies",
        "policy_votes",
        "archive_entries",
        "round_summaries",
    }
    assert expected.issubset(tables)
    conn.close()


def test_societies_are_seeded(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "test.db")
    rows = conn.execute("SELECT id, governance_type, total_resources FROM societies ORDER BY id").fetchall()
    seeded = [(r["id"], r["governance_type"], r["total_resources"]) for r in rows]
    assert seeded == sorted(SEED_SOCIETIES, key=lambda t: t[0])
    conn.close()


def test_open_round_exists_after_init(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "test.db")
    row = conn.execute("SELECT * FROM rounds WHERE status = 'open'").fetchone()
    assert row is not None
    assert row["round_number"] == 1
    conn.close()


def test_wal_mode_enabled(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "test.db")
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    conn.close()


def test_foreign_keys_enabled(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "test.db")
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1
    conn.close()


def test_expected_columns_added(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "test.db")
    society_cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(societies)").fetchall()
    }
    assert "legitimacy" in society_cols
    assert "stability" in society_cols

    agent_cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(agents)").fetchall()
    }
    assert "last_seen_round_id" in agent_cols

    policy_cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(policies)").fetchall()
    }
    assert "policy_type" in policy_cols
    assert "effect" in policy_cols
    assert "compiled_clauses" in policy_cols
    assert "policy_kind" in policy_cols
    conn.close()


def test_policy_kinds_backfilled_for_existing_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE policies (
            id TEXT PRIMARY KEY,
            society_id TEXT NOT NULL,
            proposed_by TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            policy_type TEXT,
            effect TEXT,
            status TEXT NOT NULL,
            created_round_id INTEGER NOT NULL,
            resolved_round_id INTEGER,
            created_at TIMESTAMP NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO policies (
            id, society_id, proposed_by, title, description, policy_type, effect,
            status, created_round_id, created_at
        ) VALUES (
            'mechanical-law', 'democracy_1', 'agent-1', 'Cap', 'Cap gathering',
            'gather_cap', '{"max_amount": 10}', 'enacted', 1, CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        INSERT INTO policies (
            id, society_id, proposed_by, title, description, policy_type, effect,
            status, created_round_id, created_at
        ) VALUES (
            'symbolic-law', 'democracy_1', 'agent-1', 'Declaration', 'Just words',
            NULL, NULL, 'enacted', 1, CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()

    upgraded = init_db(db_path)
    rows = upgraded.execute(
        "SELECT id, policy_kind FROM policies ORDER BY id"
    ).fetchall()
    assert [(row["id"], row["policy_kind"]) for row in rows] == [
        ("mechanical-law", "mechanical"),
        ("symbolic-law", "symbolic"),
    ]
    upgraded.close()


def test_reinit_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    conn1 = init_db(db_path)
    conn1.close()
    conn2 = init_db(db_path)
    rows = conn2.execute("SELECT COUNT(*) AS c FROM societies").fetchone()
    assert rows["c"] == len(SEED_SOCIETIES)
    rounds = conn2.execute("SELECT COUNT(*) AS c FROM rounds WHERE status = 'open'").fetchone()
    assert rounds["c"] == 1
    conn2.close()


def test_init_creates_parent_directories(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "path" / "test.db"
    assert not db_path.parent.exists()
    conn = init_db(db_path)
    assert db_path.parent.exists()
    conn.close()


def test_default_data_dir_respects_polity_home(monkeypatch, tmp_path: Path) -> None:
    override = tmp_path / "portable-polity-home"
    monkeypatch.setenv("POLITY_HOME", str(override))
    assert default_data_dir() == override
