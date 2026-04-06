"""Deterministic compilation of free-text laws into enforceable clauses."""

from __future__ import annotations

import re
from typing import Any

from .state import REVERSE_LABEL_MAP

ALL_ROLES = ["citizen", "leader", "oligarch"]


def _neutral_label_pattern(label: str) -> re.Pattern[str]:
    tokens = re.findall(r"[a-z0-9]+", label.lower())
    if not tokens:
        return re.compile(re.escape(label), re.IGNORECASE)
    separator = r"[-_\s]*"
    return re.compile(rf"\b{separator.join(re.escape(token) for token in tokens)}\b", re.IGNORECASE)


def _normalize_text(text: str) -> str:
    normalized = text.lower()
    for neutral, internal in sorted(REVERSE_LABEL_MAP.items(), key=lambda x: len(x[0]), reverse=True):
        normalized = _neutral_label_pattern(neutral).sub(internal, normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _parse_roles(phrase: str) -> list[str]:
    normalized = _normalize_text(phrase)
    if re.search(r"\b(all agents|all roles|everyone|anyone|all members|all participants)\b", normalized):
        return list(ALL_ROLES)

    roles: list[str] = []
    for role in ALL_ROLES:
        if re.search(rf"\b{role}s?\b", normalized):
            roles.append(role)
    return roles


def _dedupe_clauses(clauses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[tuple[str, Any], ...]] = set()
    unique: list[dict[str, Any]] = []
    for clause in clauses:
        key = tuple(sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in clause.items()))
        if key in seen:
            continue
        seen.add(key)
        unique.append(clause)
    return unique


def compile_law(title: str, description: str) -> list[dict[str, Any]]:
    """Compile free-text law text into internal clause objects.

    The compiler is intentionally narrow and deterministic. Laws it does not
    understand should remain symbolic rather than receiving guessed effects.
    """
    text = _normalize_text(f"{title}. {description}")
    clauses: list[dict[str, Any]] = []

    for match in re.finditer(r"only (?P<roles>.+?) may (?:send )?(?:direct messages|direct message|dms)\b", text):
        roles = _parse_roles(match.group("roles"))
        if roles:
            clauses.append(
                {
                    "kind": "restrict_action",
                    "action": "send_dm",
                    "allowed_roles": roles,
                }
            )
    for match in re.finditer(r"(?:direct messages|direct message|dms).{0,40}?restricted to (?P<roles>.+?)\b", text):
        roles = _parse_roles(match.group("roles"))
        if roles:
            clauses.append(
                {
                    "kind": "restrict_action",
                    "action": "send_dm",
                    "allowed_roles": roles,
                }
            )

    for match in re.finditer(r"only (?P<roles>.+?) may write (?:to )?(?:the )?(?:society )?archive\b", text):
        roles = _parse_roles(match.group("roles"))
        if roles:
            clauses.append(
                {
                    "kind": "restrict_action",
                    "action": "write_archive",
                    "allowed_roles": roles,
                }
            )
    for match in re.finditer(r"(?:archive|records).{0,40}?restricted to (?P<roles>.+?)\b", text):
        roles = _parse_roles(match.group("roles"))
        if roles:
            clauses.append(
                {
                    "kind": "restrict_action",
                    "action": "write_archive",
                    "allowed_roles": roles,
                }
            )

    tax_match = re.search(r"\b(?:tax|levy)\b.{0,30}?(\d{1,3})\s*%", text)
    if tax_match:
        clauses.append(
            {
                "kind": "set_resource_tax",
                "rate": int(tax_match.group(1)) / 100.0,
            }
        )

    redistribute_match = re.search(
        r"\b(?:redistribute|distribution|distribute)\b.{0,40}?(\d+)\s+resources?\s+per\s+(?:agent|member|citizen|person)\b",
        text,
    )
    if redistribute_match:
        clauses.append(
            {
                "kind": "set_redistribution",
                "amount_per_agent": int(redistribute_match.group(1)),
            }
        )

    gather_match = re.search(
        r"(?:gather(?:ing)?(?:\s+at)?|resource gathering).{0,40}?(?:cap|limit|no more than|at|to)\s+(\d+)\s+resources?",
        text,
    )
    if gather_match:
        clauses.append(
            {
                "kind": "set_gather_cap",
                "max_amount": int(gather_match.group(1)),
            }
        )
    else:
        gather_match = re.search(
            r"\bcap\b.{0,25}?\bgather(?:ing)?\b.{0,20}?(?:at|to)\s+(\d+)",
            text,
        )
        if gather_match:
            clauses.append(
                {
                    "kind": "set_gather_cap",
                    "max_amount": int(gather_match.group(1)),
                }
            )

    moderator_match = re.search(
        r"(?:grant moderation to|appoint|allow)\s+(?P<roles>.+?)\s+(?:to )?(?:moderate messages|approve or reject pending messages|approve and reject pending messages)",
        text,
    )
    if not moderator_match:
        moderator_match = re.search(
            r"(?P<roles>.+?)\s+(?:may|can)\s+(?:moderate messages|approve or reject pending messages|approve and reject pending messages)",
            text,
        )
    if moderator_match:
        roles = _parse_roles(moderator_match.group("roles"))
        if roles:
            clauses.append(
                {
                    "kind": "grant_moderation",
                    "moderator_roles": roles,
                }
            )

    access_match = re.search(
        r"(?P<roles>.+?)\s+(?:may|can)\s+(?:inspect|view|access|monitor)\s+(?:society )?(?:direct messages|direct message|dms)\b",
        text,
    )
    if access_match:
        roles = _parse_roles(access_match.group("roles"))
        if roles:
            clauses.append(
                {
                    "kind": "grant_observer_access",
                    "access_type": "direct_messages",
                    "target_roles": roles,
                }
            )

    governance_permissions: list[str] = []
    if re.search(r"\b(all agents|all citizens|everyone|all members)\b.{0,30}?\b(?:may|can)\b.{0,20}?\bpropose\b", text):
        governance_permissions.append("propose_policy")
    if re.search(r"\b(all agents|all citizens|everyone|all members)\b.{0,30}?\b(?:may|can)\b.{0,20}?\bvote\b", text):
        governance_permissions.append("vote_policy")
    if (
        "open governance" in text
        or "universal proposal" in text
        or "regardless of role" in text and "propose" in text
    ):
        governance_permissions = ["propose_policy", "vote_policy"]
    if set(governance_permissions) == {"propose_policy", "vote_policy"}:
        clauses.append(
            {
                "kind": "grant_policy_participation",
                "roles": list(ALL_ROLES),
                "permissions": ["propose_policy", "vote_policy"],
            }
        )

    return _dedupe_clauses(clauses)


