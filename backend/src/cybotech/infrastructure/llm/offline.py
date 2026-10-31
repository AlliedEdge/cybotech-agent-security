"""Local deterministic adapters. Never label their output as live-model evidence."""

import hashlib
import math
import re

from cybotech.agents.contracts import AgentTurn, ModelContext, ModelMemory


class LocalEmbedder:
    model_name = "local-token-hash-384-v1"

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * 384
        for token in re.findall(r"\w+", text.lower()):
            digest = hashlib.sha256(token.encode()).digest()
            vector[int.from_bytes(digest[:4]) % 384] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class LocalAssistantModel:
    name = "offline-extractive-v1"

    def generate(self, context: ModelContext) -> AgentTurn:
        memories = (
            [
                ModelMemory(
                    content=doc.content[:2000],
                    source_document_id=doc.id,
                    claimed_category="source_summary",
                )
                for doc in context.documents[:5]
            ]
            if not context.observations
            else []
        )
        text = "\n".join(doc.content[:600] for doc in context.documents[:5])
        evidence = "\n".join(item.content[:600] for item in context.retrieved_documents[:5])
        remembered = "\n".join(memory.content[:600] for memory in context.memories)
        return AgentTurn(
            reply="Offline extractive assistant (no live model).\n"
            + (text or evidence or remembered or "Provide a synthetic document to summarize."),
            memories=memories,
            actions=[],
            done=True,
        )
