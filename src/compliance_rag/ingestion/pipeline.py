"""Ingest one file: parse -> chunk -> dual write (Postgres text, Qdrant vectors)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue

from compliance_rag import config
from compliance_rag.chunking.splitter import chunk_doc
from compliance_rag.indexing.store import ensure_collection, get_qdrant, index_chunks
from compliance_rag.ingestion.parsers import ParsedDoc, parse_any
from compliance_rag.storage.db import ensure_schema, get_conn

SUPPORTED = (".pdf", ".docx", ".txt", ".md")


@dataclass
class IngestResult:
    path: Path
    doc: ParsedDoc | None
    status: str              # indexed | already_indexed | unreadable
    chunks: int = 0


def ingest_file(path: Path) -> IngestResult:
    """Idempotent: doc_id is a content hash, so a file already indexed is skipped.

    A failure part-way through removes the partial document, so a retry starts clean.
    """
    ensure_schema()
    ensure_collection()
    doc = parse_any(path)
    if doc is None or (not doc.pages and not doc.tables):
        return IngestResult(path, doc, "unreadable")

    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM documents WHERE doc_id = %s", (doc.doc_id,)).fetchone():
            return IngestResult(path, doc, "already_indexed")
        conn.execute(
            "INSERT INTO documents (doc_id, filename, domain, ocr_used) VALUES (%s,%s,%s,%s)",
            (doc.doc_id, doc.filename, doc.domain, doc.ocr_used),
        )
    try:
        n = index_chunks(chunk_doc(doc))
    except Exception:
        remove_document(doc.doc_id)
        raise
    return IngestResult(path, doc, "indexed", n)


def remove_document(doc_id: str) -> None:
    """Delete a document, its audits and any citations of its text, from both stores."""
    with get_conn() as conn:
        # citations -> chunks doesn't cascade, so clear citations (including ones
        # from corpus-wide audits) before chunks go; audits cascade to findings.
        conn.execute("DELETE FROM citations WHERE chunk_id IN "
                     "(SELECT chunk_id FROM chunks WHERE doc_id = %s)", (doc_id,))
        conn.execute("DELETE FROM audits WHERE doc_id = %s", (doc_id,))
        conn.execute("DELETE FROM documents WHERE doc_id = %s", (doc_id,))  # cascades to chunks
    get_qdrant().delete(
        collection_name=config.COLLECTION,
        points_selector=FilterSelector(filter=Filter(
            must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))])),
    )
