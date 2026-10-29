from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from cybotech.agents.assistant import Assistant
from cybotech.agents.contracts import ActionProposal, Document, MemoryProposal
from cybotech.infrastructure.database.models import AuditEvent, Memory, MemoryStatus
from cybotech.infrastructure.database.session import get_db
from cybotech.infrastructure.llm.providers import OpenAIProvider
from cybotech.services.integration.ports import build_persistent_assistant
from cybotech.services.memory_guard.service import MemoryGuard
from cybotech.services.policy_gateway.service import PolicyGateway
from cybotech.services.retrieval.service import OpenAIEmbedder
from cybotech.services.sentinel.service import Sentinel

DbSession = Annotated[Session, Depends(get_db)]


class ProposalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=2000)
    category: str = Field(min_length=1, max_length=80)
    source_type: str = Field(min_length=1, max_length=40)
    source_id: str = Field(min_length=1, max_length=200)
    source_trust: str = "untrusted"
    proposed_by: str = "primary_assistant"
    source_context: str = ""


class ActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_name: str
    arguments: dict[str, Any]
    proposed_by: str = "primary_assistant"
    session_id: str = "local"
    task_id: str = "local"
    user_task_description: str
    retrieved_memory_ids: list[str] = Field(default_factory=list)
    approval_id: str | None = None


class AssistantRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str = Field(min_length=1, max_length=8000)
    session_id: str = Field(min_length=1, max_length=120)
    task_id: str = Field(min_length=1, max_length=120)
    documents: list[Document] = Field(default_factory=list, max_length=50)
    approval_id: str | None = None


class ApprovalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool
    reviewer: str = Field(min_length=1, max_length=120)


class ReviewerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer: str = Field(min_length=1, max_length=120)


def require_local_security_access(request: Request) -> None:
    """Permit security API access only in explicit local-demo mode from loopback.

    This is a development boundary, not user authentication. Production must provide
    an authenticated deployment layer and must not enable ``local_demo`` remotely.
    """
    if os.getenv("CYBOTECH_SECURITY_MODE") != "local_demo":
        raise HTTPException(503, "Security API is disabled without configured authentication")
    client_host = request.client.host if request.client else ""
    test_client = client_host == "testclient" and "PYTEST_CURRENT_TEST" in os.environ
    if client_host not in {"127.0.0.1", "::1", "localhost"} and not test_client:
        raise HTTPException(403, "Local demo security routes accept loopback clients only")


router = APIRouter(dependencies=[Depends(require_local_security_access)])


def serialize_event(event: AuditEvent) -> dict[str, Any]:
    return {
        "id": event.event_id,
        "type": event.event_type,
        "timestamp": event.occurred_at.isoformat() if event.occurred_at else None,
        "actor": event.actor,
        "correlation_id": event.correlation_id,
        "object_id": event.object_id,
        "outcome": event.outcome,
        "reason_code": event.reason_code,
        "payload": event.payload,
    }


@router.post("/memories/proposals")
def propose_memory(body: ProposalIn, db: DbSession) -> dict[str, str]:
    proposal = MemoryProposal(
        content=body.content,
        source_document_id=body.source_id,
        proposed_by=body.proposed_by,
        claimed_category=body.category,
        raw_context=body.source_context,
    )
    trusted_sources = set(filter(None, os.getenv("CYBOTECH_VERIFIED_SOURCE_IDS", "").split(",")))

    def resolver(source_id: str, _context: str) -> dict[str, str]:
        return {
            "source_type": body.source_type,
            "trust_level": "verified" if source_id in trusted_sources else "untrusted",
            "source_id": source_id,
        }

    guard = MemoryGuard(db, resolver)
    # Trust is an application-side decision; caller-supplied trust labels are intentionally ignored.
    result = guard.propose_memory(proposal)
    return {"id": result.proposal_id, "status": result.status, "reason": result.reason}


