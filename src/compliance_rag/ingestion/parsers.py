"""Document parsing: PDF / DOCX / TXT into ParsedDoc with provenance pages."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
from docx import Document as DocxDocument


@dataclass
class ParsedDoc:
    doc_id: str
    filename: str
    domain: str              # legal | financial | employment
    pages: list[tuple[int, str]] = field(default_factory=list)
    tables: list[tuple[int, str]] = field(default_factory=list)
    ocr_used: bool = False


def new_doc_id(path: Path) -> str:
    return "doc_" + hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:8]


def classify(path: Path) -> str:
    """Keyword-based domain routing; swap for embedding classifier in Phase 4."""
    name = path.name.lower()
    if any(k in name for k in ("employment", "contract", "hr", "payslip", "discipline")):
        return "employment"
    if any(k in name for k in ("invoice", "fin", "audit", "statement", "budget", "tax")):
        return "financial"
    return "legal"


def parse_pdf(path: Path) -> ParsedDoc:
    pages, tables = [], []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append((i, text))
            for tbl in page.extract_tables() or []:
                rows = [" | ".join(str(c) if c else "" for c in r) for r in tbl]
                if rows:
                    tables.append((i, "\n".join(rows)))
    return ParsedDoc(new_doc_id(path), path.name, classify(path),
                     pages, tables, ocr_used=not pages and not tables)


def parse_docx(path: Path) -> ParsedDoc:
    doc = DocxDocument(path)
    text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    tables = [
        (1, "\n".join(" | ".join(c.text for c in row.cells) for row in t.rows))
        for t in doc.tables
    ]
    return ParsedDoc(new_doc_id(path), path.name, classify(path),
                     [(1, text)] if text else [], tables)


def parse_txt(path: Path) -> ParsedDoc:
    return ParsedDoc(new_doc_id(path), path.name, classify(path),
                     [(1, path.read_text(encoding="utf-8", errors="replace"))])


def parse_any(path: Path) -> ParsedDoc | None:
    if path.suffix.lower() == ".pdf":
        return parse_pdf(path)
    if path.suffix.lower() == ".docx":
        return parse_docx(path)
    if path.suffix.lower() in (".txt", ".md"):
        return parse_txt(path)
    return None  # XLSX / OCR scans: Phase 4
