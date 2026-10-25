"""Audit runner: rule pack -> retrieval -> LLM finding -> validation -> Postgres."""
from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path
from typing import Callable

from compliance_rag import config
from compliance_rag.audit.llm import generate_finding
from compliance_rag.audit.rules import Rule, load_rules
from compliance_rag.audit.validate import validate_finding
from compliance_rag.retrieval.search import fetch_chunks, hybrid_search
from compliance_rag.storage.db import ensure_schema, get_conn

ProgressFn = Callable[[Rule, str, str], None]  # (rule, event, detail)


def run_audit(pack_path: Path, domain: str | None = None,
              progress: ProgressFn | None = None,
              doc_id: str | None = None) -> tuple[str, dict]:
    """Run every rule in the pack against one document (or the whole corpus if
    doc_id is None). Returns (audit_id, status summary)."""
    def emit(rule, event, detail=""):
        if progress:
            progress(rule, event, detail)

    pack_id, version, rules = load_rules(pack_path)
    rules = [r for r in rules if domain is None or r.domain == domain]
    audit_id = "audit_" + uuid.uuid4().hex[:10]

    ensure_schema()
    conn = get_conn()
    _persist_pack_and_audit(conn, pack_id, version, rules, audit_id, doc_id)

    summary = {"compliant": 0, "non_compliant": 0, "missing_evidence": 0,
               "not_applicable": 0, "dropped": 0}

    for rule in rules:
        chunk_ids: list[str] = []
        for hint in rule.retrieval_hints:
            chunk_ids.extend(hybrid_search(hint, config.TOP_EVIDENCE, doc_id))
        chunk_ids = list(dict.fromkeys(chunk_ids))[:config.TOP_EVIDENCE]
        chunks = fetch_chunks(chunk_ids)

        try:
            raw = generate_finding(rule, chunks)
            finding, citations, _ = validate_finding(raw, chunks)
            if finding.get("absence"):
                searched = "; ".join(f'"{h}"' for h in rule.retrieval_hints)
                finding = dict(finding, finding=f"{finding['finding']} "
                                                f"[Absence claim: no clause found. Searched: {searched}]")
            summary[finding["status"]] = summary.get(finding["status"], 0) + 1
            _persist_finding(conn, audit_id, rule, finding, citations)
            emit(rule, finding["status"],
                 "clause not found" if finding.get("absence") else f"{len(citations)} citations")
        except Exception as exc:  # noqa: BLE001 — one bad rule must not kill the run
            summary["dropped"] += 1
            emit(rule, "dropped", str(exc))

    with conn.cursor() as cur:
        cur.execute("UPDATE audits SET finished_at = now() WHERE audit_id = %s", (audit_id,))
    conn.commit()
    conn.close()
    return audit_id, summary


def _persist_pack_and_audit(conn, pack_id, version, rules, audit_id, doc_id) -> None:
    with conn.cursor() as cur:
        for r in rules:
            cur.execute(
                "INSERT INTO rules (rule_id, pack_id, version, domain, description, severity, evidence_required) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (rule_id) DO UPDATE SET "
                "description=EXCLUDED.description, severity=EXCLUDED.severity",
                (r.rule_id, pack_id, version, r.domain, r.description, r.severity, r.evidence_required),
            )
        cur.execute(
            "INSERT INTO audits (audit_id, pack_id, pack_version, doc_id, rule_count, started_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (audit_id, pack_id, version, doc_id, len(rules), dt.datetime.now(dt.timezone.utc)),
        )
    conn.commit()


def _persist_finding(conn, audit_id, rule, finding, citations) -> None:
    finding_id = "find_" + uuid.uuid4().hex[:10]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO findings (finding_id, audit_id, rule_id, status, severity, text, confidence, validated) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (finding_id, audit_id, rule.rule_id, finding["status"],
             finding.get("severity", rule.severity), finding["finding"],
             float(finding.get("confidence", 0.5)), bool(citations)),
        )
        for cite in citations:
            cur.execute(
                "INSERT INTO citations (citation_id, finding_id, chunk_id, doc_id, page, section, quote, match_score) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                ("cite_" + uuid.uuid4().hex[:10], finding_id, cite["chunk_id"],
                 cite["doc_id"], cite["page"], cite["section"], cite["quote"],
                 cite["match_score"]),
            )
    conn.commit()
