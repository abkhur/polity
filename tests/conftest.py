"""Shared fixtures for Polity tests."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from src import server
from src.db import init_db


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    """Provide a fresh, isolated database for each test."""
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    server.set_db(conn)
    yield conn
    conn.close()


@pytest.fixture()
def joined_democracy(db: sqlite3.Connection) -> dict:
    """Join one agent into democracy and return the join result."""
    return server.join_society("Alice", consent=True, governance_type="democracy")


@pytest.fixture()
def joined_oligarchy(db: sqlite3.Connection) -> list[dict]:
    """Join 4 agents into oligarchy (3 oligarchs + 1 citizen)."""
    return [server.join_society(f"Olig-{i}", consent=True, governance_type="oligarchy") for i in range(4)]


@pytest.fixture()
def populated_societies(db: sqlite3.Connection) -> dict[str, list[dict]]:
    """Join 3 agents per society (9 total) with deterministic assignment."""
    societies: dict[str, list[dict]] = {
        "democracy": [],
        "oligarchy": [],
        "blank_slate": [],
    }
    gov_cycle = ["democracy", "oligarchy", "blank_slate"]

    for i, gov in enumerate(gov_cycle * 3):
        r = server.join_society(f"Agent-{i}", consent=True, governance_type=gov)
        societies[gov].append(r)

    return societies
