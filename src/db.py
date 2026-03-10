"""SQLite database setup and access for Polity."""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent.parent / "polity.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS societies (
    id TEXT PRIMARY KEY,
    governance_type TEXT NOT NULL CHECK(governance_type IN ('democracy', 'oligarchy', 'blank_slate')),
    total_resources INTEGER NOT NULL DEFAULT 10000,
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

CREATE INDEX IF NOT EXISTS idx_agents_society ON agents(society_id);
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
CREATE INDEX IF NOT EXISTS idx_communications_society ON communications(society_id);
CREATE INDEX IF NOT EXISTS idx_actions_agent ON actions(agent_id);
"""

SEED_SOCIETIES = [
    ("democracy_1", "democracy", 10000),
    ("oligarchy_1", "oligarchy", 10000),
    ("blank_slate_1", "blank_slate", 10000),
]


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = get_connection(db_path)
    conn.executescript(SCHEMA)

    for sid, gtype, resources in SEED_SOCIETIES:
        conn.execute(
            "INSERT OR IGNORE INTO societies (id, governance_type, total_resources) VALUES (?, ?, ?)",
            (sid, gtype, resources),
        )
    conn.commit()
    logger.info("Database initialized at %s", db_path)
    return conn
