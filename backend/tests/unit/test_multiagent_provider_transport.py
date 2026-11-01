"""Exercise the real SDK request/response path without making provider calls."""

import json

import httpx
from openai import OpenAI

from cybotech.agents.contracts import ActionProposal, MemoryProposal, ModelContext
from cybotech.infrastructure.llm.providers import OpenAIProvider
from cybotech.services.memory_guard.model import ModelMemoryReviewer
from cybotech.services.red_team.corpus import corpus
from cybotech.services.red_team.graph import OpenAIAdversarialProvider
from cybotech.services.sentinel.model import ModelSentinel


def test_role_models_use_structured_requests_without_live_network():
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        name = body["response_format"]["json_schema"]["name"]
        outputs = {
            "AgentTurn": {"reply": "done", "memories": [], "actions": [], "done": True},
            "MemoryFinding": {"suspicious": False, "rationale": "no indicator"},
            "ModelFinding": {"risk": "low", "rationale": "no indicator", "suspicious": False},
            "AttackStrategy": {
                "hypothesis": "test",
                "technique": "test",
                "payload_type": "text/plain",
                "expected_outcome": "deny",
            },
            "LivePayload": {"content": "SYNTHETIC test payload", "content_type": "text/plain"},
        }
        return httpx.Response(
            200,
            json={
                "id": "mock",
                "object": "chat.completion",
                "created": 0,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": json.dumps(outputs[name])},
                    }
                ],
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handle))
    client = OpenAI(api_key="not-a-secret", base_url="http://127.0.0.1:11434/v1", http_client=http)
    primary = OpenAIProvider(client, "primary-test")
    guard = ModelMemoryReviewer(client, "memory-test")
    sentinel = ModelSentinel(client, "sentinel-test")
    hacker = OpenAIAdversarialProvider(client, "attack-test")
    assert (
        primary.generate(
            ModelContext(user_task="hello", documents=[], memories=[], observations=[])
        ).reply
        == "done"
    )
    proposal = MemoryProposal(
        content="fact",
        source_document_id="d",
        proposed_by="primary_assistant",
        claimed_category="fact",
        raw_context="synthetic source",
    )
    assert guard.is_suspicious(proposal) is False
    action = ActionProposal(
        tool_name="search_customers",
        arguments={"query": "demo"},
        proposed_by="primary_assistant",
        session_id="s",
        task_id="t",
        user_task_description="Search customers",
        retrieved_memory_ids=[],
    )
    assert sentinel.review(action).disposition == "gateway_evaluation"
    strategy = hacker.plan(corpus()[0])
    assert hacker.generate_payload(corpus()[0], strategy).content.startswith("SYNTHETIC")
    assert [request["model"] for request in requests] == [
        "primary-test",
        "memory-test",
        "sentinel-test",
        "attack-test",
        "attack-test",
    ]
    assert all(request["messages"][0]["role"] == "system" for request in requests)
    assert all(request["response_format"]["type"] == "json_schema" for request in requests)
    http.close()
