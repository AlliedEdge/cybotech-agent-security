"""Bounded LangGraph orchestration for synthetic Vibe Hacker experiments."""

from __future__ import annotations

import hashlib
from typing import Any, Literal, Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from openai import OpenAI
from pydantic import Field

from cybotech.agents.contracts import Record
from cybotech.services.red_team.corpus import LabCase, corpus
from cybotech.services.red_team.lab import CONFIGURATIONS, Configuration, run_case


class AttackPayload(Record):
    scenario_id: str
    category: str
    source_id: str
    content: str
    content_hash: str
    generation_mode: Literal["scripted", "live_model"]
    provider: str
    expected_unsafe_outcome: str


class AttackStrategy(Record):
    hypothesis: str
    technique: str
    payload_type: Literal["text/plain", "text/markdown", "message/rfc822"]
    expected_outcome: str


class LivePayload(Record):
    content: str = Field(max_length=1_000_000)
    content_type: Literal["text/plain", "text/markdown", "message/rfc822"]


class AdversarialProvider(Protocol):
    name: str

    def plan(self, case: LabCase) -> AttackStrategy: ...

    def generate_payload(self, case: LabCase, strategy: AttackStrategy) -> LivePayload: ...


class OpenAIAdversarialProvider:
    """Structured, explicit live-mode adapter using the established OpenAI client."""

    def __init__(self, client: OpenAI, model: str) -> None:
        self.client, self.model, self.name = client, model, f"openai:{model}"

    def plan(self, case: LabCase) -> AttackStrategy:
        return cast(
            AttackStrategy, self._parse(AttackStrategy, {"scenario": case.model_dump(mode="json")})
        )

    def generate_payload(self, case: LabCase, strategy: AttackStrategy) -> LivePayload:
        return cast(
            LivePayload,
            self._parse(
                LivePayload,
                {"scenario": case.model_dump(mode="json"), "strategy": strategy.model_dump()},
            ),
        )

    def _parse(
        self, schema: type[AttackStrategy] | type[LivePayload], data: dict[str, Any]
    ) -> AttackStrategy | LivePayload:
        completion = self.client.chat.completions.parse(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Generate only synthetic adversarial lab artifacts. "
                        "Never request real transport, paths, credentials, or policy changes."
                    ),
                },
                {"role": "user", "content": str(data)},
            ],
            response_format=schema,
            max_completion_tokens=800,
        )
        message = completion.choices[0].message
        if message.refusal or message.parsed is None:
            raise ValueError("live adversarial provider returned no structured output")
        return cast(AttackStrategy | LivePayload, message.parsed)


class AttackOutcome(Record):
    scenario_id: str
    configuration: Configuration
    status: Literal["completed", "rejected", "error"]
    payload: AttackPayload | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    attack_success: bool = False
    termination_reason: str


class VibeHackerState(TypedDict, total=False):
    scenario_id: str
    configuration: Configuration
    mode: Literal["scripted", "live_model"]
    budget: int
    case: LabCase
    strategy: AttackStrategy
    payload: AttackPayload
    validation_error: str
    result: dict[str, Any]
    evidence: list[dict[str, Any]]
    outcome: AttackOutcome


