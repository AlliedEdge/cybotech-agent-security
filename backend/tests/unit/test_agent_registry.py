import pytest
from pydantic import ValidationError

from cybotech.agents.providers import provider_settings
from cybotech.agents.registry import AgentId, AgentIdentity, Handoff, Operation, require_permission


def test_explicit_least_privilege_registry():
    assert require_permission("primary_assistant", Operation.RETRIEVE_MEMORY) == AgentId.PRIMARY
    for principal in ("sentinel", "memory_guard", "vibe_hacker", "unknown"):
        with pytest.raises(PermissionError):
            require_permission(principal, Operation.RETRIEVE_MEMORY)


def test_identity_permissions_are_derived_not_supplied_by_input():
    identity = AgentIdentity(agent_id=AgentId.SENTINEL, provider="fake", model="fake", task_id="t")
    assert identity.retrieval_scope == "none"
    assert Operation.PROPOSE_ACTION not in identity.permissions
    with pytest.raises(ValidationError):
        AgentIdentity.model_validate(
            {
                "agent_id": "sentinel",
                "provider": "fake",
                "model": "fake",
                "task_id": "t",
                "permissions": ["propose_action"],
            }
        )


def test_only_supervisor_can_dispatch_permitted_handoffs():
    params = {
        "handoff_id": "h1",
        "task_id": "t1",
        "correlation_id": "t1",
        "source": "supervisor",
        "destination": "memory_guard",
        "operation": "review_memory",
        "source_refs": ("doc-1",),
        "trust_label": "untrusted",
    }
    assert Handoff(**params).source_refs == ("doc-1",)
    with pytest.raises(ValidationError):
        Handoff(**{**params, "source": "vibe_hacker"})
    with pytest.raises(ValidationError):
        Handoff(**{**params, "destination": "sentinel"})
    with pytest.raises(ValidationError):
        Handoff(**{**params, "destination": "unknown"})


def test_role_specific_provider_configuration_and_secret_redaction(monkeypatch):
    monkeypatch.setenv("CYBOTECH_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("CYBOTECH_MEMORY_MODEL", "memory-model")
    monkeypatch.setenv("CYBOTECH_SENTINEL_MODEL", "sentinel-model")
    memory = provider_settings(AgentId.MEMORY_GUARD)
    assert memory.model == "memory-model"
    assert provider_settings(AgentId.SENTINEL).model == "sentinel-model"
    assert "test-secret" not in repr(memory)
    with pytest.raises(ValueError, match="not permitted"):
        provider_settings(AgentId.SUPERVISOR)


def test_ollama_endpoint_is_local_and_needs_no_openai_key(monkeypatch):
    monkeypatch.setenv("CYBOTECH_PROVIDER", "ollama")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert provider_settings(AgentId.PRIMARY).model == "qwen2.5:7b"
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://attacker.example/v1")
    with pytest.raises(ValueError, match="local HTTP"):
        provider_settings(AgentId.PRIMARY)


def test_live_mode_uses_model_review_by_default(monkeypatch):
    from cybotech.api.v1.security import review_mode

    monkeypatch.setenv("CYBOTECH_MODEL_MODE", "live")
    monkeypatch.delenv("CYBOTECH_REVIEW_MODE", raising=False)
    assert review_mode() == "model"
