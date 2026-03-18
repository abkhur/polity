"""Tests for the batch runner."""

import json

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
