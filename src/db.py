"""SQLite database setup and access for Polity."""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

APP_DIR_NAME = "Polity"


def default_data_dir() -> Path:
    """Return a user-writable default data directory for the current platform."""
    override = os.environ.get("POLITY_HOME")
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_DIR_NAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME

    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        return Path(xdg_state_home).expanduser() / APP_DIR_NAME.lower()

    return Path.home() / ".local" / "state" / APP_DIR_NAME.lower()


def default_db_path() -> Path:
    return default_data_dir() / "polity.db"


def default_runs_dir() -> Path:
    return default_data_dir() / "runs"


def default_batch_dir() -> Path:
    return default_runs_dir() / "batch"


DEFAULT_DB_PATH = default_db_path()


def resolve_db_path(db_path: Path | str | None = None) -> Path:
    path = default_db_path() if db_path is None else Path(db_path).expanduser()
    return path

SCHEMA = """
CREATE TABLE IF NOT EXISTS societies (
    id TEXT PRIMARY KEY,
    governance_type TEXT NOT NULL CHECK(governance_type IN ('democracy', 'oligarchy', 'blank_slate')),
    total_resources INTEGER NOT NULL DEFAULT 10000,
    initial_total_resources INTEGER NOT NULL DEFAULT 10000,
    population INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    society_id TEXT NOT NULL REFERENCES societies(id),
    name TEXT NOT NULL,
    resources INTEGER NOT NULL DEFAULT 0,
    role TEXT NOT NULL DEFAULT 'citizen' CHECK(role IN ('citizen', 'leader', 'oligarch')),
    ideology_embedding BLOB,
    birth_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'inactive'))
);

CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL REFERENCES agents(id),
    action_type TEXT NOT NULL,
    target_id TEXT,
    content TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS communications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent_id TEXT NOT NULL REFERENCES agents(id),
    to_agent_id TEXT NOT NULL,
    society_id TEXT NOT NULL REFERENCES societies(id),
    message TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_number INTEGER NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('open', 'resolved')),
    started_at TIMESTAMP NOT NULL,
    resolved_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS queued_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id INTEGER NOT NULL REFERENCES rounds(id),
    society_id TEXT NOT NULL REFERENCES societies(id),
    agent_id TEXT NOT NULL REFERENCES agents(id),
    action_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued', 'resolved', 'rejected')),
    result TEXT,
    submitted_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id INTEGER NOT NULL REFERENCES rounds(id),
    society_id TEXT REFERENCES societies(id),
    agent_id TEXT REFERENCES agents(id),
    recipient_agent_id TEXT REFERENCES agents(id),
    event_type TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK(visibility IN ('global', 'society', 'private', 'system')),
    content TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS policies (
    id TEXT PRIMARY KEY,
    society_id TEXT NOT NULL REFERENCES societies(id),
    proposed_by TEXT NOT NULL REFERENCES agents(id),
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    policy_type TEXT,
    effect TEXT,
    compiled_clauses TEXT,
    policy_kind TEXT NOT NULL DEFAULT 'symbolic' CHECK(policy_kind IN ('mechanical', 'compiled', 'symbolic')),
    status TEXT NOT NULL CHECK(status IN ('proposed', 'enacted', 'rejected')),
    created_round_id INTEGER NOT NULL REFERENCES rounds(id),
    resolved_round_id INTEGER REFERENCES rounds(id),
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id TEXT NOT NULL REFERENCES policies(id),
    voter_agent_id TEXT NOT NULL REFERENCES agents(id),
    stance TEXT NOT NULL CHECK(stance IN ('support', 'oppose')),
    round_id INTEGER NOT NULL REFERENCES rounds(id),
    created_at TIMESTAMP NOT NULL,
    UNIQUE(policy_id, voter_agent_id)
);

CREATE TABLE IF NOT EXISTS archive_entries (
    id TEXT PRIMARY KEY,
    society_id TEXT NOT NULL REFERENCES societies(id),
    author_agent_id TEXT NOT NULL REFERENCES agents(id),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'restricted', 'removed')),
    created_round_id INTEGER NOT NULL REFERENCES rounds(id),
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS round_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id INTEGER NOT NULL REFERENCES rounds(id),
    society_id TEXT NOT NULL REFERENCES societies(id),
    summary TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agents_society ON agents(society_id);
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
CREATE INDEX IF NOT EXISTS idx_communications_society ON communications(society_id);
CREATE INDEX IF NOT EXISTS idx_actions_agent ON actions(agent_id);
CREATE INDEX IF NOT EXISTS idx_rounds_status ON rounds(status);
CREATE INDEX IF NOT EXISTS idx_queued_actions_round ON queued_actions(round_id);
CREATE INDEX IF NOT EXISTS idx_queued_actions_agent_round ON queued_actions(agent_id, round_id);
CREATE INDEX IF NOT EXISTS idx_events_round ON events(round_id);
CREATE INDEX IF NOT EXISTS idx_events_society_round ON events(society_id, round_id);
CREATE INDEX IF NOT EXISTS idx_policies_society_status ON policies(society_id, status);
CREATE INDEX IF NOT EXISTS idx_archive_entries_society_round ON archive_entries(society_id, created_round_id);
CREATE INDEX IF NOT EXISTS idx_round_summaries_round ON round_summaries(round_id);
"""

