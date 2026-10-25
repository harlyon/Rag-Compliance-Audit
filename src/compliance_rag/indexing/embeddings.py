"""Embeddings via LiteLLM, with the redaction gate applied first."""
from litellm import embedding

from compliance_rag import config
from compliance_rag.security.redaction import redact


def embed(texts: list[str], raw: bool = False) -> list[list[float]]:
    """Embed via LiteLLM. Redacts first unless raw=True (never use for API)."""
    payload = texts if raw else [redact(t) for t in texts]
    resp = embedding(model=config.EMBED_MODEL, input=payload)
    return [d["embedding"] for d in resp["data"]]
