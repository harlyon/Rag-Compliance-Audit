"""Redaction gate: sensitive data never leaves the host for API calls."""
import re

REDACT_PATTERNS = [
    re.compile(r"\b\d{10,14}\b"),                     # ID / phone numbers
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),       # emails
    re.compile(r"\b(?:ZAR|R)\s?\d+(?:[ ,]\d{3})*(?:\.\d{2})?"),  # ZAR amounts
]
# Person names: spaCy NER in Phase 4; regex covers structural PII for now.


def redact(text: str) -> str:
    out = text
    for pat in REDACT_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out