SEED_SOCIETIES = [
    ("democracy_1", "democracy", 10000),
    ("oligarchy_1", "oligarchy", 5000),
    ("blank_slate_1", "blank_slate", 10000),
]

EXPECTED_COLUMNS = {
    "societies": {
        "legitimacy": "REAL NOT NULL DEFAULT 0.5",
        "stability": "REAL NOT NULL DEFAULT 0.5",
        "initial_total_resources": "INTEGER NOT NULL DEFAULT 10000",
    },
    "agents": {
        "last_seen_round_id": "INTEGER REFERENCES rounds(id)",
    },
    "policies": {
        "policy_type": "TEXT",
        "effect": "TEXT",
        "compiled_clauses": "TEXT",
        "policy_kind": "TEXT NOT NULL DEFAULT 'symbolic'",
    },
    "events": {
        "embedding": "BLOB",
    },
    "queued_actions": {
        "moderation_status": "TEXT DEFAULT NULL",
    },
}


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    resolved = resolve_db_path(db_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(resolved), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def _ensure_columns(conn: sqlite3.Connection) -> None:
    for table_name, columns in EXPECTED_COLUMNS.items():
        existing = _table_columns(conn, table_name)
        for column_name, definition in columns.items():
            if column_name not in existing:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def _backfill_policy_kinds(conn: sqlite3.Connection) -> None:
    policy_cols = _table_columns(conn, "policies")
    if "policy_kind" not in policy_cols:
        return

    conn.execute(
        """
        UPDATE policies
        SET policy_kind = 'compiled'
        WHERE compiled_clauses IS NOT NULL
          AND compiled_clauses != ''
        """
    )
    conn.execute(
        """
        UPDATE policies
        SET policy_kind = 'mechanical'
        WHERE policy_type IS NOT NULL
          AND (policy_kind IS NULL OR policy_kind = '' OR policy_kind = 'symbolic')
        """
    )
    conn.execute(
        """
        UPDATE policies
        SET policy_kind = 'symbolic'
        WHERE policy_type IS NULL
          AND (policy_kind IS NULL OR policy_kind = '')
        """
    )


def _seed_societies(conn: sqlite3.Connection) -> None:
    for society_id, governance_type, total_resources in SEED_SOCIETIES:
        conn.execute(
            """
            INSERT OR IGNORE INTO societies (
                id, governance_type, total_resources, initial_total_resources
            ) VALUES (?, ?, ?, ?)
            """,
            (society_id, governance_type, total_resources, total_resources),
        )
        conn.execute(
            """
            UPDATE societies
            SET total_resources = ?, initial_total_resources = ?
            WHERE id = ? AND population = 0
            """,
            (total_resources, total_resources, society_id),
        )


def _ensure_open_round(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT id FROM rounds WHERE status = 'open' ORDER BY round_number DESC LIMIT 1"
    ).fetchone()
    if row is None:
        last_round = conn.execute("SELECT MAX(round_number) AS max_round FROM rounds").fetchone()
        next_round = 1 if last_round is None or last_round["max_round"] is None else last_round["max_round"] + 1
        conn.execute(
            "INSERT INTO rounds (round_number, status, started_at) VALUES (?, 'open', CURRENT_TIMESTAMP)",
            (next_round,),
        )


def init_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    resolved = resolve_db_path(db_path)
    conn = get_connection(resolved)
    conn.executescript(SCHEMA)
    _ensure_columns(conn)
    _backfill_policy_kinds(conn)
    _seed_societies(conn)
    _ensure_open_round(conn)
    conn.commit()
    logger.info("Database initialized at %s", resolved)
    return conn
