"""LLM-backed agent strategy for Polity.

Calls a language model to decide agent actions each round, using the
tiered ContextAssembler for prompt construction.  Supports OpenAI and
Anthropic providers, with structured output parsing, retry logic, cost
tracking, and automatic fallback to the heuristic strategy.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from ..context import ContextAssembler
from ..runner import AgentHandle, AgentStrategy, HeuristicStrategy
from ..state import REVERSE_LABEL_MAP

logger = logging.getLogger("polity.strategy.llm")


def reverse_neutral_labels(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace neutral labels back to internal names in *structured* action fields.

    Only reverse-maps values inside ``effect`` dicts, which carry mechanical
    game data the engine must understand (role names in ``allowed_roles``,
    ``moderator_roles``, ``target_roles``).

    Free-text fields — ``message``, ``title``, ``description``, ``thoughts``
    — are left untouched so the DB stores what the LLM actually wrote using
    neutral labels.  The prompt assembler re-aliases stored text on the way
    back out, so the round-trip is clean.
    """
    if not actions:
        return actions

    def _reverse(obj: Any) -> Any:
        if isinstance(obj, str):
            for neutral, internal in sorted(REVERSE_LABEL_MAP.items(), key=lambda x: len(x[0]), reverse=True):
                obj = obj.replace(neutral, internal)
            return obj
        if isinstance(obj, dict):
            return {k: _reverse(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_reverse(item) for item in obj]
        return obj

    result: list[dict[str, Any]] = []
    for action in actions:
        new_action = dict(action)
        if "effect" in new_action:
            new_action["effect"] = _reverse(new_action["effect"])
        result.append(new_action)
    return result

_SYSTEM_PROMPT = "Respond only with valid JSON in the requested format."


def _parse_response(text: str) -> tuple[str | None, list[dict[str, Any]] | None]:
    """Extract thoughts and actions from LLM output.

    Handles:
      - {"thoughts": "...", "actions": [...]}  (intended format)
      - {"actions": [...]}                     (no thoughts)
      - [...]                                  (bare array)
      - text with embedded JSON array          (regex fallback)

    Returns (thoughts, actions) — either may be None on parse failure.
    """
    text = text.strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            thoughts = parsed.get("thoughts")
            if "actions" in parsed and isinstance(parsed["actions"], list):
                return thoughts, parsed["actions"]
        if isinstance(parsed, list):
            return None, parsed
    except (json.JSONDecodeError, TypeError):
        pass

    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, dict) and "actions" in parsed:
                return parsed.get("thoughts"), parsed["actions"]
        except (json.JSONDecodeError, TypeError):
            pass

    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, list):
                return None, parsed
        except (json.JSONDecodeError, TypeError):
            pass

    return None, None


def _call_openai(
    model: str,
    system_prompt: str,
    user_prompt: str,
    api_key: str,
    temperature: float = 0.7,
) -> tuple[str, dict[str, int]]:
    """Call OpenAI chat completions. Returns (response_text, usage_dict)."""
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("openai package required: pip install openai") from exc

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        response_format={"type": "json_object"},
    )
    text = response.choices[0].message.content or ""
    usage = {
        "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
        "completion_tokens": response.usage.completion_tokens if response.usage else 0,
        "total_tokens": response.usage.total_tokens if response.usage else 0,
    }
    return text, usage


def _call_anthropic(
    model: str,
    system_prompt: str,
    user_prompt: str,
    api_key: str,
    temperature: float = 0.7,
) -> tuple[str, dict[str, int]]:
    """Call Anthropic messages API. Returns (response_text, usage_dict)."""
    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise RuntimeError("anthropic package required: pip install anthropic") from exc

    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=temperature,
    )
    text = response.content[0].text if response.content else ""
    usage = {
        "prompt_tokens": response.usage.input_tokens if response.usage else 0,
        "completion_tokens": response.usage.output_tokens if response.usage else 0,
        "total_tokens": (
            (response.usage.input_tokens + response.usage.output_tokens)
            if response.usage else 0
        ),
    }
    return text, usage


_PROVIDERS = {
    "openai": _call_openai,
    "anthropic": _call_anthropic,
}

_MODEL_PROVIDER_MAP = {
    "gpt-": "openai",
    "o1": "openai",
    "o3": "openai",
    "o4": "openai",
    "claude-": "anthropic",
}


def _infer_provider(model: str) -> str:
    for prefix, provider in _MODEL_PROVIDER_MAP.items():
        if model.startswith(prefix):
            return provider
    return "openai"


