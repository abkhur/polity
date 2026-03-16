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
        server.db = init_db(Path(db_path) if db_path else DEFAULT_DB_PATH)


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

    return {
        "society": society,
        "agents": agents,
        "enacted_policies": enacted_policies,
        "pending_policies": pending_policies,
        "archive_entries": archive_entries,
        "recent_events": recent_events,
        "summaries": summaries,
    }


def _round_detail(round_number: int) -> dict[str, Any]:
    round_row = server.db.execute(
        "SELECT * FROM rounds WHERE round_number = ?",
        (round_number,),
    ).fetchone()
    if round_row is None:
        raise KeyError(round_number)

    round_id = round_row["id"]
    queued_actions = [
        {
            **dict(row),
            "payload_obj": json.loads(row["payload"]),
            "result_obj": json.loads(row["result"]) if row["result"] else None,
        }
        for row in server.db.execute(
            """
            SELECT *
            FROM queued_actions
            WHERE round_id = ?
            ORDER BY id ASC
            """,
            (round_id,),
        ).fetchall()
    ]
    events = [
        {
            **json.loads(row["content"]),
            "event_type": row["event_type"],
            "visibility": row["visibility"],
            "created_at": row["created_at"],
            "society_id": row["society_id"],
            "agent_id": row["agent_id"],
            "recipient_agent_id": row["recipient_agent_id"],
        }
        for row in server.db.execute(
            """
            SELECT *
            FROM events
            WHERE round_id = ?
            ORDER BY id ASC
            """,
            (round_id,),
        ).fetchall()
    ]
    summaries = [
        json.loads(row["summary"])
        for row in server.db.execute(
            """
            SELECT summary
            FROM round_summaries
            WHERE round_id = ?
            ORDER BY id ASC
            """,
            (round_id,),
        ).fetchall()
    ]

    return {
        "round": dict(round_row),
        "queued_actions": queued_actions,
        "events": events,
        "summaries": summaries,
    }


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


def create_dashboard_app(db_path: str | None = None) -> Starlette:
    server.db = init_db(Path(db_path) if db_path else DEFAULT_DB_PATH)
    app = Starlette(
        debug=True,
        routes=[
            Route("/", overview_page),
            Route("/societies/{society_id:str}", society_page),
            Route("/rounds/{round_number:int}", round_page),
            Route("/admin", admin_page),
            Route("/admin/resolve-round", resolve_round_action, methods=["POST"]),
            Route("/api/societies", api_societies),
            Route("/api/societies/{society_id:str}", api_society),
            Route("/api/rounds/{round_number:int}", api_round),
            Route("/api/admin/state", api_admin_state),
        ],
    )
    app.state.db_path = db_path
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


app = create_dashboard_app()


def main() -> None:
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Polity replay dashboard")
    parser.add_argument("--db", type=str, default=None, help="Path to simulation database")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    args = parser.parse_args()

    if args.db:
        global app
        app = create_dashboard_app(db_path=args.db)

    uvicorn.run(app, host="127.0.0.1", port=args.port, reload=False)
