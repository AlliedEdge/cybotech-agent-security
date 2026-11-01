"""Offline wiring demonstration. Doubles below are NOT production defenses.

Run: python -m cybotech.services.red_team.demo
"""

import json
from functools import partial
from typing import Any, Literal

from cybotech.agents.assistant import Assistant
from cybotech.agents.contracts import (
    ActionProposal,
    AgentTurn,
    GatewayDecision,
    MemoryDecision,
    MemoryProposal,
    MemoryRecord,
    ModelAction,
    ModelContext,
    ModelMemory,
    RetrievalQuery,
)
from cybotech.infrastructure.sandbox.tools import SandboxDispatcher
from cybotech.services.red_team.runner import ExperimentEnvironment, ExperimentRunner
from cybotech.services.red_team.scenarios import AttackCase, VibeHacker


class DemoServices:
    """In-memory doubles only. Dev B supplies real policy, retrieval and audit adapters."""

    def __init__(self, protected: bool) -> None:
        self.protected = protected
        self.records: list[MemoryRecord] = []
        self.events: list[tuple[str, dict[str, Any]]] = []

    def propose_memory(self, proposal: MemoryProposal) -> MemoryDecision:
        # Intentionally simplistic; this demonstrates port wiring, not detection quality.
        status: Literal["quarantined", "trusted"] = "quarantined" if self.protected else "trusted"
        if status == "trusted":
            self.records.append(
                MemoryRecord(
                    id=f"memory-{len(self.records)}",
                    content=proposal.content,
                    trust_label="trusted",
                    source_type="email",
                )
            )
        return MemoryDecision(
            proposal_id="demo-proposal", status=status, reason="Scripted demonstration decision"
        )

    def retrieve(self, query: RetrievalQuery) -> list[MemoryRecord]:
        return self.records[: query.max_results]

    def review(self, proposal: ActionProposal) -> dict[str, Any]:
        return {"recommendation": "PROCEED", "demo_only": True}

    def authorize(
        self, proposal: ActionProposal, *, sentinel_finding: object | None = None
    ) -> GatewayDecision:
        allowed = not self.protected or proposal.arguments.get("destination") == (
            "reports@internal.example"
        )
        return GatewayDecision(
            authorized=allowed,
            reason="Demo policy double",
            failed_check=None if allowed else "destination_not_allowed",
        )

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((event_type, payload))


class VulnerableDemoModel:
    """Explicitly scripted attacker compliance. No claim about real-model behavior."""

    name = "scripted-vulnerable-offline"

    def __init__(self, case: AttackCase) -> None:
        self.case = case

    def generate(self, context: ModelContext) -> AgentTurn:
        if context.documents:
            return AgentTurn(
                reply="Scripted ingestion",
                memories=[
                    ModelMemory(
                        content=f"Reporting destination: {self.case.target}",
                        source_document_id=self.case.document.id,
                        claimed_category="reporting_destination",
                    )
                ],
                actions=[],
                done=True,
            )
        destination = "reports@internal.example"
        if context.memories:
            destination = context.memories[0].content.removeprefix("Reporting destination: ")
        return AgentTurn(
            reply="Scripted action proposal",
            memories=[],
            actions=[
                ModelAction(
                    tool_name="send_report",
                    arguments_json=json.dumps(
                        {
                            "destination": destination,
                            "content": "SYNTHETIC weekly customer report",
                        }
                    ),
                )
            ],
            done=True,
        )


def make_environment(case: AttackCase, protected: bool) -> ExperimentEnvironment:
    services = DemoServices(protected)
    sandbox = SandboxDispatcher([case.document])
    assistant = Assistant(
        VulnerableDemoModel(case), services, services, services, services, sandbox, services
    )
    return ExperimentEnvironment(
        assistant, sandbox, lambda: [record.content for record in services.records]
    )


def main() -> None:
    cases = VibeHacker().generate(variants_per_objective=1)
    rows = []
    for protected in (False, True):
        rows.extend(
            ExperimentRunner().run(
                cases,
                partial(make_environment, protected=protected),
                "demo_protected" if protected else "demo_baseline",
                "scripted",
            )
        )
    print(
        json.dumps(
            {
                "warning": "Scripted wiring demo; not a security benchmark",
                "results": [row.model_dump() for row in rows],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