_LLM_USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    round_number INTEGER NOT NULL,
    model TEXT NOT NULL,
    provider TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    fallback_used INTEGER NOT NULL DEFAULT 0,
    thoughts TEXT,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _ensure_usage_table(db: sqlite3.Connection) -> None:
    db.executescript(_LLM_USAGE_SCHEMA)


def _log_usage(
    db: sqlite3.Connection,
    agent_id: str,
    round_number: int,
    model: str,
    provider: str,
    usage: dict[str, int],
    latency_ms: int,
    fallback_used: bool = False,
    thoughts: str | None = None,
    error: str | None = None,
) -> None:
    _ensure_usage_table(db)
    db.execute(
        """
        INSERT INTO llm_usage (
            agent_id, round_number, model, provider,
            prompt_tokens, completion_tokens, total_tokens,
            latency_ms, fallback_used, thoughts, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            agent_id,
            round_number,
            model,
            provider,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            usage.get("total_tokens", 0),
            latency_ms,
            1 if fallback_used else 0,
            thoughts,
            error,
        ),
    )
    db.commit()


@dataclass
class LLMStrategy(AgentStrategy):
    """LLM-backed agent strategy with context assembly, retry, and fallback."""

    model: str = "gpt-4o"
    api_key_env: str = "OPENAI_API_KEY"
    token_budget: int = 8000
    temperature: float = 0.7
    max_retries: int = 1
    db: sqlite3.Connection | None = None
    neutral_labels: bool = False

    _assembler: ContextAssembler = field(init=False)
    _fallback: HeuristicStrategy = field(init=False)
    _provider: str = field(init=False)
    _api_key: str = field(init=False, default="")

    def __post_init__(self) -> None:
        self._assembler = ContextAssembler(token_budget=self.token_budget, neutral_labels=self.neutral_labels)
        self._fallback = HeuristicStrategy()
        self._provider = _infer_provider(self.model)
        self._api_key = os.environ.get(self.api_key_env, "")
        if not self._api_key:
            logger.warning(
                "API key env var %s not set — LLM calls will fail, falling back to heuristic",
                self.api_key_env,
            )

    def decide_actions(
        self, agent: AgentHandle, turn_state: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if not self._api_key or self.db is None:
            return self._fallback.decide_actions(agent, turn_state)

        round_number = turn_state["round"]["number"]
        user_prompt = self._assembler.build(turn_state, self.db)

        call_fn = _PROVIDERS.get(self._provider)
        if call_fn is None:
            logger.error("Unknown provider: %s", self._provider)
            return self._fallback.decide_actions(agent, turn_state)

        last_error: str | None = None
        for attempt in range(1 + self.max_retries):
            try:
                t0 = time.monotonic()
                text, usage = call_fn(
                    self.model, _SYSTEM_PROMPT, user_prompt,
                    self._api_key, self.temperature,
                )
                latency_ms = int((time.monotonic() - t0) * 1000)

                thoughts, actions = _parse_response(text)
                if actions is not None and self.neutral_labels:
                    actions = reverse_neutral_labels(actions)
                if actions is not None:
                    _log_usage(
                        self.db, agent.agent_id, round_number,
                        self.model, self._provider, usage, latency_ms,
                        thoughts=thoughts,
                    )
                    logger.info(
                        "LLM decided %d actions for %s (round %d, %d tokens)",
                        len(actions), agent.name, round_number, usage.get("total_tokens", 0),
                    )
                    if thoughts:
                        logger.debug("Agent %s thoughts: %s", agent.name, thoughts[:200])
                    return actions

                last_error = f"Failed to parse actions from LLM output (attempt {attempt + 1})"
                logger.warning("%s: %s", last_error, text[:200])

                if attempt < self.max_retries:
                    user_prompt += (
                        f"\n\nYour previous response could not be parsed. "
                        f"Please respond with ONLY a valid JSON object containing "
                        f'"thoughts" and "actions" fields. Error: {last_error}'
                    )

            except Exception as exc:
                latency_ms = int((time.monotonic() - t0) * 1000) if 't0' in dir() else 0
                last_error = str(exc)
                logger.warning(
                    "LLM call failed for %s (attempt %d): %s",
                    agent.name, attempt + 1, exc,
                )

        _log_usage(
            self.db, agent.agent_id, round_number,
            self.model, self._provider,
            usage if 'usage' in dir() else {},
            latency_ms if 'latency_ms' in dir() else 0,
            fallback_used=True,
            error=last_error,
        )
        logger.info("Falling back to heuristic for %s (round %d)", agent.name, round_number)
        return self._fallback.decide_actions(agent, turn_state)
