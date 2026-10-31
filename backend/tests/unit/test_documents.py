from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from cybotech.infrastructure.database.models import Base, DocumentChunk
from cybotech.services.documents.service import DocumentService, chunk_text


class Embedder:
    model_name = "offline-test-384"

    def embed(self, text):
        return [1.0 if "needle" in text else 0.0] + [0.0] * 383


def test_chunks_are_deterministic_with_overlap():
    text = "x" * 1600
    chunks = chunk_text(text)
    assert [len(item) for item in chunks] == [800, 800, 240]
    assert chunks[0][-120:] == chunks[1][:120]


def test_document_ingestion_scope_deletion_and_untrusted_retrieval():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        service = DocumentService(db, Embedder())
        row = service.ingest("notes.md", "text/markdown", b"# note\nneedle evidence", "session-a")
        assert (
            service.ingest("notes.md", "text/markdown", b"# note\nneedle evidence", "session-a").id
            == row.id
        )
        assert service.retrieve("needle", "session-b") == []
        evidence = service.retrieve("needle", "session-a")
        assert evidence[0].trust_label == "untrusted"
        assert db.query(DocumentChunk).count() == 1
        assert service.delete(row.id, "session-a")
        assert service.retrieve("needle", "session-a") == []
    engine.dispose()
