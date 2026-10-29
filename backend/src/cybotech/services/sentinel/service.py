from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from cybotech.agents.contracts import ActionProposal


class SentinelFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = "completed"
    risk: str
    rationale: str = Field(max_length=500)
    evidence_refs: list[str] = Field(default_factory=list, max_length=10)
    disposition: str
    provider: str = "deterministic"


class Sentinel:
    """Bounded advisory reviewer; its findings never grant authorization."""

    def review(self, proposal: ActionProposal) -> SentinelFinding:
        args = proposal.arguments
        destination = str(args.get("destination", ""))
        untrusted_refs = [
            ref
            for ref in proposal.retrieved_memory_ids
            if ref.startswith(("quarantined:", "untrusted:"))
        ]
        if untrusted_refs or re.search(r"attacker|external", destination, re.I):
            return SentinelFinding(
                risk="high",
                rationale="Action references untrusted evidence or a suspicious destination.",
                evidence_refs=untrusted_refs,
                disposition="flag_for_denial",
            )
        task_words = set(re.findall(r"[a-z]{4,}", proposal.user_task_description.lower()))
        action_words = set(re.findall(r"[a-z]{4,}", proposal.tool_name.lower()))
        if (
            action_words
            and not (task_words & action_words)
            and proposal.tool_name in {"send_report"}
        ):
            return SentinelFinding(
                risk="medium",
                rationale="Requested operation may not match the stated task.",
                disposition="additional_review",
            )
        return SentinelFinding(
            risk="low",
            rationale="No advisory indicator detected; deterministic gateway checks still apply.",
            disposition="gateway_evaluation",
        )