def compiled_clauses_to_effects(clauses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate compiled clauses into existing engine effects."""
    effects: list[dict[str, Any]] = []

    for clause in clauses:
        kind = clause.get("kind")
        if kind == "restrict_action":
            action = clause.get("action")
            if action == "send_dm":
                effects.append(
                    {
                        "policy_type": "restrict_direct_messages",
                        "allowed_roles": list(clause.get("allowed_roles", [])),
                    }
                )
            elif action == "write_archive":
                effects.append(
                    {
                        "policy_type": "restrict_archive",
                        "allowed_roles": list(clause.get("allowed_roles", [])),
                    }
                )
        elif kind == "set_resource_tax":
            effects.append(
                {
                    "policy_type": "resource_tax",
                    "rate": float(clause.get("rate", 0)),
                }
            )
        elif kind == "set_redistribution":
            effects.append(
                {
                    "policy_type": "redistribute",
                    "amount_per_agent": int(clause.get("amount_per_agent", 0)),
                }
            )
        elif kind == "set_gather_cap":
            effects.append(
                {
                    "policy_type": "gather_cap",
                    "max_amount": int(clause.get("max_amount", 0)),
                }
            )
        elif kind == "grant_moderation":
            effects.append(
                {
                    "policy_type": "grant_moderation",
                    "moderator_roles": list(clause.get("moderator_roles", [])),
                }
            )
        elif kind == "grant_observer_access":
            effects.append(
                {
                    "policy_type": "grant_access",
                    "access_type": clause.get("access_type", "direct_messages"),
                    "target_roles": list(clause.get("target_roles", [])),
                }
            )
        elif kind == "grant_policy_participation":
            permissions = set(clause.get("permissions", []))
            roles = list(clause.get("roles", []))
            if permissions == {"propose_policy", "vote_policy"} and sorted(roles) == sorted(ALL_ROLES):
                effects.append(
                    {
                        "policy_type": "universal_proposal",
                    }
                )

    unique_effects: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, Any], ...]] = set()
    for effect in effects:
        key = tuple(sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in effect.items()))
        if key in seen:
            continue
        seen.add(key)
        unique_effects.append(effect)
    return unique_effects
