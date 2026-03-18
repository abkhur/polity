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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .runner import SimulationConfig, run_simulation

logger = logging.getLogger("polity.batch")

_SEPARATOR = "═" * 64


@dataclass
class BatchConfig:
    num_runs: int = 10
    agents_per_society: int = 4
    num_rounds: int = 10
    base_seed: int = 1000
    output_dir: str = "runs/batch"
    equal_start: bool = False
    override_starting_resources: int | None = None
    override_total_resources: int | None = None


def run_batch(config: BatchConfig) -> dict[str, Any]:
    """Run N simulations and aggregate final-round metrics per society."""
    output_path = Path(config.output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    all_results: list[dict[str, Any]] = []
    per_society_metrics: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )

    metric_keys = [
        "inequality_gini",
        "scarcity_pressure",
        "governance_engagement",
        "communication_openness",
        "resource_concentration",
        "policy_compliance",
        "participation_rate",
    ]

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
        )

        logger.info("Run %d/%d (seed=%d)", i + 1, config.num_runs, seed)
        result = run_simulation(sim_config)
        all_results.append(result)

        for summary in result.get("final_summaries", []):
            sid = summary["society_id"]
            metrics = summary.get("metrics", {})
            for key in metric_keys:
                if key in metrics:
                    per_society_metrics[sid][key].append(metrics[key])

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

    report = {
        "config": {
            "num_runs": config.num_runs,
            "agents_per_society": config.agents_per_society,
            "num_rounds": config.num_rounds,
            "base_seed": config.base_seed,
            "equal_start": config.equal_start,
        },
        "aggregated": aggregated,
        "run_count": len(all_results),
    }

    report_path = output_path / "batch_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    _print_report(report)
    print(f"\n  Report saved to: {report_path}")

    return report


def _print_report(report: dict[str, Any]) -> None:
    print(f"\n{_SEPARATOR}")
    print("  POLITY BATCH RESULTS")
    print(_SEPARATOR)
    cfg = report["config"]
    print(f"  Runs: {cfg['num_runs']}  |  Rounds: {cfg['num_rounds']}  |  "
          f"Agents/society: {cfg['agents_per_society']}  |  Equal start: {cfg['equal_start']}")
    print(_SEPARATOR)

    for sid, metrics in report["aggregated"].items():
        print(f"\n  {sid}")
        for key, stats in metrics.items():
            print(f"    {key:<28s} {stats['mean']:>8.4f} ± {stats['std']:.4f}  "
                  f"[{stats['min']:.4f}, {stats['max']:.4f}]  n={stats['n']}")

    print(f"\n{_SEPARATOR}")


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
        "--output", type=str, default="runs/batch",
        help="Output directory for databases and report (default: runs/batch)",
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
    )
    run_batch(config)


if __name__ == "__main__":
    main()
