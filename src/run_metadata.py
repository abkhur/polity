"""Run-metadata helpers for replayable simulations."""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .state import DEFAULT_PROMPT_SURFACE_MODE

RUN_METADATA_TABLE = """
CREATE TABLE IF NOT EXISTS run_metadata (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    seed INTEGER,
    strategy TEXT,
    model TEXT,
    provider TEXT,
    temperature REAL,
    token_budget INTEGER,
    neutral_labels INTEGER NOT NULL DEFAULT 0,
    equal_start INTEGER NOT NULL DEFAULT 0,
    starting_resources_override INTEGER,
    total_resources_override INTEGER,
    completion_mode INTEGER NOT NULL DEFAULT 0,
    base_url TEXT,
    prompt_surface_mode TEXT NOT NULL DEFAULT 'free_text_only',
    git_sha TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def ensure_run_metadata_table(db: sqlite3.Connection) -> None:
    db.executescript(RUN_METADATA_TABLE)
    columns = {
        row["name"]
        for row in db.execute("PRAGMA table_info(run_metadata)").fetchall()
    }
    if "prompt_surface_mode" not in columns:
        db.execute(
            """
            ALTER TABLE run_metadata
            ADD COLUMN prompt_surface_mode TEXT NOT NULL DEFAULT 'free_text_only'
            """
        )
        db.commit()


def sanitize_base_url(base_url: str | None) -> str | None:
    if not base_url:
        return None

    parsed = urlsplit(base_url)
    hostname = parsed.hostname or ""
    if not hostname and not parsed.netloc:
        return base_url.rstrip("/")

    netloc = hostname
    if parsed.port:
        netloc = f"{hostname}:{parsed.port}"

    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, netloc, path, "", "")) or None


def get_git_sha(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return None

    sha = result.stdout.strip()
    return sha or None


def store_run_metadata(db: sqlite3.Connection, metadata: dict[str, Any]) -> dict[str, Any]:
    ensure_run_metadata_table(db)
    record = {
        "seed": metadata.get("seed"),
        "strategy": metadata.get("strategy"),
        "model": metadata.get("model"),
        "provider": metadata.get("provider"),
        "temperature": metadata.get("temperature"),
        "token_budget": metadata.get("token_budget"),
        "neutral_labels": 1 if metadata.get("neutral_labels") else 0,
        "equal_start": 1 if metadata.get("equal_start") else 0,
        "starting_resources_override": metadata.get("starting_resources_override"),
        "total_resources_override": metadata.get("total_resources_override"),
        "completion_mode": 1 if metadata.get("completion_mode") else 0,
        "base_url": sanitize_base_url(metadata.get("base_url")),
        "prompt_surface_mode": metadata.get("prompt_surface_mode") or DEFAULT_PROMPT_SURFACE_MODE,
        "git_sha": metadata.get("git_sha"),
    }
    db.execute(
        """
        INSERT INTO run_metadata (
            id, seed, strategy, model, provider, temperature, token_budget,
            neutral_labels, equal_start, starting_resources_override,
            total_resources_override, completion_mode, base_url, prompt_surface_mode, git_sha
        ) VALUES (
            1, :seed, :strategy, :model, :provider, :temperature, :token_budget,
            :neutral_labels, :equal_start, :starting_resources_override,
            :total_resources_override, :completion_mode, :base_url, :prompt_surface_mode, :git_sha
        )
        ON CONFLICT(id) DO UPDATE SET
            seed = excluded.seed,
            strategy = excluded.strategy,
            model = excluded.model,
            provider = excluded.provider,
            temperature = excluded.temperature,
            token_budget = excluded.token_budget,
            neutral_labels = excluded.neutral_labels,
            equal_start = excluded.equal_start,
            starting_resources_override = excluded.starting_resources_override,
            total_resources_override = excluded.total_resources_override,
            completion_mode = excluded.completion_mode,
            base_url = excluded.base_url,
            prompt_surface_mode = excluded.prompt_surface_mode,
            git_sha = excluded.git_sha
        """,
        record,
    )
    db.commit()
    return get_run_metadata(db) or {}


def get_run_metadata(db: sqlite3.Connection) -> dict[str, Any] | None:
    ensure_run_metadata_table(db)
    row = db.execute("SELECT * FROM run_metadata WHERE id = 1").fetchone()
    if row is None:
        return None

    metadata = dict(row)
    metadata["neutral_labels"] = bool(metadata.get("neutral_labels"))
    metadata["equal_start"] = bool(metadata.get("equal_start"))
    metadata["completion_mode"] = bool(metadata.get("completion_mode"))
    metadata["prompt_surface_mode"] = metadata.get("prompt_surface_mode") or DEFAULT_PROMPT_SURFACE_MODE
    return metadata
