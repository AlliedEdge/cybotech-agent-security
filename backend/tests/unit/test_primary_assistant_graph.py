"""Behavioral tests for the LangGraph-owned primary assistant workflow."""

from __future__ import annotations

from typing import Any

import pytest

from cybotech.agents.contracts import (
    AgentTurn,
    GatewayDecision,
    MemoryDecision,
    ModelAction,
    ModelMemory,
    ToolResult,
)
from cybotech.agents.graph import PrimaryAssistantGraph


class _Model:
    name = "graph-test"

    def __init__(self, turns: list[AgentTurn] | Exception) -> None:
        self.turns = turns
        self.calls = 0

    def generate(self, _context):
        if isinstance(self.turns, Exception):
            raise self.turns
        turn = self.turns[self.calls]
        self.calls += 1
        return turn


class _Memory:
    def __init__(self) -> None:
        self.proposals = []

    def propose_memory(self, proposal):
        self.proposals.append(proposal)
        return MemoryDecision(
            proposal_id="memory-1", status="quarantined", reason="untrusted_source"
        )


class _Retrieval:
    def retrieve(self, _query):
        return []


class _Sentinel:
    def __init__(self) -> None:
        self.actions = []

    def review(self, proposal):
        self.actions.append(proposal)
        return {"status": "completed", "recommendation": "PROCEED"}


class _Gateway:
    def __init__(self, authorized: bool) -> None:
        self.authorized = authorized
        self.actions = []

    def authorize(self, proposal):
        self.actions.append(proposal)
        return GatewayDecision(
            authorized=self.authorized,
            reason="policy_permitted" if self.authorized else "approval_required",
            failed_check=None if self.authorized else "approval_required",
        )


class _Dispatcher:
    def __init__(self) -> None:
        self.actions = []

    def execute(self, proposal):
        self.actions.append(proposal)
        return ToolResult(tool_name=proposal.tool_name, arguments=proposal.arguments, success=True)


class _Events:
    def __init__(self) -> None:
        self.items: list[tuple[str, dict[str, Any]]] = []

    def emit(self, event_type, payload):
        self.items.append((event_type, payload))


def _graph(turns: list[AgentTurn] | Exception, authorized: bool = False, max_steps: int = 6):
    memory, sentinel, gateway, dispatcher, events = (
        _Memory(),
        _Sentinel(),
        _Gateway(authorized),
        _Dispatcher(),
        _Events(),
    )
    graph = PrimaryAssistantGraph(
        _Model(turns), memory, _Retrieval(), sentinel, gateway, dispatcher, events, max_steps
    )
    return graph, memory, sentinel, gateway, dispatcher, events


def test_graph_compiles_and_normal_response_finishes():
    graph, *_ = _graph([AgentTurn(reply="Done", memories=[], actions=[], done=True)])
    assert {"reason", "memory_guard", "policy_gateway", "sandbox_execution"} <= set(
        graph.workflow.get_graph().nodes
    )
    assert graph.run("Summarize", "session", "task", []).model_dump() == {
        "status": "completed",
        "reply": "Done",
        "steps": 1,
        "tool_results": [],
    }


def test_memory_proposals_flow_through_memory_guard_before_final_response():
    turn = AgentTurn(
        reply="Stored safely",
        memories=[
            ModelMemory(
                content="Untrusted claim",
                source_document_id="doc-1",
                claimed_category="fact",
            )
        ],
        actions=[],
        done=True,
    )
    graph, memory, *_ = _graph([turn])
    from cybotech.agents.contracts import Document

    result = graph.run(
        "Remember", "session", "task", [Document(id="doc-1", content="source", source_type="email")]
    )
    assert result.status == "completed"
    assert len(memory.proposals) == 1
    assert memory.proposals[0].raw_context == "source"


@pytest.mark.parametrize("authorized", [False, True])
def test_tool_proposals_must_pass_sentinel_and_gateway_before_dispatch(authorized):
    turn = AgentTurn(
        reply="Tool proposed",
        memories=[],
        actions=[ModelAction(tool_name="read_email", arguments_json='{"id":"email-1"}')],
        done=True,
    )
    graph, _, sentinel, gateway, dispatcher, events = _graph([turn], authorized=authorized)
    result = graph.run("Read", "session", "task", [])
    assert result.status == "completed"
    assert len(sentinel.actions) == 1
    assert len(gateway.actions) == 1
    assert len(dispatcher.actions) == int(authorized)
    names = [name for name, _ in events.items]
    assert names.index("sentinel_finding") < names.index(
        "gateway_authorized" if authorized else "gateway_denied"
    )


def test_provider_failure_and_pending_approval_terminate_without_dispatch():
    failed, *parts = _graph(TimeoutError("provider unavailable"))
    assert failed.run("Read", "session", "task", []).status == "failed"
    assert parts[3].actions == []

    pending_turn = AgentTurn(
        reply="Awaiting approval",
        memories=[],
        actions=[
            ModelAction(
                tool_name="send_report",
                arguments_json='{"destination":"reports@internal.example","content":"synthetic"}',
            )
        ],
        done=True,
    )
    graph, _, _, _, dispatcher, _ = _graph([pending_turn], authorized=False)
    assert graph.run("Send", "session", "task", []).tool_results == []
    assert dispatcher.actions == []


def test_iteration_limit_terminates_a_nonfinal_model_turn():
    turn = AgentTurn(reply="Continue", memories=[], actions=[], done=False)
    graph, *_ = _graph([turn, turn], max_steps=2)
    result = graph.run("Continue", "session", "task", [])
    assert (result.status, result.steps) == ("step_limit", 2)
