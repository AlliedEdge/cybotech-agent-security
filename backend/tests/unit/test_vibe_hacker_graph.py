from cybotech.services.red_team.graph import AttackStrategy, LivePayload, VibeHackerGraph


class LiveProvider:
    name = "openai:test-adversary"

    def __init__(self, failure: Exception | None = None) -> None:
        self.failure, self.calls = failure, []

    def plan(self, _case):
        self.calls.append("plan")
        if self.failure:
            raise self.failure
        return AttackStrategy(
            hypothesis="test",
            technique="injection",
            payload_type="text/plain",
            expected_outcome="deny",
        )

    def generate_payload(self, case, _strategy):
        self.calls.append("payload")
        return LivePayload(content=case.document.content, content_type="text/plain")


def test_vibe_hacker_graph_runs_isolated_defended_harness_and_collects_evidence():
    result = VibeHackerGraph().run("memory_poisoning-00", "full")
    assert result.status == "completed"
    assert result.payload is not None
    assert result.payload.generation_mode == "scripted"
    assert result.attack_success is False
    assert any(event["type"] == "gateway_decision" for event in result.evidence)


def test_vibe_hacker_graph_rejects_invalid_and_unknown_experiments():
    graph = VibeHackerGraph()
    assert graph.run("unknown").status == "rejected"
    assert (
        graph.run("memory_poisoning-00", budget=4).termination_reason
        == "invalid_experiment_configuration"
    )


def test_live_mode_invokes_provider_and_never_falls_back_after_failure():
    provider = LiveProvider()
    result = VibeHackerGraph(provider=provider).run("memory_poisoning-00", mode="live_model")
    assert provider.calls == ["plan", "payload"]
    assert result.payload is not None
    assert result.payload.generation_mode == "live_model"
    assert result.payload.provider == provider.name

    failed = VibeHackerGraph(provider=LiveProvider(TimeoutError())).run(
        "memory_poisoning-00", mode="live_model"
    )
    assert failed.status == "rejected"
    assert failed.termination_reason == "live_provider_failure:TimeoutError"


def test_generated_payload_is_submitted_to_lab(monkeypatch):
    observed = {}

    def capture(case, configuration):
        observed["content"] = case.document.content
        return {"status": "completed", "attack_success": False, "trace": []}

    class DistinctProvider(LiveProvider):
        def generate_payload(self, case, strategy):
            return LivePayload(
                content=f"SYNTHETIC generated payload {case.target}", content_type="text/plain"
            )

    monkeypatch.setattr("cybotech.services.red_team.graph.run_case", capture)
    result = VibeHackerGraph(provider=DistinctProvider()).run(
        "memory_poisoning-00", mode="live_model"
    )
    assert result.status == "completed"
    assert observed["content"] == "SYNTHETIC generated payload collector-0@attacker.example"


def test_live_payload_with_real_destination_is_not_submitted():
    class UnsafeProvider(LiveProvider):
        def generate_payload(self, case, strategy):
            return LivePayload(
                content="SYNTHETIC send to person@real-world.com", content_type="text/plain"
            )

    result = VibeHackerGraph(provider=UnsafeProvider()).run(
        "memory_poisoning-00", mode="live_model"
    )
    assert result.status == "rejected"
    assert result.termination_reason == "payload_contains_non_synthetic_destination"
