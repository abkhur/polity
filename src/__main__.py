"""Top-level command launcher for ``python -m src``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence


CommandMain = Callable[[], None]

COMMANDS: dict[str, tuple[str, str]] = {
    "run": ("polity-run", "Run a headless simulation."),
    "batch": ("polity-batch", "Run repeated simulations and aggregate results."),
    "dashboard": ("polity-dashboard", "Serve the replay dashboard for a run database."),
    "server": ("polity-server", "Run the Polity MCP server."),
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src",
        description="Polity command launcher. Installed scripts are usually the clearest interface.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m src run --agents 4 --rounds 10\n"
            "  python -m src batch --runs 5 --agents 3\n"
            "  python -m src dashboard --db important_runs/run_004_qwen25_72b_base.db\n"
            "  python -m src server"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", metavar="command")
    for name, (_, help_text) in COMMANDS.items():
        subparser = subparsers.add_parser(
            name,
            help=help_text,
            description=help_text,
            add_help=False,
        )
        subparser.add_argument("args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    return parser


def _load_command(command: str) -> tuple[str, CommandMain]:
    script_name, _ = COMMANDS[command]
    if command == "run":
        from .runner import main as target
    elif command == "batch":
        from .batch import main as target
    elif command == "dashboard":
        from .dashboard import main as target
    else:
        try:
            from .server import main as target
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on local install
            if exc.name == "mcp":
                raise RuntimeError(
                    "The `server` command requires the `mcp` package. "
                    "Install the project dependencies or use the installed script entry points."
                ) from exc
            raise
    return script_name, target


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()

    if not args or args[0] in {"-h", "--help"}:
        parser.print_help()
        return 0

    if args[0] == "help":
        if len(args) == 1:
            parser.print_help()
            return 0
        args = [args[1], "--help", *args[2:]]

    command = args[0]
    if command not in COMMANDS:
        parser.print_usage(sys.stderr)
        print(f"python -m src: error: unknown command '{command}'", file=sys.stderr)
        return 2

    script_name, target = _load_command(command)
    old_argv = sys.argv[:]
    try:
        sys.argv = [script_name, *args[1:]]
        target()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1
    finally:
        sys.argv = old_argv
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