@router.get("/memories")
def list_memories(
    db: DbSession,
    status: str | None = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    filtered = select(Memory)
    if status:
        try:
            filtered = filtered.where(Memory.status == MemoryStatus(status))
        except ValueError:
            raise HTTPException(422, "Unknown memory status") from None
    if q:
        filtered = filtered.where(Memory.content.ilike(f"%{q}%"))
    total = db.scalar(select(func.count()).select_from(filtered.subquery())) or 0
    stmt = filtered.order_by(desc(Memory.created_at)).offset(offset).limit(limit)
    rows = db.scalars(stmt).all()
    return {"items": [_memory(m) for m in rows], "limit": limit, "offset": offset, "total": total}


@router.get("/memories/quarantined")
def quarantined(db: DbSession) -> dict[str, Any]:
    return list_memories(status="quarantined", db=db, limit=50, offset=0)


@router.get("/memories/{memory_id}")
def get_memory(memory_id: uuid.UUID, db: DbSession) -> dict[str, Any]:
    row = db.get(Memory, memory_id)
    if not row:
        raise HTTPException(404, "Memory not found")
    return _memory(row)


@router.get("/memories/{memory_id}/history")
def memory_history(memory_id: uuid.UUID, db: DbSession) -> dict[str, Any]:
    rows = db.scalars(
        select(AuditEvent).where(AuditEvent.object_id == str(memory_id)).order_by(AuditEvent.id)
    ).all()
    return {"items": [serialize_event(e) for e in rows]}


def _transition(
    memory_id: uuid.UUID, status: MemoryStatus, actor: str, reason: str, db: Session
) -> dict[str, Any]:
    try:
        row = MemoryGuard(db).transition(memory_id, status, actor, reason)
    except LookupError:
        raise HTTPException(404, "Memory not found") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _memory(row)


@router.post("/memories/{memory_id}/approve")
def approve_memory(memory_id: uuid.UUID, body: ApprovalIn, db: DbSession) -> dict[str, Any]:
    if not body.approved:
        raise HTTPException(
            422, "Approval endpoint requires approved=true; use reject to deny memory"
        )
    return _transition(memory_id, MemoryStatus.trusted, body.reviewer, "explicit_local_review", db)


@router.post("/memories/{memory_id}/reject")
def reject_memory(memory_id: uuid.UUID, body: ApprovalIn, db: DbSession) -> dict[str, Any]:
    if body.approved:
        raise HTTPException(422, "Rejection endpoint requires approved=false")
    return _transition(memory_id, MemoryStatus.rejected, body.reviewer, "review_rejected", db)


@router.post("/memories/{memory_id}/revoke")
def revoke_memory(memory_id: uuid.UUID, body: ReviewerIn, db: DbSession) -> dict[str, Any]:
    return _transition(memory_id, MemoryStatus.revoked, body.reviewer, "revoked_by_reviewer", db)


def _memory(m: Memory) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "content": m.content,
        "category": m.category,
        "status": m.status.value,
        "source_type": m.source_type,
        "source_id": m.source_id,
        "provenance": m.provenance,
        "created_by": m.created_by,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "expires_at": m.expires_at.isoformat() if m.expires_at else None,
        "reason": m.decision_reason,
    }


@router.post("/actions/review")
def review_action(body: ActionIn) -> dict[str, Any]:
    finding = Sentinel().review(ActionProposal(**body.model_dump()))
    return finding.model_dump()


@router.post("/actions/authorize")
def authorize_action(body: ActionIn, db: DbSession) -> dict[str, Any]:
    proposal = ActionProposal(
        **body.model_dump(exclude={"approval_id"}), approval_id=body.approval_id
    )
    finding = Sentinel().review(proposal)
    decision = PolicyGateway(db).authorize(
        proposal, sentinel_finding=finding, approval_id=body.approval_id
    )
    return {"finding": finding.model_dump(), "decision": decision.model_dump()}


@router.post("/approvals")
def request_approval(body: ActionIn, db: DbSession) -> dict[str, Any]:
    proposal = ActionProposal(**body.model_dump(exclude={"approval_id"}))
    approval_id = PolicyGateway(db).create_approval(
        proposal, "local_demo", datetime.now(UTC) + timedelta(minutes=15)
    )
    return {"id": approval_id, "status": "pending", "expires_in_seconds": 900}


