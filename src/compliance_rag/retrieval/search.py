"""Hybrid retrieval: dense (Qdrant) + BM25 (Postgres), fused with RRF."""
import uuid

from qdrant_client.models import FieldCondition, Filter, MatchValue

from compliance_rag import config
from compliance_rag.indexing.embeddings import embed
from compliance_rag.indexing.store import get_qdrant
from compliance_rag.storage.db import get_conn


def hybrid_search(query: str, top: int, doc_id: str | None = None) -> list[str]:
    """Return top chunk_ids via dense + BM25 fused with Reciprocal Rank Fusion.

    With doc_id, both searches are restricted to that one document.
    """
    vec = embed([query])[0]
    dense = get_qdrant().search(
        collection_name=config.COLLECTION, query_vector=vec,
        query_filter=Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))])
        if doc_id else None,
        limit=config.TOP_K_BEFORE_RERANK, with_payload=True,
    )
    # Qdrant returns dashed UUIDs; Postgres stores chunk_id as uuid.hex
    dense_ids = [uuid.UUID(str(hit.id)).hex for hit in dense]

    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT chunk_id FROM chunks "
            "WHERE to_tsvector('english', text) @@ plainto_tsquery('english', %s) "
            "AND (%s::text IS NULL OR doc_id = %s) "
            "ORDER BY ts_rank(to_tsvector('english', text), plainto_tsquery('english', %s)) "
            "LIMIT %s", (query, doc_id, doc_id, query, config.TOP_K_BEFORE_RERANK),
        )
        sparse_ids = [r[0] for r in cur.fetchall()]

    scores: dict[str, float] = {}
    for rank, cid in enumerate(dense_ids, start=1):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (60 + rank)
    for rank, cid in enumerate(sparse_ids, start=1):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (60 + rank)
    return sorted(scores, key=scores.get, reverse=True)[:top]  # type: ignore[arg-type]


def fetch_chunks(chunk_ids: list[str]) -> list[dict]:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT chunk_id, doc_id, page, section, chunk_type, text "
            "FROM chunks WHERE chunk_id = ANY(%s)", (chunk_ids,),
        )
        cols = ["chunk_id", "doc_id", "page", "section", "chunk_type", "text"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
