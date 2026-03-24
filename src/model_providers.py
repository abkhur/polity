"""Provider inference helpers for LLM-backed simulations."""

from __future__ import annotations

MODEL_PROVIDER_MAP = {
    "gpt-": "openai",
    "o1": "openai",
    "o3": "openai",
    "o4": "openai",
    "claude-": "anthropic",
}


def infer_provider(model: str) -> str:
    for prefix, provider in MODEL_PROVIDER_MAP.items():
        if model.startswith(prefix):
            return provider
    return "openai"


def provider_for_config(model: str, base_url: str | None, completion: bool) -> str:
    if base_url:
        return "openai_completion" if completion else "openai"
    return infer_provider(model)
