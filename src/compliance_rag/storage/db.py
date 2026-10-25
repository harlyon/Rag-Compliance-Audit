"""Postgres access and schema (documents/chunks + audit tables)."""
import psycopg

from compliance_rag import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id      TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    domain      TEXT NOT NULL,
    ocr_used    BOOLEAN NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id    TEXT PRIMARY KEY,
    doc_id      TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    page        INT NOT NULL,
    section     TEXT NOT NULL,
    chunk_type  TEXT NOT NULL,
    text        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_text_idx ON chunks USING gin (to_tsvector('english', text));
CREATE INDEX IF NOT EXISTS chunks_doc_idx ON chunks (doc_id);
"""

AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS rules (
    rule_id     TEXT PRIMARY KEY,
    pack_id     TEXT NOT NULL,
    version     TEXT NOT NULL,
    domain      TEXT NOT NULL,
    description TEXT NOT NULL,
    severity    TEXT NOT NULL,
    evidence_required BOOLEAN NOT NULL
);
CREATE TABLE IF NOT EXISTS audits (
    audit_id    TEXT PRIMARY KEY,
    pack_id     TEXT NOT NULL,
    pack_version TEXT NOT NULL,
    rule_count  INT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ
);
-- NULL doc_id = audit ran across the whole corpus
ALTER TABLE audits ADD COLUMN IF NOT EXISTS doc_id TEXT REFERENCES documents(doc_id) ON DELETE CASCADE;
CREATE TABLE IF NOT EXISTS findings (
    finding_id  TEXT PRIMARY KEY,
    audit_id    TEXT NOT NULL REFERENCES audits(audit_id) ON DELETE CASCADE,
    rule_id     TEXT NOT NULL REFERENCES rules(rule_id),
    status      TEXT NOT NULL,
    severity    TEXT NOT NULL,
    text        TEXT NOT NULL,
    confidence  REAL NOT NULL,
    validated   BOOLEAN NOT NULL
);
CREATE TABLE IF NOT EXISTS citations (
    citation_id TEXT PRIMARY KEY,
    finding_id  TEXT NOT NULL REFERENCES findings(finding_id) ON DELETE CASCADE,
    chunk_id    TEXT NOT NULL REFERENCES chunks(chunk_id),
    doc_id      TEXT NOT NULL,
    page        INT NOT NULL,
    section     TEXT NOT NULL,
    quote       TEXT NOT NULL,
    match_score REAL NOT NULL
);
"""


def get_conn() -> psycopg.Connection:
    return psycopg.connect(config.DB_DSN)


def ensure_schema() -> None:
    with get_conn() as cxn, cxn.cursor() as cur:
        cur.execute(SCHEMA)
        cur.execute(AUDIT_SCHEMA)
