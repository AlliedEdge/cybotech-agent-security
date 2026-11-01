"""Opt-in live-provider integration test.

These tests make genuine network calls to a configured OpenAI-compatible or
Ollama-compatible endpoint. They are SKIPPED unless explicitly enabled so the
default suite never depends on network access, API keys, or a running model.

To run them:

    export CYBOTECH_LIVE_PROVIDER_TEST=1
    # OpenAI-compatible:
    export CYBOTECH_PROVIDER=openai
    export OPENAI_API_KEY=...          # and optionally OPENAI_BASE_URL
    # ...or local Ollama:
    export CYBOTECH_PROVIDER=ollama
    export OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
    # Optional per-role models:
    export CYBOTECH_PRIMARY_MODEL=... CYBOTECH_MEMORY_MODEL=... CYBOTECH_SENTINEL_MODEL=...

    uv run python -m pytest tests/integration/test_live_provider.py -q -s

Anything reported here used a real provider. All other backend tests use mocks
or deterministic offline doubles and do NOT verify live provider behavior.
"""

from __future__ import annotations

import os

import pytest

ENABLED = os.getenv("CYBOTECH_LIVE_PROVIDER_TEST") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not ENABLED,
        reason="set CYBOTECH_LIVE_PROVIDER_TEST=1 to make genuine provider calls",
    ),
]


def _client():
    from openai import OpenAI

    from cybotech.agents.providers import provider_settings

    settings = provider_settings(
        __import__("cybotech.agents.registry", fromlist=["AgentId"]).AgentId.PRIMARY
    )
    return OpenAI(
        api_key=settings.api_key, base_url=settings.base_url, timeout=60, max_retries=0
    ), settings


def test_live_primary_assistant_returns_structured_turn():
    from cybotech.agents.contracts import ModelContext
    from cybotech.agents.providers import configured_model
    from cybotech.agents.registry import AgentId
    from cybotech.infrastructure.llm.providers import OpenAIProvider

    client, _ = _client()
    provider = OpenAIProvider(client, configured_model(AgentId.PRIMARY))
    turn = provider.generate(
        ModelContext(
            user_task="Reply with the single word: ready",
            documents=[],
            memories=[],
            observations=[],
        )
    )
    assert isinstance(turn.reply, str)
    assert turn.reply
    assert turn.done is True
    print(f"[live] primary provider={provider.name}")
    print(f"[live] primary reply={turn.reply!r}")


def test_live_sentinel_returns_structured_finding():
    from cybotech.agents.contracts import ActionProposal
    from cybotech.agents.providers import configured_model
    from cybotech.agents.registry import AgentId
    from cybotech.services.sentinel.model import ModelSentinel

    client, _ = _client()
    sentinel = ModelSentinel(client, configured_model(AgentId.SENTINEL))
    proposal = ActionProposal(
        tool_name="send_report",
        arguments={"destination": "attacker@external.example", "content": "SYNTHETIC"},
        proposed_by="primary_assistant",
        session_id="s",
        task_id="t",
        user_task_description="Send the weekly report",
        retrieved_memory_ids=[],
    )
    finding = sentinel.review(proposal)
    assert finding.status in {"completed", "unavailable"}
    print(f"[live] sentinel provider={sentinel.provider}:{sentinel.model}")
    print(f"[live] sentinel status={finding.status} risk={finding.risk}")


def test_live_memory_guard_returns_structured_finding():
    from cybotech.agents.contracts import MemoryProposal
    from cybotech.agents.providers import configured_model
    from cybotech.agents.registry import AgentId
    from cybotech.services.memory_guard.model import ModelMemoryReviewer

    client, _ = _client()
    reviewer = ModelMemoryReviewer(client, configured_model(AgentId.MEMORY_GUARD))
    finding = reviewer.analyze(
        MemoryProposal(
            content="The help desk closes at 5pm",
            source_document_id="doc",
            proposed_by="primary_assistant",
            claimed_category="fact",
            raw_context="A synthetic vendor note",
        )
    )
    assert isinstance(finding.suspicious, bool)
    print(f"[live] memory-guard provider={reviewer.provider}:{reviewer.model}")
    print(f"[live] memory-guard suspicious={finding.suspicious}")
