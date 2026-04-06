"""Batch runner for repeated Polity simulations with statistical aggregation.

Runs N simulations with the same configuration (varying only the seed)
and reports mean/std of all per-society metrics across runs.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .db import default_batch_dir
from .runner import SimulationConfig, run_simulation
from .state import DEFAULT_PROMPT_SURFACE_MODE, PROMPT_SURFACE_MODES
from .terminal_ui import glyphs as terminal_glyphs

logger = logging.getLogger("polity.batch")


@dataclass
class BatchConfig:
    num_runs: int = 10
    agents_per_society: int = 4
    num_rounds: int = 10
    base_seed: int = 1000
    output_dir: str = field(default_factory=lambda: str(default_batch_dir()))
    equal_start: bool = False
    override_starting_resources: int | None = None
    override_total_resources: int | None = None
    strategy: str = "heuristic"
    model: str = "gpt-4o"
    api_key_env: str = "OPENAI_API_KEY"
    base_url: str | None = None
    completion: bool = False
    token_budget: int = 8000
    temperature: float = 0.7
    neutral_labels: bool = False
    prompt_surface_mode: str = DEFAULT_PROMPT_SURFACE_MODE


def run_batch(config: BatchConfig) -> dict[str, Any]:
    """Run N simulations and aggregate final-round metrics per society."""
    output_path = Path(config.output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    all_results: list[dict[str, Any]] = []
    run_records: list[dict[str, Any]] = []
    per_society_metrics: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    validity_metrics: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    validity_warning_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for i in range(config.num_runs):
        seed = config.base_seed + i
        db_path = str(output_path / f"run_{i:03d}_seed{seed}.db")

        sim_config = SimulationConfig(
            agents_per_society=config.agents_per_society,
            num_rounds=config.num_rounds,
            seed=seed,
            db_path=db_path,
            equal_start=config.equal_start,
            override_starting_resources=config.override_starting_resources,
            override_total_resources=config.override_total_resources,
            strategy=config.strategy,
            model=config.model,
            api_key_env=config.api_key_env,
            base_url=config.base_url,
            completion=config.completion,
            token_budget=config.token_budget,
            temperature=config.temperature,
            neutral_labels=config.neutral_labels,
            prompt_surface_mode=config.prompt_surface_mode,
        )

        logger.info("Run %d/%d (seed=%d)", i + 1, config.num_runs, seed)
        result = run_simulation(sim_config)
        all_results.append(result)
        run_records.append(
            {
                "seed": seed,
                "db_path": result.get("db_path"),
                "run_metadata": result.get("run_metadata"),
                "run_validity": result.get("run_validity"),
            }
        )

        for summary in result.get("final_summaries", []):
            sid = summary["society_id"]
            metrics = summary.get("metrics", {})
            for key, value in metrics.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    per_society_metrics[sid][key].append(float(value))

        for sid, validity in (result.get("run_validity", {}).get("societies", {}) or {}).items():
            for key, value in validity.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    validity_metrics[sid][key].append(float(value))
            for flag in validity.get("warning_flags", []):
                validity_warning_counts[sid][flag] += 1

    aggregated: dict[str, dict[str, dict[str, float]]] = {}
    for sid, metrics in per_society_metrics.items():
        aggregated[sid] = {}
        for key, values in metrics.items():
            aggregated[sid][key] = {
                "mean": round(statistics.mean(values), 4) if values else 0,
                "std": round(statistics.stdev(values), 4) if len(values) > 1 else 0,
                "min": round(min(values), 4) if values else 0,
                "max": round(max(values), 4) if values else 0,
                "n": len(values),
            }

    validity_aggregated: dict[str, dict[str, dict[str, float]]] = {}
    for sid, metrics in validity_metrics.items():
        validity_aggregated[sid] = {}
        for key, values in metrics.items():
            validity_aggregated[sid][key] = {
                "mean": round(statistics.mean(values), 4) if values else 0,
                "std": round(statistics.stdev(values), 4) if len(values) > 1 else 0,
                "min": round(min(values), 4) if values else 0,
                "max": round(max(values), 4) if values else 0,
                "n": len(values),
            }

    report = {
        "config": {
            "num_runs": config.num_runs,
            "agents_per_society": config.agents_per_society,
            "num_rounds": config.num_rounds,
            "base_seed": config.base_seed,
            "equal_start": config.equal_start,
            "override_starting_resources": config.override_starting_resources,
            "override_total_resources": config.override_total_resources,
            "strategy": config.strategy,
            "model": config.model,
            "api_key_env": config.api_key_env,
            "base_url": config.base_url,
            "completion": config.completion,
            "token_budget": config.token_budget,
            "temperature": config.temperature,
            "neutral_labels": config.neutral_labels,
            "prompt_surface_mode": config.prompt_surface_mode,
        },
        "aggregated": aggregated,
        "validity_aggregated": validity_aggregated,
        "validity_warning_counts": {
            sid: dict(sorted(flags.items()))
            for sid, flags in validity_warning_counts.items()
        },
        "runs": run_records,
        "run_count": len(all_results),
    }

    report_path = output_path / "batch_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    _print_report(report)
    print(f"\n  Report saved to: {report_path}")

    return report


def _print_report(report: dict[str, Any]) -> None:
    ui = terminal_glyphs()
    separator = ui["separator"]
    print(f"\n{separator}")
    print("  POLITY BATCH RESULTS")
    print(separator)
    cfg = report["config"]
    header = (
        f"  Runs: {cfg['num_runs']}  |  Rounds: {cfg['num_rounds']}  |  "
        f"Agents/society: {cfg['agents_per_society']}  |  Equal start: {cfg['equal_start']}  |  "
        f"Strategy: {cfg['strategy']}"
    )
    if cfg["strategy"] == "llm":
        header += f"  |  Model: {cfg['model']}"
        header += f"  |  Prompt: {cfg['prompt_surface_mode']}"
    print(header)
    print(separator)

    for sid, metrics in report["aggregated"].items():
        print(f"\n  {sid}")
        for key, stats in metrics.items():
            print(f"    {key:<28s} {stats['mean']:>8.4f} {ui['plus_minus']} {stats['std']:.4f}  "
                  f"[{stats['min']:.4f}, {stats['max']:.4f}]  n={stats['n']}")

    print(f"\n{separator}")


def main() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Run repeated Polity simulations and aggregate results."
    )
    parser.add_argument(
        "--runs", type=int, default=10,
        help="Number of simulations to run (default: 10)",
    )
    parser.add_argument(
        "--agents", type=int, default=4,
        help="Agents per society (default: 4)",
    )
    parser.add_argument(
        "--rounds", type=int, default=10,
        help="Rounds per simulation (default: 10)",
    )
    parser.add_argument(
        "--base-seed", type=int, default=1000,
        help="Starting seed (incremented for each run, default: 1000)",
    )
    parser.add_argument(
        "--output", type=str, default=str(default_batch_dir()),
        help=f"Output directory for databases and report (default: {default_batch_dir()})",
    )
    parser.add_argument(
        "--equal-start", action="store_true",
        help="Give all agents equal starting resources",
    )
    parser.add_argument(
        "--start-resources", type=int, default=None,
        help="Override starting resources per agent",
    )
    parser.add_argument(
        "--total-resources", type=int, default=None,
        help="Override total resources per society",
    )
    parser.add_argument(
        "--strategy", type=str, default="heuristic",
        choices=["heuristic", "llm"],
        help="Agent strategy: heuristic or llm (default: heuristic)",
    )
    parser.add_argument(
        "--model", type=str, default="gpt-4o",
        help="LLM model name (default: gpt-4o)",
    )
    parser.add_argument(
        "--api-key-env", type=str, default="OPENAI_API_KEY",
        help="Environment variable for the model API key (default: OPENAI_API_KEY)",
    )
    parser.add_argument(
        "--base-url", type=str, default=None,
        help="Custom OpenAI-compatible API base URL",
    )
    parser.add_argument(
        "--completion", action="store_true",
        help="Use completion mode for OpenAI-compatible base models",
    )
    parser.add_argument(
        "--token-budget", type=int, default=8000,
        help="Token budget per agent per round (default: 8000)",
    )
    parser.add_argument(
        "--temperature", type=float, default=0.7,
        help="Sampling temperature for LLM runs (default: 0.7)",
    )
    parser.add_argument(
        "--neutral-labels", action="store_true",
        help="Replace role/society names with neutral identifiers in LLM prompts",
    )
    parser.add_argument(
        "--prompt-surface-mode",
        type=str,
        choices=list(PROMPT_SURFACE_MODES),
        default=DEFAULT_PROMPT_SURFACE_MODE,
        help="Prompt surface for policy proposal affordances (default: free_text_only)",
    )
    args = parser.parse_args()

    config = BatchConfig(
        num_runs=args.runs,
        agents_per_society=args.agents,
        num_rounds=args.rounds,
        base_seed=args.base_seed,
        output_dir=args.output,
        equal_start=args.equal_start or args.start_resources is not None,
        override_starting_resources=args.start_resources,
        override_total_resources=args.total_resources,
        strategy=args.strategy,
        model=args.model,
        api_key_env=args.api_key_env,
        base_url=args.base_url,
        completion=args.completion,
        token_budget=args.token_budget,
        temperature=args.temperature,
        neutral_labels=args.neutral_labels,
        prompt_surface_mode=args.prompt_surface_mode,
    )
    run_batch(config)


if __name__ == "__main__":
    main()
