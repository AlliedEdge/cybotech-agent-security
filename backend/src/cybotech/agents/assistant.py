"""Bounded orchestration: retrieve, propose, review, authorize, then dispatch."""

import json

from cybotech.agents.contracts import (
    ActionProposal,
    DispatcherPort,
    Document,
    EventPort,
    GatewayDecision,
    GatewayPort,
    MemoryPort,
    MemoryProposal,
    ModelContext,
    ModelProvider,
    Record,
    RetrievalPort,
    RetrievalQuery,
    SentinelPort,
    ToolResult,
)


class TaskResult(Record):
    status: str
    reply: str
    steps: int
    tool_results: list[ToolResult]


class Assistant:
    def __init__(
        self,
        model: ModelProvider,
        memory: MemoryPort,
        retrieval: RetrievalPort,
        sentinel: SentinelPort,
        gateway: GatewayPort,
        dispatcher: DispatcherPort,
        events: EventPort,
        max_steps: int = 6,
    ) -> None:
        if not 1 <= max_steps <= 20:
            raise ValueError("max_steps must be between 1 and 20")
        self.model = model
        self.memory = memory
        self.retrieval = retrieval
        self.sentinel = sentinel
        self.gateway = gateway
        self.dispatcher = dispatcher
        self.events = events
        self.max_steps = max_steps

    def run(
        self, task: str, session_id: str, task_id: str, documents: list[Document]
    ) -> TaskResult:
        """Failures abort the task; no provider/service error enables a bypass."""
        observations: list[str] = []
        results: list[ToolResult] = []
        reply = ""
        step = 0
        try:
            sources = {doc.id: doc for doc in documents}
            if len(sources) != len(documents):
                raise ValueError("duplicate document IDs")
            memories = self.retrieval.retrieve(
                RetrievalQuery(query_text=task, session_id=session_id)
            )
            self.events.emit("retrieval_completed", {"count": len(memories)})
            for step in range(1, self.max_steps + 1):
                turn = self.model.generate(
                    ModelContext(
                        user_task=task,
                        documents=list(sources.values()),
                        memories=memories,
                        observations=observations,
                    )
                )
                reply = turn.reply
                for candidate in turn.memories:
                    source = sources.get(candidate.source_document_id)
                    if source is None:
                        observations.append("Memory rejected: unknown source_document_id")
                        self.events.emit("memory_proposal_invalid", {"reason": "unknown_source"})
                        continue
                    proposal = MemoryProposal(
                        content=candidate.content,
                        source_document_id=source.id,
                        proposed_by="primary_assistant",
                        claimed_category=candidate.claimed_category,
                        raw_context=source.content,
                    )
                    memory_decision = self.memory.propose_memory(proposal)
                    self.events.emit("memory_decision", {"status": memory_decision.status})
                    observations.append(f"Memory decision: {memory_decision.status}")
                for action_candidate in turn.actions:
                    arguments = json.loads(action_candidate.arguments_json)
                    if not isinstance(arguments, dict):
                        raise ValueError("tool arguments must be a JSON object")
                    action = ActionProposal(
                        tool_name=action_candidate.tool_name,
                        arguments=arguments,
                        proposed_by="primary_assistant",
                        session_id=session_id,
                        task_id=task_id,
                        user_task_description=task,
                        retrieved_memory_ids=[m.id for m in memories],
                    )
                    self.events.emit("action_proposed", {"tool_name": action.tool_name})
                    review_available = True
                    try:
                        finding = self.sentinel.review(action)
                    except Exception:
                        review_available = False
                        finding = {"recommendation": "UNAVAILABLE"}
                    self.events.emit("sentinel_finding", finding)
                    # Always invoke independent authorization, including on reviewer failure.
                    decision = self.gateway.authorize(action)
                    if not review_available:
                        decision = GatewayDecision(
                            authorized=False,
                            reason="Required reviewer unavailable",
                            failed_check="review_unavailable",
                        )
                    self.events.emit(
                        "gateway_authorized" if decision.authorized else "gateway_denied",
                        {"tool_name": action.tool_name, "failed_check": decision.failed_check},
                    )
                    if not decision.authorized:
                        observations.append(f"Action denied: {decision.failed_check}")
                        continue
                    result = self.dispatcher.execute(action)
                    results.append(result)
                    self.events.emit(
                        "tool_execution_completed",
                        {
                            "tool_name": result.tool_name,
                            "success": result.success,
                        },
                    )
                    observations.append(result.model_dump_json())
                    # Only actual read results can introduce additional source documents.
                    if (
                        result.success
                        and result.result
                        and action.tool_name
                        in {
                            "read_email",
                            "read_document",
                        }
                    ):
                        source = Document.model_validate(result.result["document"])
                        if source.id in sources and source != sources[source.id]:
                            raise ValueError("source identity collision")
                        sources[source.id] = source
                if turn.done:
                    return TaskResult(
                        status="completed",
                        reply=reply,
                        steps=step,
                        tool_results=results,
                    )
            return TaskResult(
                status="step_limit",
                reply=reply,
                steps=step,
                tool_results=results,
            )
        except Exception as error:
            # Exception messages may contain raw provider output or credentials.
            self.events.emit("task_failed", {"error_type": type(error).__name__})
            return TaskResult(
                status="failed", reply="Task failed safely", steps=step, tool_results=results
            )
