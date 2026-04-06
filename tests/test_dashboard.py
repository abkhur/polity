"""Tests for dashboard API surfaces and page rendering."""

import asyncio
import json

from starlette.requests import Request

from src import server
from src.dashboard import (
    STATIC_DIR,
    TEMPLATES_DIR,
    api_round,
    api_research_runs,
    api_society,
    api_timeseries,
    admin_page,
    create_dashboard_app,
    overview_page,
    research_page,
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
    def test_packaged_asset_directories_exist(self) -> None:
        assert TEMPLATES_DIR.exists()
        assert STATIC_DIR is not None
        assert STATIC_DIR.exists()

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
        assert "run_validity" in body
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
        assert "run_validity" in data
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
        society_data = json.loads(society_response.body.decode("utf-8"))
        assert society_data["run_metadata"]["seed"] == 99
        assert "run_validity" in society_data

        round_response = asyncio.run(
            api_round(_request(app, "/api/rounds/1", {"round_number": 1}))
        )
        assert round_response.status_code == 200
        round_data = json.loads(round_response.body.decode("utf-8"))
        assert round_data["run_metadata"]["seed"] == 99
        assert "run_validity" in round_data

    def test_research_run_inventory_page_and_api(self, tmp_path) -> None:
        first_db = str(tmp_path / "run_a.db")
        second_db = str(tmp_path / "run_b.db")
        run_simulation(
            SimulationConfig(
                agents_per_society=2,
                num_rounds=2,
                seed=11,
                db_path=first_db,
            )
        )
        run_simulation(
            SimulationConfig(
                agents_per_society=2,
                num_rounds=2,
                seed=12,
                db_path=second_db,
            )
        )

        app = create_dashboard_app(first_db, research_dir=str(tmp_path))

        page = asyncio.run(research_page(_request(app, "/research")))
        assert page.status_code == 200
        body = page.body.decode("utf-8")
        assert "Research Atlas" in body
        assert "run_a.db" in body
        assert "run_b.db" in body

        response = asyncio.run(api_research_runs(_request(app, "/api/research/runs")))
        assert response.status_code == 200
        data = json.loads(response.body.decode("utf-8"))
        assert len(data["runs"]) == 2
        assert {run["filename"] for run in data["runs"]} == {"run_a.db", "run_b.db"}
        assert "democracy_1" in data["runs"][0]["societies"]
        assert "run_validity" in data["runs"][0]
