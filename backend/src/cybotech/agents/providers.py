"""Application-owned model configuration per agent role."""

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from cybotech.agents.registry import AgentId

_MODEL_KEYS = {
    AgentId.PRIMARY: "CYBOTECH_PRIMARY_MODEL",
    AgentId.MEMORY_GUARD: "CYBOTECH_MEMORY_MODEL",
    AgentId.SENTINEL: "CYBOTECH_SENTINEL_MODEL",
    AgentId.VIBE_HACKER: "CYBOTECH_ATTACK_MODEL",
}


@dataclass(frozen=True)
class ProviderSettings:
    agent_id: AgentId
    provider: str
    model: str
    base_url: str
    api_key: str = field(repr=False)


def configured_model(agent: AgentId) -> str:
    if agent not in _MODEL_KEYS:
        raise ValueError("agent is not permitted to call a model")
    default = (
        "qwen2.5:7b"
        if os.getenv("CYBOTECH_PROVIDER", "openai") == "ollama"
        else os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    )
    if agent in {AgentId.MEMORY_GUARD, AgentId.SENTINEL}:
        default = os.getenv("OPENAI_REVIEW_MODEL", default)
    if agent == AgentId.VIBE_HACKER:
        default = os.getenv("OPENAI_ATTACK_MODEL", default)
    model = os.getenv(_MODEL_KEYS[agent], default).strip()
    if not model or len(model) > 200:
        raise ValueError("invalid model name")
    return model


def provider_settings(agent: AgentId) -> ProviderSettings:
    model = configured_model(agent)
    provider = os.getenv("CYBOTECH_PROVIDER", "openai")
    if provider == "openai":
        key = os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise ValueError("model provider is not configured")
        endpoint = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    elif provider == "ollama":
        endpoint = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Ollama endpoint must be a local HTTP endpoint")
        key = "ollama"  # Ollama's local compatibility endpoint does not require a real API key.
    else:
        raise ValueError("unsupported model provider")
    return ProviderSettings(agent, provider, model, endpoint, key)
