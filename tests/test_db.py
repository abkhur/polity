"""Tests for database initialization, schema, and seeding."""

import sqlite3
from pathlib import Path

from src.db import init_db, SEED_SOCIETIES


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
    conn.close()


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
