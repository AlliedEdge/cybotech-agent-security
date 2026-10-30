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
