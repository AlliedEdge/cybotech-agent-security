from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from cybotech.agents.contracts import ActionProposal, GatewayDecision
from cybotech.infrastructure.database.models import AuditEvent, PolicyDecision
from cybotech.services.sentinel.service import SentinelFinding


class PolicyGateway:
    """Final deterministic authorization. Configure allowlists in trusted app code."""

    def __init__(
        self,
        db: Session,
        *,
        destinations: set[str] | None = None,
        principals: set[str] | None = None,
        sentinel_required: bool = True,
        policy_version: str = "local-v1",
    ) -> None:
        configured = set(
            filter(
                None, os.getenv("ALLOWED_EMAIL_DESTINATIONS", "reports@internal.example").split(",")
            )
        )
        self.db, self.destinations = db, destinations if destinations is not None else configured
        self.principals, self.sentinel_required, self.policy_version = (
            principals or {"primary_assistant"},
            sentinel_required,
            policy_version,
        )

    @staticmethod
    def action_hash(proposal: ActionProposal) -> str:
        bound = {
            "tool": proposal.tool_name,
            "principal": proposal.proposed_by,
            "arguments": proposal.arguments,
            "policy_scope": "tool-execution",
        }
        return hashlib.sha256(
            json.dumps(bound, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()

    def authorize(
        self,
        proposal: ActionProposal,
        *,
        sentinel_finding: SentinelFinding | None = None,
        approval_id: str | None = None,
    ) -> GatewayDecision:
        failure = self._check(proposal, sentinel_finding, approval_id)
        allowed = failure is None
        action_hash = self.action_hash(proposal)
        decision_id = f"pd_{uuid.uuid4().hex}"
        if allowed and proposal.tool_name == "send_report" and approval_id:
            approval = self.db.get(PolicyDecision, approval_id)
            if approval is None:
                raise RuntimeError("validated approval disappeared before consumption")
            approval.approval_consumed_at = datetime.now(UTC)
        self.db.add(
            PolicyDecision(
                id=decision_id,
                tool_name=proposal.tool_name,
                principal=proposal.proposed_by,
                action_hash=action_hash,
                policy_version=self.policy_version,
                decision="allow" if allowed else "deny",
                reason_codes=[] if allowed else [failure],
            )
        )
        self.db.add(
            AuditEvent(
                event_id=f"evt_{uuid.uuid4().hex}",
                event_type="gateway_decision",
                actor=proposal.proposed_by,
                correlation_id=proposal.task_id,
                object_id=decision_id,
                outcome="allowed" if allowed else "denied",
                reason_code=failure or "policy_permitted",
                payload={
                    "tool": proposal.tool_name,
                    "action_hash": action_hash,
                    "approval_id": approval_id,
                },
            )
        )
        self.db.commit()
        return GatewayDecision(
            authorized=allowed,
            reason="policy_permitted" if allowed else failure or "policy_denied",
            failed_check=failure,
            approval_id=approval_id,
        )

    def _check(
        self, p: ActionProposal, finding: SentinelFinding | None, approval_id: str | None
    ) -> str | None:
        schemas = {
            "send_report": {"destination": str, "content": str},
            "read_email": {"id": str},
            "read_document": {"id": str},
            "search_customers": {"query": str},
        }
        if p.tool_name not in schemas:
            return "unknown_tool"
        if p.proposed_by not in self.principals:
            return "principal_not_allowed"
        for memory_id in p.retrieved_memory_ids:
            try:
                memory_uuid = uuid.UUID(memory_id)
            except (ValueError, TypeError):
                return "memory_reference_unverifiable"
            from cybotech.infrastructure.database.models import Memory, MemoryStatus

            memory = self.db.get(Memory, memory_uuid)
            if (
                memory is None
                or memory.status != MemoryStatus.trusted
                or memory.revoked_at is not None
                or (memory.expires_at is not None and _utc(memory.expires_at) <= datetime.now(UTC))
                or memory.provenance.get("trust_level") not in {"verified", "internal"}
            ):
                return "memory_reference_untrusted"
        schema = schemas[p.tool_name]
        if set(p.arguments) != set(schema) or any(
            not isinstance(p.arguments[k], schema[k]) for k in schema
        ):
            return "invalid_arguments"
        if p.tool_name == "send_report" and p.arguments["destination"] not in self.destinations:
            return "destination_not_approved"
        if self.sentinel_required and (
            finding is None or getattr(finding, "status", None) != "completed"
        ):
            return "sentinel_review_required"
        if p.tool_name == "send_report":
            if not approval_id:
                return "approval_required"
            row = self.db.scalar(
                select(PolicyDecision).where(PolicyDecision.id == approval_id).with_for_update()
            )
            if (
                not row
                or row.approval_status != "approved"
                or row.approval_expires_at is None
                or _utc(row.approval_expires_at) <= datetime.now(UTC)
            ):
                return "approval_missing_or_expired"
            if row.action_hash != self.action_hash(p):
                return "approval_action_mismatch"
            if row.approval_consumed_at is not None:
                return "approval_already_consumed"
        return None

    def create_approval(self, proposal: ActionProposal, actor: str, expires_at: datetime) -> str:
        approval_id = f"ap_{uuid.uuid4().hex}"
        self.db.add(
            PolicyDecision(
                id=approval_id,
                tool_name=proposal.tool_name,
                principal=proposal.proposed_by,
                action_hash=self.action_hash(proposal),
                policy_version=self.policy_version,
                decision="approval_pending",
                reason_codes=[],
                approval_status="pending",
                approval_expires_at=expires_at,
            )
        )
        self.db.add(
            AuditEvent(
                event_id=f"evt_{uuid.uuid4().hex}",
                event_type="approval_requested",
                actor=actor,
                correlation_id=proposal.task_id,
                object_id=approval_id,
                outcome="pending",
                reason_code="sensitive_action",
                payload={"tool": proposal.tool_name},
            )
        )
        self.db.commit()
        return approval_id

    def decide_approval(self, approval_id: str, actor: str, approved: bool) -> str:
        row = self.db.get(PolicyDecision, approval_id)
        if not row or row.approval_status != "pending":
            raise ValueError("pending approval not found")
        if row.approval_expires_at and _utc(row.approval_expires_at) <= datetime.now(UTC):
            row.approval_status = "expired"
        else:
            row.approval_status = "approved" if approved else "denied"
        self.db.add(
            AuditEvent(
                event_id=f"evt_{uuid.uuid4().hex}",
                event_type="approval_decision",
                actor=actor,
                object_id=approval_id,
                outcome=row.approval_status,
                reason_code="human_decision",
                payload={},
            )
        )
        self.db.commit()
        return row.approval_status or "unknown"


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
