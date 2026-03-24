"""Tests for the batch runner."""

import json
from unittest.mock import patch

import pytest

from src.batch import BatchConfig, run_batch


class TestBatchRunner:
    def test_batch_produces_report(self, tmp_path):
        config = BatchConfig(
            num_runs=3,
            agents_per_society=2,
            num_rounds=3,
            base_seed=42,
            output_dir=str(tmp_path / "batch"),
        )
        report = run_batch(config)

        assert report["run_count"] == 3
        assert "aggregated" in report
        assert "democracy_1" in report["aggregated"]
        assert "oligarchy_1" in report["aggregated"]
        assert "blank_slate_1" in report["aggregated"]

    def test_batch_report_has_stats(self, tmp_path):
        config = BatchConfig(
            num_runs=3,
            agents_per_society=2,
            num_rounds=3,
            base_seed=100,
            output_dir=str(tmp_path / "batch2"),
        )
        report = run_batch(config)

        for sid in ["democracy_1", "oligarchy_1", "blank_slate_1"]:
            metrics = report["aggregated"][sid]
            assert "inequality_gini" in metrics
            stats = metrics["inequality_gini"]
            assert "mean" in stats
            assert "std" in stats
            assert stats["n"] == 3

    def test_batch_saves_report_file(self, tmp_path):
        config = BatchConfig(
            num_runs=2,
            agents_per_society=2,
            num_rounds=2,
            base_seed=200,
            output_dir=str(tmp_path / "batch3"),
        )
        run_batch(config)

        report_path = tmp_path / "batch3" / "batch_report.json"
        assert report_path.exists()
        data = json.loads(report_path.read_text())
        assert data["run_count"] == 2

    def test_batch_equal_start(self, tmp_path):
        config = BatchConfig(
            num_runs=2,
            agents_per_society=2,
            num_rounds=2,
            base_seed=300,
            output_dir=str(tmp_path / "batch4"),
            equal_start=True,
            override_starting_resources=100,
            override_total_resources=10000,
        )
        report = run_batch(config)
        assert report["config"]["equal_start"] is True
        assert report["run_count"] == 2

    def test_batch_records_run_metadata_and_llm_config(self, tmp_path):
        from src.strategies import llm as llm_module

        config = BatchConfig(
            num_runs=1,
            agents_per_society=2,
            num_rounds=1,
            base_seed=123,
            output_dir=str(tmp_path / "batch_llm"),
            strategy="llm",
            model="gpt-4o-mini",
            api_key_env="TEST_KEY",
            base_url="http://localhost:8000/v1/",
            completion=True,
            token_budget=4096,
            temperature=0.1,
            neutral_labels=True,
        )
        mock_usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        mock_response = '{"thoughts":"test","actions":[]}'
        with patch.dict(
            llm_module._PROVIDERS,
            {"openai_completion": lambda *_args, **_kwargs: (mock_response, mock_usage)},
        ):
            report = run_batch(config)

        assert report["config"]["strategy"] == "llm"
        assert report["config"]["completion"] is True
        assert report["runs"][0]["run_metadata"]["provider"] == "openai_completion"
        assert report["runs"][0]["run_metadata"]["base_url"] == "http://localhost:8000/v1"
