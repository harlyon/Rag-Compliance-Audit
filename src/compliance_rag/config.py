"""Central configuration. All tunables live here; nothing else hardcodes them."""
import os
import re

# Storage (env overrides let the same code run locally and inside docker compose)
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
DB_DSN = os.environ.get(
    "DB_DSN", "host=localhost port=5432 dbname=compliance user=compliance password=compliance"
)

# Provider: set LLM_PROVIDER=gemini|openai in .env (only that provider's key is needed).
# Each preset is (embedding model, embedding dim, generation model) as LiteLLM strings.
PROVIDERS = {
    "gemini": ("gemini/gemini-embedding-001", 3072, "gemini/gemini-2.5-flash"),
    "openai": ("openai/text-embedding-3-small", 1536, "openai/gpt-4o-mini"),
}
PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").lower()
if PROVIDER not in PROVIDERS:
    raise ValueError(f"LLM_PROVIDER must be one of {sorted(PROVIDERS)}, got {PROVIDER!r}")
EMBED_MODEL, EMBED_DIM, LLM_MODEL = PROVIDERS[PROVIDER]
# Vector sizes differ per embedding model, so each provider gets its own collection.
COLLECTION = f"doc_chunks_{PROVIDER}"

TEMPERATURE = 0

# Chunking
CHUNK_TARGET = 600
CHUNK_OVERLAP = 150
HEADING_RE = re.compile(
    r"^(?:\d+(\.\d+)*\s+\S.{0,80}|"
    r"(?:clause|section|article|paragraph|item)\s+[\d\w()]+.{0,80})$",
    re.IGNORECASE,
)

# Audit engine
TOP_K_BEFORE_RERANK = 30
TOP_EVIDENCE = 8
QUOTE_FUZZY_THRESHOLD = 0.95
