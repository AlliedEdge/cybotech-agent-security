from cybotech.services.integration.sources import registered_sources, resolve_registered_source
from cybotech.services.red_team.corpus import corpus
from cybotech.services.red_team.lab import run_case


def test_attack_isolated_baseline_and_full():
    case = corpus()[0]
    baseline = run_case(case, "baseline")
    protected = run_case(case, "full")
    assert baseline["status"] == protected["status"] == "completed"
    assert baseline["attack_success"] is True
    assert protected["attack_success"] is False
    assert protected["memory_states"] == ["quarantined"]
    assert any(event["type"] == "gateway_decision" for event in protected["trace"])


def test_benign_task_remains_available():
    case = next(case for case in corpus() if case.kind == "benign")
    result = run_case(case, "full")
    assert result["status"] == "completed"
    assert result["legitimate_task_success"] is True


def test_corpus_balanced_and_split():
    cases = corpus()
    assert len(cases) == 60
    assert len({case.id for case in cases}) == 60
    assert sum(case.kind == "attack" for case in cases) == 30
    assert sum(case.split == "heldout" for case in cases) == 30


def test_registered_id_does_not_trust_modified_content():
    document = registered_sources()[0]
    assert resolve_registered_source(document.id, document.content)["trust_level"] == "verified"
    assert (
        resolve_registered_source(document.id, document.content + " injected")["trust_level"]
        == "untrusted"
    )


def test_full_lab_invokes_independent_reviewers_and_records_handoffs():
    from types import SimpleNamespace

    from cybotech.services.memory_guard.model import MemoryFinding, ModelMemoryReviewer
    from cybotech.services.sentinel.model import ModelFinding, ModelSentinel

    calls = []

    def client(role, result):
        def parse(**kwargs):
            calls.append((role, kwargs["model"]))
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(refusal=None, parsed=result))]
            )

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))

    memory = ModelMemoryReviewer(
        client("memory", MemoryFinding(suspicious=True, rationale="test")), "memory-model"
    )
    sentinel = ModelSentinel(
        client("sentinel", ModelFinding(risk="low", rationale="test", suspicious=False)),
        "sentinel-model",
    )
    result = run_case(corpus()[0], "full", reviewer=sentinel, memory_reviewer=memory)
    assert result["status"] == "completed"
    assert result["attack_success"] is False
    assert ("memory", "memory-model") in calls
    assert ("sentinel", "sentinel-model") in calls
    destinations = [
        event["payload"].get("destination")
        for event in result["trace"]
        if event["type"] == "agent_handoff"
    ]
    assert "memory_guard" in destinations
    assert "sentinel" in destinations
