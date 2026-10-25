"""Verbatim quote validation — the anti-hallucination gate."""
from __future__ import annotations

import difflib
import re

from compliance_rag import config
from compliance_rag.security.redaction import redact

_ELLIPSIS = re.compile(r"\s*(?:\.{3,}|…)\s*")
_TYPOGRAPHY = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def _normalize(text: str) -> str:
    """Ignore differences that never change meaning: case, whitespace, curly quotes, dashes."""
    return " ".join(text.translate(_TYPOGRAPHY).lower().split())


def _best_window(segment: str, text: str, start: int) -> tuple[float, int]:
    """Best similarity of `segment` to a same-length window of text[start:], and where it ends."""
    idx = text.find(segment, start)
    if idx != -1:
        return 1.0, idx + len(segment)
    n, best, end = len(segment), 0.0, start
    for i in range(start, max(start + 1, len(text) - n + 1)):
        matcher = difflib.SequenceMatcher(None, segment, text[i:i + n])
        if matcher.quick_ratio() <= best:
            continue
        score = matcher.ratio()
        if score > best:
            best, end = score, i + n
    return best, end


def _score(quote: str, text: str) -> float:
    # An elided quote ("A ... B") must match each part, in order.
    segments = [s for s in _ELLIPSIS.split(quote) if s]
    pos, worst = 0, 1.0 if segments else 0.0
    for segment in segments:
        score, pos = _best_window(segment, text, pos)
        worst = min(worst, score)
    return worst


def quote_is_verbatim(quote: str, chunk_text: str) -> float:
    """Similarity of the quote to the closest same-length span of the chunk (1.0 = exact).

    Windows match the quote's length, so a dropped or added word ("shall not
    exceed" -> "shall exceed") costs score instead of being diluted by the
    surrounding text. The LLM sees redacted text, so the redacted chunk is
    checked too.
    """
    quote_norm = _normalize(quote)
    if not quote_norm:
        return 0.0
    return max(_score(quote_norm, _normalize(t)) for t in (chunk_text, redact(chunk_text)))


def validate_finding(finding: dict, chunks: list[dict]) -> tuple[dict, list[dict], bool]:
    """Check every citation quote against its stored chunk.

    Returns (finding, citations, validated), where validated means at least one
    quote was verified. Fabricated chunk references and unverifiable quotes are
    dropped. A compliant or non_compliant verdict whose quotes all fail is
    downgraded to missing_evidence. A non_compliant verdict that cites nothing
    is kept as an absence claim (finding["absence"] = True): the required
    clause was not found, so there is nothing to quote.
    """
    by_id = {c["chunk_id"]: c for c in chunks}
    claimed = finding.get("citations") or []
    citations: list[dict] = []
    for cite in claimed:
        chunk = by_id.get(cite.get("chunk_id"))
        if chunk is None:
            continue  # fabricated chunk reference -> dropped
        score = quote_is_verbatim(cite.get("quote", ""), chunk["text"])
        if score >= config.QUOTE_FUZZY_THRESHOLD:
            citations.append({
                "chunk_id": chunk["chunk_id"], "doc_id": chunk["doc_id"],
                "page": chunk["page"], "section": chunk["section"],
                "quote": cite["quote"], "match_score": round(score, 4),
            })

    status = finding["status"]
    if not citations and (status == "compliant" or (status == "non_compliant" and claimed)):
        finding = dict(finding, status="missing_evidence",
                       finding=f"{status.replace('_', ' ').capitalize()} claim could not be "
                               f"verified against evidence. Model said: {finding['finding']}",
                       confidence=min(finding.get("confidence", 0.5), 0.4))
    elif not citations and status == "non_compliant":
        finding = dict(finding, absence=True)
    return finding, citations, bool(citations)
