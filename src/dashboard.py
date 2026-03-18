"""Minimal replay-first dashboard for Polity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from . import server
from .db import DEFAULT_DB_PATH, init_db

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _format_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


templates.env.filters["to_pretty_json"] = _format_json


def _ensure_runtime(db_path: str | None = None) -> None:
    if server.db is None:
        server.set_db(init_db(Path(db_path) if db_path else DEFAULT_DB_PATH))


def _current_round() -> dict[str, Any]:
    row = server.db.execute(
        "SELECT * FROM rounds WHERE status = 'open' ORDER BY round_number DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else {"id": None, "round_number": 0, "status": "missing"}


def _society_cards() -> list[dict[str, Any]]:
    current_round = _current_round()
    rows = server.db.execute(
        """
        SELECT id, governance_type, total_resources, population, legitimacy, stability
        FROM societies
        ORDER BY id ASC
        """
    ).fetchall()

    cards: list[dict[str, Any]] = []
    for row in rows:
        society = dict(row)
        summary_row = server.db.execute(
            """
            SELECT summary
            FROM round_summaries
            WHERE society_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (society["id"],),
        ).fetchone()
        latest_summary = json.loads(summary_row["summary"]) if summary_row else None
        queued_count_row = server.db.execute(
            """
            SELECT COUNT(*) AS count
            FROM queued_actions
            WHERE round_id = ? AND society_id = ? AND status = 'queued'
            """,
            (current_round["id"], society["id"]),
        ).fetchone()
        queued_count = int(queued_count_row["count"] or 0)
        cards.append(
            {
                **society,
                "queued_actions": queued_count,
                "latest_summary": latest_summary,
            }
        )
    return cards


def _society_detail(society_id: str) -> dict[str, Any]:
    society_row = server.db.execute("SELECT * FROM societies WHERE id = ?", (society_id,)).fetchone()
    if society_row is None:
        raise KeyError(society_id)

    society = dict(society_row)
    agents = [
        dict(row)
        for row in server.db.execute(
            """
            SELECT id, name, role, resources, status
            FROM agents
            WHERE society_id = ?
            ORDER BY status DESC, resources DESC, name ASC
            """,
            (society_id,),
        ).fetchall()
    ]
    enacted_policies = [
        dict(row)
        for row in server.db.execute(
            """
            SELECT id, title, description, proposed_by, created_at, resolved_round_id
            FROM policies
            WHERE society_id = ? AND status = 'enacted'
            ORDER BY resolved_round_id DESC, created_at DESC
            LIMIT 10
            """,
            (society_id,),
        ).fetchall()
    ]
    pending_policies = [
        dict(row)
        for row in server.db.execute(
            """
            SELECT id, title, description, proposed_by, created_round_id, created_at
            FROM policies
            WHERE society_id = ? AND status = 'proposed'
            ORDER BY created_at DESC
            LIMIT 10
            """,
            (society_id,),
        ).fetchall()
    ]
    archive_entries = [
        dict(row)
        for row in server.db.execute(
            """
            SELECT id, title, content, author_agent_id, status, created_round_id, created_at
            FROM archive_entries
            WHERE society_id = ?
            ORDER BY created_at DESC
            LIMIT 12
            """,
            (society_id,),
        ).fetchall()
    ]
    recent_events = [
        {
            **json.loads(row["content"]),
            "event_type": row["event_type"],
            "visibility": row["visibility"],
            "created_at": row["created_at"],
        }
        for row in server.db.execute(
            """
            SELECT event_type, visibility, content, created_at
            FROM events
            WHERE society_id = ?
            ORDER BY id DESC
            LIMIT 25
            """,
            (society_id,),
        ).fetchall()
    ]
    summaries = [
        json.loads(row["summary"])
        for row in server.db.execute(
            """
            SELECT summary
            FROM round_summaries
            WHERE society_id = ?
            ORDER BY id DESC
            LIMIT 12
            """,
            (society_id,),
        ).fetchall()
    ]

    message_feed = _society_message_feed(society_id)

    return {
        "society": society,
        "agents": agents,
        "enacted_policies": enacted_policies,
        "pending_policies": pending_policies,
        "archive_entries": archive_entries,
        "recent_events": recent_events,
        "summaries": summaries,
        "message_feed": message_feed,
    }


