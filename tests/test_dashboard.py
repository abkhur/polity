"""Tests for dashboard API surfaces and page rendering."""

import asyncio
import json

from starlette.requests import Request

from src import server
from src.dashboard import (
    api_round,
    api_society,
    api_timeseries,
    admin_page,
    create_dashboard_app,
    overview_page,
    round_page,
    society_page,
)
from src.runner import SimulationConfig, run_simulation


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


class TestDashboardPages:
    def test_dashboard_pages_and_api(self, tmp_path):
        app = create_dashboard_app(str(tmp_path / "dashboard.db"))

        alice = server.join_society("Alice", consent=True, governance_type="democracy")
        bob = server.join_society("Bob", consent=True, governance_type="democracy")

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
        assert "run_metadata" in body
        assert len(body["activity"]) >= 1
        assert any(summary["society_id"] == "democracy_1" for summary in body["summaries"])

        admin = asyncio.run(admin_page(_request(app, "/admin")))
        assert admin.status_code == 200
        assert "Operator Console" in admin.body.decode("utf-8")


class TestDashboardApi:
    def test_timeseries_exposes_run_metadata_and_new_metrics(self, tmp_path) -> None:
        db_path = str(tmp_path / "dashboard.db")
        run_simulation(
            SimulationConfig(
                agents_per_society=2,
                num_rounds=2,
                seed=42,
                db_path=db_path,
            )
        )

        app = create_dashboard_app(db_path)
        response = asyncio.run(api_timeseries(_request(app, "/api/timeseries")))
        assert response.status_code == 200

        data = json.loads(response.body.decode("utf-8"))
        assert data["run_metadata"]["seed"] == 42
        point = data["series"]["democracy_1"][-1]
        assert "common_pool_depletion" in point
        assert "governance_participation_rate" in point
        assert "public_message_share" in point
        assert "top_agent_resource_share" in point
        assert "policy_block_rate" in point
        assert "governance_engagement" in point

    def test_society_and_round_api_include_run_metadata(self, tmp_path) -> None:
        db_path = str(tmp_path / "dashboard_detail.db")
        run_simulation(
            SimulationConfig(
                agents_per_society=2,
                num_rounds=1,
                seed=99,
                db_path=db_path,
            )
        )

        app = create_dashboard_app(db_path)

        society_response = asyncio.run(
            api_society(_request(app, "/api/societies/democracy_1", {"society_id": "democracy_1"}))
        )
        assert society_response.status_code == 200
        assert json.loads(society_response.body.decode("utf-8"))["run_metadata"]["seed"] == 99

        round_response = asyncio.run(
            api_round(_request(app, "/api/rounds/1", {"round_number": 1}))
        )
        assert round_response.status_code == 200
        assert json.loads(round_response.body.decode("utf-8"))["run_metadata"]["seed"] == 99
