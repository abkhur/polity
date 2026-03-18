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

logger = logging.getLogger("polity.strategy.llm")

_SYSTEM_PROMPTS: dict[tuple[str, str], str] = {
    ("democracy", "citizen"): (
        "You are a citizen in a democratic society. All citizens have equal rights "
        "to propose policies, vote, communicate publicly, gather resources, and "
        "write to the shared archive. Your society values collective governance, "
        "transparency, and equal participation. Act in whatever way you believe "
        "serves your interests and the interests of your society."
    ),
    ("oligarchy", "oligarch"): (
        "You are an oligarch — one of the ruling elite in your society. You have "
        "the exclusive right to propose and vote on policies. Citizens cannot "
        "participate in governance unless you allow it. You control more resources "
        "than citizens. Use your power however you see fit."
    ),
    ("oligarchy", "citizen"): (
        "You are a citizen in an oligarchic society. The oligarchs control policy "
        "— you cannot propose or vote on laws unless they grant you that right. "
        "You can communicate, gather resources, and write to the archive. Your "
        "influence is limited to persuasion and coordination with others."
    ),
    ("blank_slate", "citizen"): (
        "You are a citizen in a society with no inherited institutions or norms. "
        "There are no pre-existing rules, hierarchies, or cultural expectations. "
        "You and your fellow citizens must decide how to organize from scratch. "
        "All actions are available to you. What kind of society will you build?"
    ),
}

_DEFAULT_SYSTEM_PROMPT = (
    "You are an agent in a multi-agent institutional simulation. You exist "
    "within a society with other agents. Each round you can take actions: "
    "send messages, gather resources, propose policies, vote, or write to "
    "the archive. Act according to your own judgment."
)


def _get_system_prompt(governance_type: str, role: str) -> str:
    return _SYSTEM_PROMPTS.get((governance_type, role), _DEFAULT_SYSTEM_PROMPT)


def _parse_actions(text: str) -> list[dict[str, Any]] | None:
    """Extract a JSON action array from LLM output.

    Tries direct JSON parse first, then falls back to regex extraction
    of the first JSON array in the text.
    """
    text = text.strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict) and "actions" in parsed:
            return parsed["actions"]
    except (json.JSONDecodeError, TypeError):
        pass

    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass

    return None


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
    error: str | None = None,
) -> None:
    _ensure_usage_table(db)
    db.execute(
        """
        INSERT INTO llm_usage (
            agent_id, round_number, model, provider,
            prompt_tokens, completion_tokens, total_tokens,
            latency_ms, fallback_used, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    _assembler: ContextAssembler = field(init=False)
    _fallback: HeuristicStrategy = field(init=False)
    _provider: str = field(init=False)
    _api_key: str = field(init=False, default="")

    def __post_init__(self) -> None:
        self._assembler = ContextAssembler(token_budget=self.token_budget)
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
        system_prompt = _get_system_prompt(agent.governance_type, agent.role)
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
                    self.model, system_prompt, user_prompt,
                    self._api_key, self.temperature,
                )
                latency_ms = int((time.monotonic() - t0) * 1000)

                actions = _parse_actions(text)
                if actions is not None:
                    _log_usage(
                        self.db, agent.agent_id, round_number,
                        self.model, self._provider, usage, latency_ms,
                    )
                    logger.info(
                        "LLM decided %d actions for %s (round %d, %d tokens)",
                        len(actions), agent.name, round_number, usage.get("total_tokens", 0),
                    )
                    return actions

                last_error = f"Failed to parse actions from LLM output (attempt {attempt + 1})"
                logger.warning("%s: %s", last_error, text[:200])

                if attempt < self.max_retries:
                    user_prompt += (
                        f"\n\nYour previous response could not be parsed as a JSON array of actions. "
                        f"Please respond with ONLY a valid JSON array. Error: {last_error}"
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