def _has_table(name: str) -> bool:
    row = server.db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _round_agent_activity(round_id: int, round_number: int) -> list[dict[str, Any]]:
    """Build per-society, per-agent activity for a given round."""
    agent_map: dict[str, dict[str, Any]] = {}
    for row in server.db.execute(
        "SELECT id, name, role, society_id, resources FROM agents"
    ).fetchall():
        agent_map[row["id"]] = dict(row)

    thoughts_by_agent: dict[str, dict[str, Any]] = {}
    if _has_table("llm_usage"):
        for row in server.db.execute(
            "SELECT * FROM llm_usage WHERE round_number = ? ORDER BY id ASC",
            (round_number,),
        ).fetchall():
            thoughts_by_agent[row["agent_id"]] = dict(row)

    actions_by_agent: dict[str, list[dict[str, Any]]] = {}
    for row in server.db.execute(
        "SELECT * FROM queued_actions WHERE round_id = ? ORDER BY id ASC",
        (round_id,),
    ).fetchall():
        aid = row["agent_id"]
        action = {
            "action_type": row["action_type"],
            "status": row["status"],
            "payload": json.loads(row["payload"]),
            "result": json.loads(row["result"]) if row["result"] else None,
        }
        actions_by_agent.setdefault(aid, []).append(action)

    messages_by_agent: dict[str, list[dict[str, Any]]] = {}
    for row in server.db.execute(
        """SELECT agent_id, event_type, visibility, content, recipient_agent_id
           FROM events WHERE round_id = ? AND event_type IN ('public_message', 'direct_message')
           ORDER BY id ASC""",
        (round_id,),
    ).fetchall():
        aid = row["agent_id"]
        content = json.loads(row["content"])
        msg = {
            "event_type": row["event_type"],
            "visibility": row["visibility"],
            "message": content.get("message", ""),
            "recipient_name": agent_map.get(row["recipient_agent_id"], {}).get("name")
            if row["recipient_agent_id"]
            else None,
        }
        messages_by_agent.setdefault(aid, []).append(msg)

    all_agent_ids = set(thoughts_by_agent) | set(actions_by_agent) | set(messages_by_agent)
    society_groups: dict[str, list[dict[str, Any]]] = {}
    for aid in all_agent_ids:
        agent_info = agent_map.get(aid, {"name": aid[:8], "role": "unknown", "society_id": "unknown"})
        sid = agent_info.get("society_id", "unknown")
        llm = thoughts_by_agent.get(aid, {})
        entry = {
            "agent_id": aid,
            "agent_name": agent_info.get("name", aid[:8]),
            "role": agent_info.get("role", "unknown"),
            "thoughts": llm.get("thoughts"),
            "model": llm.get("model"),
            "prompt_tokens": llm.get("prompt_tokens", 0),
            "completion_tokens": llm.get("completion_tokens", 0),
            "latency_ms": llm.get("latency_ms", 0),
            "actions": actions_by_agent.get(aid, []),
            "messages": messages_by_agent.get(aid, []),
        }
        society_groups.setdefault(sid, []).append(entry)

    for agents in society_groups.values():
        agents.sort(key=lambda a: a["agent_name"])

    return [
        {"society_id": sid, "agents": agents}
        for sid, agents in sorted(society_groups.items())
    ]


def _round_detail(round_number: int) -> dict[str, Any]:
    round_row = server.db.execute(
        "SELECT * FROM rounds WHERE round_number = ?",
        (round_number,),
    ).fetchone()
    if round_row is None:
        raise KeyError(round_number)

    round_id = round_row["id"]
    summaries = [
        json.loads(row["summary"])
        for row in server.db.execute(
            "SELECT summary FROM round_summaries WHERE round_id = ? ORDER BY id ASC",
            (round_id,),
        ).fetchall()
    ]
    activity = _round_agent_activity(round_id, round_number)

    return {
        "round": dict(round_row),
        "activity": activity,
        "summaries": summaries,
    }


def _agent_detail(agent_id: str) -> dict[str, Any]:
    agent_row = server.db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    if agent_row is None:
        raise KeyError(agent_id)
    agent = dict(agent_row)
    agent.pop("ideology_embedding", None)

    society_row = server.db.execute(
        "SELECT governance_type FROM societies WHERE id = ?", (agent["society_id"],)
    ).fetchone()
    agent["governance_type"] = society_row["governance_type"] if society_row else "unknown"

    rounds_data: list[dict[str, Any]] = []
    round_rows = server.db.execute(
        "SELECT id, round_number, status FROM rounds ORDER BY round_number ASC"
    ).fetchall()

    for rr in round_rows:
        rid, rnum = rr["id"], rr["round_number"]

        thoughts_row = None
        if _has_table("llm_usage"):
            thoughts_row = server.db.execute(
                "SELECT thoughts, model, prompt_tokens, completion_tokens, latency_ms "
                "FROM llm_usage WHERE agent_id = ? AND round_number = ?",
                (agent_id, rnum),
            ).fetchone()

        actions = [
            {
                "action_type": r["action_type"],
                "status": r["status"],
                "payload": json.loads(r["payload"]),
            }
            for r in server.db.execute(
                "SELECT action_type, status, payload FROM queued_actions "
                "WHERE round_id = ? AND agent_id = ? ORDER BY id ASC",
                (rid, agent_id),
            ).fetchall()
        ]

        messages = []
        for r in server.db.execute(
            """SELECT event_type, visibility, content, recipient_agent_id
               FROM events WHERE round_id = ? AND agent_id = ?
               AND event_type IN ('public_message', 'direct_message')
               ORDER BY id ASC""",
            (rid, agent_id),
        ).fetchall():
            content = json.loads(r["content"])
            rec_name = None
            if r["recipient_agent_id"]:
                rec = server.db.execute(
                    "SELECT name FROM agents WHERE id = ?", (r["recipient_agent_id"],)
                ).fetchone()
                rec_name = rec["name"] if rec else r["recipient_agent_id"][:8]
            messages.append({
                "event_type": r["event_type"],
                "message": content.get("message", ""),
                "recipient_name": rec_name,
            })

        if thoughts_row or actions or messages:
            rounds_data.append({
                "round_number": rnum,
                "thoughts": dict(thoughts_row) if thoughts_row else None,
                "actions": actions,
                "messages": messages,
            })

    return {"agent": agent, "rounds": rounds_data}


