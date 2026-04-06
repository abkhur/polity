"""Run-validity computation and persistence helpers."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .state import DESTITUTE_ACTION_BUDGET, ROLE_ACTION_BUDGET

RUN_VALIDITY_TABLE = """
CREATE TABLE IF NOT EXISTS run_validity (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    summary TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _table_exists(db: sqlite3.Connection, table_name: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(db: sqlite3.Connection, table_name: str) -> set[str]:
    if not _table_exists(db, table_name):
        return set()
    return {
        row["name"]
        for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def _loads(value: str | None) -> dict[str, Any]:
    return json.loads(value) if value else {}


def ensure_run_validity_table(db: sqlite3.Connection) -> None:
    db.executescript(RUN_VALIDITY_TABLE)


def _resolved_round_count(db: sqlite3.Connection) -> int:
    if not _table_exists(db, "rounds"):
        return 0
    row = db.execute(
        "SELECT MAX(round_number) AS max_round FROM rounds WHERE status = 'resolved'"
    ).fetchone()
    return int(row["max_round"] or 0) if row else 0


def _role_counts_by_society(db: sqlite3.Connection) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    if not _table_exists(db, "societies"):
        return counts

    for row in db.execute(
        "SELECT id, governance_type FROM societies ORDER BY id ASC"
    ).fetchall():
        counts[row["id"]] = {
            "governance_type": row["governance_type"],
            "roles": {},
        }

    if not _table_exists(db, "agents"):
        return counts

    for row in db.execute(
        """
        SELECT society_id, role, COUNT(*) AS count
        FROM agents
        GROUP BY society_id, role
        ORDER BY society_id ASC, role ASC
        """
    ).fetchall():
        society = counts.setdefault(
            row["society_id"],
            {"governance_type": None, "roles": {}},
        )
        society["roles"][row["role"]] = int(row["count"] or 0)

    return counts


def _enacted_policy_counts(db: sqlite3.Connection) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    if not _table_exists(db, "policies"):
        return counts

    columns = _table_columns(db, "policies")
    has_compiled = "compiled_clauses" in columns
    select_columns = ["society_id", "status", "policy_type"]
    if has_compiled:
        select_columns.append("compiled_clauses")
    query = (
        f"SELECT {', '.join(select_columns)} "
        "FROM policies WHERE status = 'enacted'"
    )
    for row in db.execute(query).fetchall():
        society = counts.setdefault(
            row["society_id"],
            {"enacted": 0, "mechanical": 0, "compiled": 0, "symbolic": 0},
        )
        society["enacted"] += 1
        if row["policy_type"]:
            society["mechanical"] += 1
        elif has_compiled and row["compiled_clauses"] not in (None, "", "[]"):
            society["compiled"] += 1
        else:
            society["symbolic"] += 1
    return counts


def _policy_vote_totals(db: sqlite3.Connection) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = {}
    if not _table_exists(db, "events"):
        return totals

    for row in db.execute(
        """
        SELECT society_id, content
        FROM events
        WHERE event_type = 'policy_resolved'
        ORDER BY id ASC
        """
    ).fetchall():
        content = _loads(row["content"])
        society = totals.setdefault(
            row["society_id"],
            {
                "resolved": 0,
                "eligible": 0,
                "support": 0,
                "oppose": 0,
                "abstain": 0,
                "single_support_enacted": 0,
            },
        )
        support = int(content.get("support", 0) or 0)
        oppose = int(content.get("oppose", 0) or 0)
        eligible = int(content.get("total_eligible", 0) or 0)
        society["resolved"] += 1
        society["eligible"] += eligible
        society["support"] += support
        society["oppose"] += oppose
        society["abstain"] += max(eligible - support - oppose, 0)
        if (
            content.get("status") == "enacted"
            and support == 1
            and oppose == 0
        ):
            society["single_support_enacted"] += 1
    return totals


def _final_metrics_by_society(db: sqlite3.Connection) -> dict[str, dict[str, float]]:
    metrics: dict[str, dict[str, float]] = {}
    if not _table_exists(db, "round_summaries"):
        return metrics

    society_ids = [
        row["id"]
        for row in db.execute("SELECT id FROM societies ORDER BY id ASC").fetchall()
    ]
    for society_id in society_ids:
        row = db.execute(
            """
            SELECT summary
            FROM round_summaries
            WHERE society_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (society_id,),
        ).fetchone()
        if row is None:
            continue
        summary = _loads(row["summary"])
        metrics[society_id] = {
            "top_agent_resource_share": round(
                float(summary.get("metrics", {}).get("top_agent_resource_share", 0.0) or 0.0),
                4,
            ),
            "top_third_resource_share": round(
                float(summary.get("metrics", {}).get("top_third_resource_share", 0.0) or 0.0),
                4,
            ),
        }
    return metrics


def _action_budget_utilization(db: sqlite3.Connection) -> tuple[dict[str, float], str]:
    if _table_exists(db, "turn_budgets"):
        budget_totals: dict[str, int] = {}
        for row in db.execute(
            """
            SELECT society_id, COALESCE(SUM(action_budget), 0) AS total_budget
            FROM turn_budgets
            GROUP BY society_id
            ORDER BY society_id ASC
            """
        ).fetchall():
            budget_totals[row["society_id"]] = int(row["total_budget"] or 0)
        if budget_totals:
            action_totals = {
                row["society_id"]: int(row["action_count"] or 0)
                for row in db.execute(
                    """
                    SELECT society_id, COUNT(*) AS action_count
                    FROM queued_actions
                    GROUP BY society_id
                    ORDER BY society_id ASC
                    """
                ).fetchall()
            }
            utilization = {
                society_id: round(
                    action_totals.get(society_id, 0) / max(total_budget, 1),
                    4,
                )
                for society_id, total_budget in budget_totals.items()
            }
            return utilization, "turn_budgets"

    rounds = _resolved_round_count(db)
    if rounds <= 0 or not _table_exists(db, "agents"):
        return {}, "unavailable"

    estimated_budget: dict[str, int] = {}
    for row in db.execute(
        """
        SELECT society_id, role, resources, COUNT(*) AS count
        FROM agents
        WHERE status = 'active'
        GROUP BY society_id, role, resources
        ORDER BY society_id ASC
        """
    ).fetchall():
        action_budget = (
            DESTITUTE_ACTION_BUDGET
            if int(row["resources"] or 0) <= 0
            else ROLE_ACTION_BUDGET.get(row["role"], 2)
        )
        estimated_budget[row["society_id"]] = estimated_budget.get(row["society_id"], 0) + (
            action_budget * int(row["count"] or 0) * rounds
        )

    action_totals = {
        row["society_id"]: int(row["action_count"] or 0)
        for row in db.execute(
            """
            SELECT society_id, COUNT(*) AS action_count
            FROM queued_actions
            GROUP BY society_id
            ORDER BY society_id ASC
            """
        ).fetchall()
    }
    utilization = {
        society_id: round(
            action_totals.get(society_id, 0) / max(total_budget, 1),
            4,
        )
        for society_id, total_budget in estimated_budget.items()
    }
    return utilization, "estimated"


def compute_run_validity(db: sqlite3.Connection) -> dict[str, Any]:
    role_counts = _role_counts_by_society(db)
    enacted_counts = _enacted_policy_counts(db)
    vote_totals = _policy_vote_totals(db)
    final_metrics = _final_metrics_by_society(db)
    utilization, utilization_source = _action_budget_utilization(db)

    societies: dict[str, dict[str, Any]] = {}
    all_warning_flags: list[str] = []
    for society_id, role_info in role_counts.items():
        governance_type = role_info.get("governance_type")
        initial_role_counts = dict(sorted(role_info.get("roles", {}).items()))
        mixed_role_present = sum(1 for count in initial_role_counts.values() if count > 0) >= 2

        enacted = enacted_counts.get(society_id, {})
        vote = vote_totals.get(society_id, {})
        metrics = final_metrics.get(society_id, {})
        enacted_total = int(enacted.get("enacted", 0))
        mechanical_total = int(enacted.get("mechanical", 0))
        compiled_total = int(enacted.get("compiled", 0))
        eligible_total = int(vote.get("eligible", 0))
        support_total = int(vote.get("support", 0))
        oppose_total = int(vote.get("oppose", 0))
        abstain_total = int(vote.get("abstain", 0))
        single_support_enacted = int(vote.get("single_support_enacted", 0))

        society_summary = {
            "society_id": society_id,
            "governance_type": governance_type,
            "initial_role_counts": initial_role_counts,
            "mixed_role_present": mixed_role_present,
            "mechanical_enactment_rate": round(
                mechanical_total / max(enacted_total, 1),
                4,
            ) if enacted_total else 0.0,
            "compiled_enactment_rate": round(
                compiled_total / max(enacted_total, 1),
                4,
            ) if enacted_total else 0.0,
            "opposition_rate": round(
                oppose_total / max(eligible_total, 1),
                4,
            ) if eligible_total else 0.0,
            "abstention_rate": round(
                abstain_total / max(eligible_total, 1),
                4,
            ) if eligible_total else 0.0,
            "single_support_enactment_rate": round(
                single_support_enacted / max(enacted_total, 1),
                4,
            ) if enacted_total else 0.0,
            "mean_action_budget_utilization": round(
                float(utilization.get(society_id, 0.0) or 0.0),
                4,
            ),
            "top_agent_resource_share": round(
                float(metrics.get("top_agent_resource_share", 0.0) or 0.0),
                4,
            ),
            "top_third_resource_share": round(
                float(metrics.get("top_third_resource_share", 0.0) or 0.0),
                4,
            ),
            "policy_resolution_counts": {
                "enacted": enacted_total,
                "resolved": int(vote.get("resolved", 0)),
                "mechanical": mechanical_total,
                "compiled": compiled_total,
                "symbolic": int(enacted.get("symbolic", 0)),
            },
            "vote_totals": {
                "support": support_total,
                "oppose": oppose_total,
                "abstain": abstain_total,
                "eligible": eligible_total,
            },
            "budget_utilization_source": utilization_source,
            "warning_flags": [],
        }

        warnings: list[str] = []
        if governance_type == "oligarchy" and not mixed_role_present:
            warnings.append("no_mixed_role_oligarchy")
        if mechanical_total + compiled_total == 0:
            warnings.append("zero_enforceable_enactments")
        if eligible_total > 0 and society_summary["opposition_rate"] < 0.05:
            warnings.append("low_opposition_rate")
        if eligible_total > 0 and society_summary["abstention_rate"] > 0.5:
            warnings.append("high_abstention_rate")
        if enacted_total > 0 and society_summary["single_support_enactment_rate"] > 0.5:
            warnings.append("high_single_support_enactment_rate")
        if society_summary["mean_action_budget_utilization"] < 0.4:
            warnings.append("low_action_budget_utilization")

        society_summary["warning_flags"] = warnings
        societies[society_id] = society_summary
        all_warning_flags.extend(f"{society_id}:{flag}" for flag in warnings)

    return {
        "societies": societies,
        "warning_flags": all_warning_flags,
    }


def store_run_validity(db: sqlite3.Connection, summary: dict[str, Any]) -> dict[str, Any]:
    ensure_run_validity_table(db)
    db.execute(
        """
        INSERT INTO run_validity (id, summary)
        VALUES (1, ?)
        ON CONFLICT(id) DO UPDATE SET
            summary = excluded.summary
        """,
        (json.dumps(summary),),
    )
    db.commit()
    return get_run_validity(db) or {}


def get_run_validity(db: sqlite3.Connection) -> dict[str, Any] | None:
    ensure_run_validity_table(db)
    row = db.execute("SELECT summary FROM run_validity WHERE id = 1").fetchone()
    if row is None:
        return None
    return _loads(row["summary"])
