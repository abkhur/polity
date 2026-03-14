"""Tests for the minimal Polity dashboard."""

import asyncio
import json
from unittest.mock import patch

from starlette.requests import Request

from src import server
from src.dashboard import (
    api_round,
    admin_page,
    create_dashboard_app,
    overview_page,
    round_page,
    society_page,
)


async def _empty_receive():
    return {"type": "http.request", "body": b"", "more_body": False}


def _request(app, path: str, path_params: dict | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "app": app,
            "path_params": path_params or {},
        },
        _empty_receive,
    )


def test_dashboard_pages_and_api(tmp_path):
    app = create_dashboard_app(str(tmp_path / "dashboard.db"))

    with patch.object(server.random, "choice", return_value="democracy"):
        alice = server.join_society("Alice", consent=True)
        bob = server.join_society("Bob", consent=True)

    server.submit_actions(
        alice["agent_id"],
        [
            {"type": "post_public_message", "message": "Hello polity."},
            {"type": "propose_policy", "title": "Open Records", "description": "Keep archives readable."},
        ],
    )
    server.submit_actions(
        bob["agent_id"],
        [{"type": "gather_resources", "amount": 20}],
    )
    server.resolve_round()

    overview = asyncio.run(overview_page(_request(app, "/")))
    assert overview.status_code == 200
    assert "Polity" in overview.body.decode("utf-8")
    assert "democracy_1" in overview.body.decode("utf-8")

    society = asyncio.run(
        society_page(_request(app, "/societies/democracy_1", {"society_id": "democracy_1"}))
    )
    assert society.status_code == 200
    assert "Open Records" in society.body.decode("utf-8")

    round_response = asyncio.run(round_page(_request(app, "/rounds/1", {"round_number": 1})))
    assert round_response.status_code == 200
    assert "Hello polity." in round_response.body.decode("utf-8")

    api_response = asyncio.run(api_round(_request(app, "/api/rounds/1", {"round_number": 1})))
    assert api_response.status_code == 200
    body = json.loads(api_response.body.decode("utf-8"))
    assert body["round"]["round_number"] == 1
    assert any(event["event_type"] == "public_message" for event in body["events"])

    admin = asyncio.run(admin_page(_request(app, "/admin")))
    assert admin.status_code == 200
    assert "Operator Console" in admin.body.decode("utf-8")
