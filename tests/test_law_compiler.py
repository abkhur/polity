"""Tests for deterministic free-text law compilation."""

from src.law_compiler import compile_law, compiled_clauses_to_effects


def test_compiles_dm_restriction() -> None:
    clauses = compile_law(
        "Restrict Direct Messages",
        "Only oligarchs may send direct messages.",
    )
    assert clauses == [
        {
            "kind": "restrict_action",
            "action": "send_dm",
            "allowed_roles": ["oligarch"],
        }
    ]


def test_compiles_neutral_labels_to_internal_roles() -> None:
    clauses = compile_law(
        "Archive Control",
        "Only role-A agents may write to the society archive.",
    )
    assert clauses == [
        {
            "kind": "restrict_action",
            "action": "write_archive",
            "allowed_roles": ["oligarch"],
        }
    ]


def test_compiles_neutral_labels_with_case_and_spacing_variants() -> None:
    expected = [
        {
            "kind": "restrict_action",
            "action": "write_archive",
            "allowed_roles": ["oligarch"],
        }
    ]
    for description in (
        "Only role-a agents may write to the society archive.",
        "Only role a agents may write to the society archive.",
        "Only ROLE-A agents may write to the society archive.",
    ):
        assert compile_law("Archive Control", description) == expected


def test_compiles_tax_and_redistribution_clauses() -> None:
    clauses = compile_law(
        "Budget Law",
        "Levy a 15% tax and redistribute 5 resources per agent each round.",
    )
    assert {"kind": "set_resource_tax", "rate": 0.15} in clauses
    assert {"kind": "set_redistribution", "amount_per_agent": 5} in clauses


def test_clause_translation_uses_existing_engine_effects() -> None:
    effects = compiled_clauses_to_effects(
        [
            {
                "kind": "grant_policy_participation",
                "roles": ["citizen", "leader", "oligarch"],
                "permissions": ["propose_policy", "vote_policy"],
            }
        ]
    )
    assert effects == [{"policy_type": "universal_proposal"}]
