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