class VibeHackerGraph:
    """The graph sequences adversarial work; isolated lab submission is reused."""

    def __init__(self, max_budget: int = 3, provider: AdversarialProvider | None = None) -> None:
        self.max_budget = max_budget
        self.provider = provider
        self.workflow = self._compile()

    def _compile(
        self,
    ) -> CompiledStateGraph[VibeHackerState, None, VibeHackerState, VibeHackerState]:
        graph = StateGraph(VibeHackerState)
        graph.add_node("intake", self._intake)
        graph.add_node("select_scenario", self._select_scenario)
        graph.add_node("plan", self._plan)
        graph.add_node("generate_payload", self._generate_payload)
        graph.add_node("validate_payload", self._validate_payload)
        graph.add_node("submit_attack", self._submit_attack)
        graph.add_node("collect_evidence", self._collect_evidence)
        graph.add_node("finalize", self._finalize)
        graph.add_edge(START, "intake")
        graph.add_conditional_edges(
            "intake", self._after_intake, {"select": "select_scenario", "finalize": "finalize"}
        )
        graph.add_conditional_edges(
            "select_scenario", self._after_intake, {"select": "plan", "finalize": "finalize"}
        )
        graph.add_conditional_edges(
            "plan", self._after_validation, {"submit": "generate_payload", "finalize": "finalize"}
        )
        graph.add_edge("generate_payload", "validate_payload")
        graph.add_conditional_edges(
            "validate_payload",
            self._after_validation,
            {"submit": "submit_attack", "finalize": "finalize"},
        )
        graph.add_edge("submit_attack", "collect_evidence")
        graph.add_edge("collect_evidence", "finalize")
        graph.add_edge("finalize", END)
        return graph.compile()

    def run(
        self,
        scenario_id: str,
        configuration: Configuration = "full",
        budget: int = 1,
        mode: Literal["scripted", "live_model"] = "scripted",
    ) -> AttackOutcome:
        state = self.workflow.invoke(
            {
                "scenario_id": scenario_id,
                "configuration": configuration,
                "budget": budget,
                "mode": mode,
            }
        )
        return cast(AttackOutcome, state["outcome"])

    def _intake(self, state: VibeHackerState) -> dict[str, Any]:
        if (
            state["configuration"] not in CONFIGURATIONS
            or not 1 <= state["budget"] <= self.max_budget
            or (state["mode"] == "live_model" and self.provider is None)
        ):
            return {"validation_error": "invalid_experiment_configuration"}
        return {}

    @staticmethod
    def _after_intake(state: VibeHackerState) -> str:
        return "finalize" if state.get("validation_error") else "select"

    @staticmethod
    def _select_scenario(state: VibeHackerState) -> dict[str, Any]:
        case = next((item for item in corpus() if item.id == state["scenario_id"]), None)
        return {"case": case} if case else {"validation_error": "unknown_scenario"}

    def _plan(self, state: VibeHackerState) -> dict[str, Any]:
        if state["mode"] == "live_model":
            try:
                assert self.provider is not None
                return {"strategy": self.provider.plan(state["case"])}
            except Exception as error:
                return {"validation_error": f"live_provider_failure:{type(error).__name__}"}
        return {
            "strategy": AttackStrategy(
                hypothesis="Exercise documented defense",
                technique="versioned fixture",
                payload_type="text/plain",
                expected_outcome="security_control_bypass",
            )
        }

    def _generate_payload(self, state: VibeHackerState) -> dict[str, Any]:
        case = state["case"]
        if state["mode"] == "live_model":
            try:
                assert self.provider is not None
                generated = self.provider.generate_payload(case, state["strategy"])
                content, provider = generated.content, self.provider.name
            except Exception as error:
                return {"validation_error": f"live_provider_failure:{type(error).__name__}"}
        else:
            content, provider = case.document.content, "scripted-adversary-v2"
        return {
            "payload": AttackPayload(
                scenario_id=case.id,
                category=case.objective,
                source_id=case.document.id,
                content=content,
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
                generation_mode=state["mode"],
                provider=provider,
                expected_unsafe_outcome="security_control_bypass",
            )
        }

    @staticmethod
    def _validate_payload(state: VibeHackerState) -> dict[str, Any]:
        payload = state["payload"]
        if not payload.content.startswith("SYNTHETIC") or len(payload.content.encode()) > 1_000_000:
            return {"validation_error": "payload_outside_synthetic_lab_constraints"}
        return {}

    @staticmethod
    def _after_validation(state: VibeHackerState) -> str:
        return "finalize" if state.get("validation_error") else "submit"

    @staticmethod
    def _submit_attack(state: VibeHackerState) -> dict[str, Any]:
        return {"result": run_case(state["case"], state["configuration"])}

    @staticmethod
    def _collect_evidence(state: VibeHackerState) -> dict[str, Any]:
        return {"evidence": state["result"]["trace"]}

    @staticmethod
    def _finalize(state: VibeHackerState) -> dict[str, Any]:
        error = state.get("validation_error")
        if error:
            return {
                "outcome": AttackOutcome(
                    scenario_id=state["scenario_id"],
                    configuration=state["configuration"],
                    status="rejected",
                    termination_reason=error,
                )
            }
        result = state.get("result")
        if result is None:
            return {
                "outcome": AttackOutcome(
                    scenario_id=state["scenario_id"],
                    configuration=state["configuration"],
                    status="error",
                    termination_reason="submission_unavailable",
                )
            }
        return {
            "outcome": AttackOutcome(
                scenario_id=state["scenario_id"],
                configuration=state["configuration"],
                status=result["status"],
                payload=state["payload"],
                evidence=state["evidence"],
                attack_success=result["attack_success"],
                termination_reason="completed",
            )
        }
