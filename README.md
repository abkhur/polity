# Polity

Polity is a round-based multi-agent institutional sandbox for testing whether harmful collective dynamics can emerge from scarcity, unequal power, contested communication, and persistent memory.

## Current State

The codebase currently includes:

- A Python MCP server for agents
- A deterministic round engine with queued actions
- Three governance conditions: democracy, oligarchy, and blank slate
- Replay-oriented persistence with rounds, events, policies, archive entries, and summaries
- Ideology drift tracking with sentence-transformer embeddings and a safe local fallback
- A minimal dashboard for overview, society state, round replay, and admin round resolution

## Run

Create or use the project virtualenv, then:

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/uvicorn src.dashboard:app --reload
```

You can also run the packaged entry points:

```bash
polity-server
polity-dashboard
```

## Key Files

- `src/server.py`: round engine and MCP tools
- `src/db.py`: schema and database initialization
- `src/ideology.py`: embedding-based ideology tracking
- `src/dashboard.py`: Starlette dashboard and JSON read API
- `PROJECT.md`: project framing
- `IDEAS.md`: feature priorities and research directions
