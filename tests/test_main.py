"""Tests for the top-level ``python -m src`` command launcher."""

import sys

from src import __main__ as cli
from src import runner


def test_help_lists_available_commands(capsys) -> None:
    code = cli.main(["--help"])

    captured = capsys.readouterr()
    assert code == 0
    assert "python -m src" in captured.out
    assert "run" in captured.out
    assert "batch" in captured.out
    assert "dashboard" in captured.out
    assert "server" in captured.out


def test_run_subcommand_dispatches_with_script_name(monkeypatch) -> None:
    seen: dict[str, list[str]] = {}

    def fake_main() -> None:
        seen["argv"] = sys.argv[:]

    monkeypatch.setattr(runner, "main", fake_main)

    code = cli.main(["run", "--agents", "2", "--rounds", "1"])

    assert code == 0
    assert seen["argv"] == ["polity-run", "--agents", "2", "--rounds", "1"]
