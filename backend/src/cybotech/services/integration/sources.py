"""Operator-owned canonical synthetic sources; request IDs never grant trust."""

import hashlib
import json
import os
from pathlib import Path

from cybotech.agents.contracts import Document


def registered_sources() -> list[Document]:
    path = os.getenv("CYBOTECH_SOURCE_REGISTRY")
    if path:
        records = json.loads(Path(path).read_text(encoding="utf-8"))
        return [Document.model_validate(record) for record in records]
    return [
        Document(
            id="internal-maintenance-v1",
            source_type="internal",
            trust_level="verified",
            content="SYNTHETIC: Vendor maintenance is Friday at 18:00 UTC.",
        )
    ]


def resolve_registered_source(source_id: str, content: str) -> dict[str, str]:
    for document in registered_sources():
        if document.id == source_id and document.content == content:
            return {
                "source_id": source_id,
                "source_type": document.source_type,
                "trust_level": document.trust_level,
                "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            }
    return {"source_id": source_id, "source_type": "user_supplied", "trust_level": "untrusted"}
