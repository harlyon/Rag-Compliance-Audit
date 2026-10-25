"""Qdrant collection management and chunk indexing."""
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from compliance_rag import config
from compliance_rag.chunking.splitter import Chunk
from compliance_rag.storage.db import get_conn


def get_qdrant() -> QdrantClient:
    return QdrantClient(url=config.QDRANT_URL)


def ensure_collection() -> None:
    qd = get_qdrant()
    if not qd.collection_exists(config.COLLECTION):
        qd.create_collection(
            collection_name=config.COLLECTION,
            vectors_config=VectorParams(size=config.EMBED_DIM, distance=Distance.COSINE),
        )


def index_chunks(chunks: list[Chunk], batch: int = 64) -> int:
    """Store full text locally in Postgres; embed only REDACTED text."""
    from compliance_rag.indexing.embeddings import embed

    qd, conn = get_qdrant(), get_conn()
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO chunks (chunk_id, doc_id, page, section, chunk_type, text) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (chunk_id) DO NOTHING",
            [(c.chunk_id, c.doc_id, c.page, c.section, c.chunk_type, c.text) for c in chunks],
        )
    conn.commit()

    for i in range(0, len(chunks), batch):
        part = chunks[i:i + batch]
        vectors = embed([c.text for c in part])
        qd.upsert(
            collection_name=config.COLLECTION,
            points=[
                PointStruct(id=c.chunk_id, vector=v, payload=c.metadata())
                for c, v in zip(part, vectors)
            ],
        )
    return len(chunks)