def create_runtime_assistant(db: Session, documents: list[Document], task_id: str) -> Assistant:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(503, "Assistant model and embedding provider are not configured")
    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    )
    embedder = OpenAIEmbedder(client)
    verified_sources = set(filter(None, os.getenv("CYBOTECH_VERIFIED_SOURCE_IDS", "").split(",")))

    def resolve_source(source_id: str, _context: str) -> dict[str, str]:
        if source_id not in verified_sources:
            return {
                "source_type": "user_supplied",
                "trust_level": "untrusted",
                "source_id": source_id,
            }
        return {
            "source_type": "configured_verified_source",
            "trust_level": "verified",
            "source_id": source_id,
        }

    return build_persistent_assistant(
        db,
        OpenAIProvider(client, os.getenv("OPENAI_MODEL", "gpt-4o-mini")),
        embedder,
        documents,
        resolve_source,
        task_id,
    )


@router.post("/assistant/run")
def run_assistant(body: AssistantRunIn, db: DbSession) -> dict[str, Any]:
    assistant = create_runtime_assistant(db, body.documents, body.task_id)
    result = assistant.run(
        body.task,
        body.session_id,
        body.task_id,
        body.documents,
        approval_id=body.approval_id,
    )
    return dict(result.model_dump(mode="json"))


@router.post("/approvals/{approval_id}/decision")
def decide_approval(approval_id: str, body: ApprovalIn, db: DbSession) -> dict[str, str]:
    try:
        status = PolicyGateway(db).decide_approval(approval_id, body.reviewer, body.approved)
    except ValueError:
        raise HTTPException(404, "Pending approval not found") from None
    return {"id": approval_id, "status": status}


@router.get("/security-events")
def security_events(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    event_type: str | None = None,
) -> dict[str, Any]:
    stmt = select(AuditEvent).order_by(desc(AuditEvent.id)).offset(offset).limit(limit)
    if event_type:
        stmt = stmt.where(AuditEvent.event_type == event_type)
    count_stmt = select(func.count()).select_from(AuditEvent)
    if event_type:
        count_stmt = count_stmt.where(AuditEvent.event_type == event_type)
    return {
        "items": [serialize_event(e) for e in db.scalars(stmt).all()],
        "limit": limit,
        "offset": offset,
        "total": db.scalar(count_stmt) or 0,
    }


@router.get("/dashboard/summary")
def dashboard_summary(db: DbSession) -> dict[str, Any]:
    memory_counts = {
        status.value: count
        for status, count in db.execute(
            select(Memory.status, func.count()).group_by(Memory.status)
        ).all()
    }
    event_counts = {
        (event_type, outcome): count
        for event_type, outcome, count in db.execute(
            select(AuditEvent.event_type, AuditEvent.outcome, func.count()).group_by(
                AuditEvent.event_type, AuditEvent.outcome
            )
        ).all()
    }
    return {
        "memory_counts": memory_counts,
        "event_counts": [
            {"type": event_type, "outcome": outcome, "count": count}
            for (event_type, outcome), count in event_counts.items()
        ],
        "event_total": db.scalar(select(func.count()).select_from(AuditEvent)) or 0,
    }


@router.get("/security-events/{event_id}")
def security_event(event_id: str, db: DbSession) -> dict[str, Any]:
    row = db.scalar(select(AuditEvent).where(AuditEvent.event_id == event_id))
    if not row:
        raise HTTPException(404, "Event not found")
    return serialize_event(row)


@router.get("/events/stream")
async def event_stream(
    request: Request, db: DbSession, last_event_id: str | None = None
) -> StreamingResponse:
    # Replay is read from the persisted source. Polling is bounded; disconnect cancels promptly.
    cursor = request.headers.get("last-event-id") or last_event_id

    return StreamingResponse(
        stream_audit_events(request, db, cursor),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def stream_audit_events(
    request: Request, db: Session, cursor: str | None = None
) -> AsyncIterator[str]:
    """Yield persisted event frames; the database is the replay source of truth."""
    import asyncio

    while not await request.is_disconnected():
        stmt = select(AuditEvent).order_by(AuditEvent.id)
        if cursor:
            current = db.scalar(select(AuditEvent.id).where(AuditEvent.event_id == cursor))
            if current is not None:
                stmt = stmt.where(AuditEvent.id > current)
        rows = db.scalars(stmt.limit(100)).all()
        frames = [
            f"id: {event.event_id}\nevent: {event.event_type}\n"
            f"data: {json.dumps(serialize_event(event))}\n\n"
            for event in rows
        ]
        for frame in frames:
            cursor = frame.split("\n", 1)[0][4:]
            yield frame
        if not frames:
            yield ": keep-alive\n\n"
        await asyncio.sleep(1)
