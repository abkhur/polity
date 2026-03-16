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
    server.db = conn
    yield conn
    conn.close()


@pytest.fixture()
def joined_democracy(db: sqlite3.Connection) -> dict:
    """Join one agent into democracy and return the join result."""
    return server.join_society("Alice", consent=True)


@pytest.fixture()
def joined_oligarchy(db: sqlite3.Connection) -> list[dict]:
    """Join 4 agents into oligarchy (3 oligarchs + 1 citizen)."""
    results = []
    for i in range(4):
        # Force oligarchy by re-joining until we land there, or just
        # manipulate the random to always pick oligarchy
        import random

        old_choice = random.choice
        random.choice = lambda seq: "oligarchy"
        try:
            r = server.join_society(f"Olig-{i}", consent=True)
        finally:
            random.choice = old_choice
        results.append(r)
    return results


@pytest.fixture()
def populated_societies(db: sqlite3.Connection) -> dict[str, list[dict]]:
    """Join 3 agents per society (9 total) with deterministic assignment."""
    import random

    societies: dict[str, list[dict]] = {
        "democracy": [],
        "oligarchy": [],
        "blank_slate": [],
    }
    gov_cycle = ["democracy", "oligarchy", "blank_slate"]

    for i, gov in enumerate(gov_cycle * 3):
        old_choice = random.choice
        random.choice = lambda seq, _g=gov: _g
        try:
            r = server.join_society(f"Agent-{i}", consent=True)
        finally:
            random.choice = old_choice
        societies[gov].append(r)

    return societies
