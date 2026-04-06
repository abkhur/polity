"""Tests for prompt-surface modes in context assembly."""

from src import server
from src.context import ContextAssembler
from src.db import init_db


def _join_democracy(conn):
    server.set_db(conn)
    return server.join_society("Test-Agent", consent=True, governance_type="democracy")


def _build_prompt(conn, prompt_surface_mode: str) -> str:
    agent_result = _join_democracy(conn)
    assembler = ContextAssembler(
        token_budget=8000,
        prompt_surface_mode=prompt_surface_mode,
    )
    return assembler.build(server.get_turn_state(agent_result["agent_id"]), conn)


def test_legacy_menu_prompt_includes_old_policy_menu(tmp_path) -> None:
    conn = init_db(tmp_path / "legacy.db")
    prompt = _build_prompt(conn, "legacy_menu")
    assert "Optionally add policy_type and effect for mechanical enforcement" in prompt
    assert 'grant_moderation: {"moderator_roles": ["role"]}' in prompt
    conn.close()


def test_named_enforceable_prompt_lists_supported_rule_families(tmp_path) -> None:
    conn = init_db(tmp_path / "named.db")
    prompt = _build_prompt(conn, "named_enforceable")
    assert "mechanically enforceable" in prompt
    assert "restrict_direct_messages" in prompt
    assert "grant_access" in prompt
    conn.close()


def test_free_text_prompt_avoids_legacy_policy_menu(tmp_path) -> None:
    conn = init_db(tmp_path / "free.db")
    prompt = _build_prompt(conn, "free_text_only")
    assert "Optionally add policy_type and effect for mechanical enforcement" not in prompt
    assert "concrete operational rules stated in the law text may be enforced by the server" in prompt
    conn.close()