def _society_message_feed(society_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Chronological message feed for a society."""
    agent_names: dict[str, str] = {}
    for r in server.db.execute("SELECT id, name FROM agents WHERE society_id = ?", (society_id,)):
        agent_names[r["id"]] = r["name"]

    rows = server.db.execute(
        """SELECT e.agent_id, e.event_type, e.visibility, e.content,
                  e.recipient_agent_id, r.round_number
           FROM events e JOIN rounds r ON e.round_id = r.id
           WHERE e.society_id = ? AND e.event_type IN ('public_message', 'direct_message')
           ORDER BY e.id ASC LIMIT ?""",
        (society_id, limit),
    ).fetchall()

    feed: list[dict[str, Any]] = []
    for row in rows:
        content = json.loads(row["content"])
        feed.append({
            "agent_name": agent_names.get(row["agent_id"], row["agent_id"][:8]),
            "agent_id": row["agent_id"],
            "event_type": row["event_type"],
            "message": content.get("message", ""),
            "round_number": row["round_number"],
            "recipient_name": agent_names.get(row["recipient_agent_id"])
            if row["recipient_agent_id"]
            else None,
        })
    return feed


def _admin_state() -> dict[str, Any]:
    current_round = _current_round()
    queue_by_society = [
        dict(row)
        for row in server.db.execute(
            """
            SELECT society_id, COUNT(*) AS queued_count
            FROM queued_actions
            WHERE round_id = ? AND status = 'queued'
            GROUP BY society_id
            ORDER BY society_id ASC
            """,
            (current_round["id"],),
        ).fetchall()
    ]
    recent_rounds = [
        dict(row)
        for row in server.db.execute(
            """
            SELECT round_number, status, started_at, resolved_at
            FROM rounds
            ORDER BY round_number DESC
            LIMIT 10
            """
        ).fetchall()
    ]
    return {
        "current_round": current_round,
        "queue_by_society": queue_by_society,
        "recent_rounds": recent_rounds,
    }


def _all_rounds() -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in server.db.execute(
            "SELECT round_number, status, started_at, resolved_at "
            "FROM rounds ORDER BY round_number DESC"
        ).fetchall()
    ]


def _all_agents() -> list[dict[str, Any]]:
    rows = server.db.execute(
        """SELECT a.id, a.name, a.role, a.resources, a.status, a.society_id,
                  s.governance_type
           FROM agents a JOIN societies s ON a.society_id = s.id
           ORDER BY s.id ASC, a.name ASC"""
    ).fetchall()
    return [dict(r) for r in rows]


async def overview_page(request: Request) -> HTMLResponse:
    _ensure_runtime(request.app.state.db_path)
    context = {
        "request": request,
        "current_round": _current_round(),
        "societies": _society_cards(),
    }
    return templates.TemplateResponse(request, "overview.html", context)


async def society_page(request: Request) -> HTMLResponse:
    _ensure_runtime(request.app.state.db_path)
    society_id = request.path_params["society_id"]
    try:
        detail = _society_detail(society_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown society: {society_id}") from exc
    context = {"request": request, **detail}
    return templates.TemplateResponse(request, "society.html", context)


async def round_page(request: Request) -> HTMLResponse:
    _ensure_runtime(request.app.state.db_path)
    round_number = int(request.path_params["round_number"])
    try:
        detail = _round_detail(round_number)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown round: {round_number}") from exc
    context = {"request": request, **detail}
    return templates.TemplateResponse(request, "round.html", context)


async def agent_page(request: Request) -> HTMLResponse:
    _ensure_runtime(request.app.state.db_path)
    agent_id = request.path_params["agent_id"]
    try:
        detail = _agent_detail(agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown agent: {agent_id}") from exc
    context = {"request": request, **detail}
    return templates.TemplateResponse(request, "agent.html", context)


async def rounds_index(request: Request) -> HTMLResponse:
    _ensure_runtime(request.app.state.db_path)
    context = {"request": request, "rounds": _all_rounds()}
    return templates.TemplateResponse(request, "rounds.html", context)


async def agents_index(request: Request) -> HTMLResponse:
    _ensure_runtime(request.app.state.db_path)
    context = {"request": request, "agents": _all_agents()}
    return templates.TemplateResponse(request, "agents.html", context)


async def admin_page(request: Request) -> HTMLResponse:
    _ensure_runtime(request.app.state.db_path)
    context = {"request": request, **_admin_state()}
    return templates.TemplateResponse(request, "admin.html", context)


async def resolve_round_action(request: Request) -> RedirectResponse:
    _ensure_runtime(request.app.state.db_path)
    report = server.resolve_round()
    return RedirectResponse(f"/rounds/{report['round_number']}", status_code=303)


async def api_societies(request: Request) -> JSONResponse:
    _ensure_runtime(request.app.state.db_path)
    return JSONResponse({"current_round": _current_round(), "societies": _society_cards()})


async def api_society(request: Request) -> JSONResponse:
    _ensure_runtime(request.app.state.db_path)
    society_id = request.path_params["society_id"]
    try:
        detail = _society_detail(society_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown society: {society_id}") from exc
    return JSONResponse(detail)


async def api_round(request: Request) -> JSONResponse:
    _ensure_runtime(request.app.state.db_path)
    round_number = int(request.path_params["round_number"])
    try:
        detail = _round_detail(round_number)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown round: {round_number}") from exc
    return JSONResponse(detail)


async def api_admin_state(request: Request) -> JSONResponse:
    _ensure_runtime(request.app.state.db_path)
    return JSONResponse(_admin_state())


def _metrics_timeseries() -> dict[str, Any]:
    """Return per-society metric time-series for charting."""
    rows = server.db.execute(
        """
        SELECT society_id, summary
        FROM round_summaries
        ORDER BY id ASC
        """
    ).fetchall()

    series: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        sid = row["society_id"]
        s = json.loads(row["summary"])
        m = s.get("metrics", {})
        compass = s.get("ideology_compass", {})
        point = {
            "round": s["round_number"],
            "inequality_gini": m.get("inequality_gini", 0),
            "scarcity_pressure": m.get("scarcity_pressure", 0),
            "governance_engagement": m.get("governance_engagement", 0),
            "communication_openness": m.get("communication_openness", 0),
            "resource_concentration": m.get("resource_concentration", 0),
            "policy_compliance": m.get("policy_compliance", 0),
            "participation_rate": m.get("participation_rate", 0),
            "ideology_x": compass.get("x", 0),
            "ideology_y": compass.get("y", 0),
            "ideology_name": compass.get("ideology_name", ""),
        }
        series.setdefault(sid, []).append(point)

    return {"series": series}


async def api_timeseries(request: Request) -> JSONResponse:
    _ensure_runtime(request.app.state.db_path)
    return JSONResponse(_metrics_timeseries())


async def compare_page(request: Request) -> HTMLResponse:
    _ensure_runtime(request.app.state.db_path)
    context = {
        "request": request,
        "current_round": _current_round(),
        "societies": _society_cards(),
    }
    return templates.TemplateResponse(request, "compare.html", context)


def create_dashboard_app(db_path: str | None = None) -> Starlette:
    server.set_db(init_db(Path(db_path) if db_path else DEFAULT_DB_PATH))
    app = Starlette(
        debug=True,
        routes=[
            Route("/", overview_page),
            Route("/compare", compare_page),
            Route("/rounds", rounds_index),
            Route("/agents", agents_index),
            Route("/societies/{society_id:str}", society_page),
            Route("/agents/{agent_id:str}", agent_page),
            Route("/rounds/{round_number:int}", round_page),
            Route("/admin", admin_page),
            Route("/admin/resolve-round", resolve_round_action, methods=["POST"]),
            Route("/api/societies", api_societies),
            Route("/api/societies/{society_id:str}", api_society),
            Route("/api/rounds/{round_number:int}", api_round),
            Route("/api/admin/state", api_admin_state),
            Route("/api/timeseries", api_timeseries),
        ],
    )
    app.state.db_path = db_path
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


app: Starlette | None = None


def _get_app() -> Starlette:
    global app
    if app is None:
        app = create_dashboard_app()
    return app


def main() -> None:
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Polity replay dashboard")
    parser.add_argument("--db", type=str, default=None, help="Path to simulation database")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    args = parser.parse_args()

    global app
    app = create_dashboard_app(db_path=args.db if args.db else None)

    uvicorn.run(app, host="127.0.0.1", port=args.port, reload=False)
