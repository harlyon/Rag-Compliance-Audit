"""Document parsing: PDF / DOCX / TXT into ParsedDoc with provenance pages."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
from docx import Document as DocxDocument
from docx.text.paragraph import Paragraph
from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@dataclass
class ParsedDoc:
    doc_id: str
    filename: str
    domain: str              # legal | financial | employment
    pages: list[tuple[int, str]] = field(default_factory=list)
    tables: list[tuple[int, str]] = field(default_factory=list)
    ocr_used: bool = False
    # Lines known to be headings from document structure (e.g. Word styles),
    # used alongside config.HEADING_RE when chunking.
    headings: set[str] = field(default_factory=set)


def new_doc_id(path: Path) -> str:
    """Content hash: the same file always gets the same id, different files never share one."""
    return "doc_" + hashlib.sha1(path.read_bytes()).hexdigest()[:12]


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


def _has_numbering(p: Paragraph) -> bool:
    """Auto-numbered by Word, directly or through its style (the number itself isn't in p.text)."""
    for ppr in (p._p.pPr, p.style.element.pPr if p.style is not None else None):
        if ppr is not None and ppr.numPr is not None:
            return True
    return False


def _is_bold(p: Paragraph) -> bool:
    runs = [r for r in p.runs if r.text.strip()]
    style_bold = bool(p.style is not None and p.style.font.bold)
    return bool(runs) and all(r.bold or (r.bold is None and style_bold) for r in runs)


def _is_docx_heading(p: Paragraph) -> bool:
    """Word marks structure with styles and numbering, not typed '7.2' prefixes.

    A heading is a Heading/Title-styled paragraph, or a short title-like line
    (no closing punctuation) that is auto-numbered or entirely bold.
    """
    text = p.text.strip()
    if not text or len(text) > 100:
        return False
    style = (p.style.name or "") if p.style is not None else ""
    if style.startswith("Heading") or style == "Title":
        return True
    return not text.endswith((".", ";", ",", ":")) and (_has_numbering(p) or _is_bold(p))


def note_lines(xml: bytes, tag: str) -> list[str]:
    """Text of each real footnote/endnote in footnotes.xml or endnotes.xml, as '[id] text'."""
    lines = []
    for note in etree.fromstring(xml).iter(f"{W}{tag}"):
        if note.get(f"{W}type"):  # separator / continuationSeparator, not content
            continue
        text = " ".join("".join(t.text or "" for t in note.iter(f"{W}t")).split())
        if text:
            lines.append(f"[{note.get(f'{W}id')}] {text}")
    return lines


def parse_docx(path: Path) -> ParsedDoc:
    doc = DocxDocument(str(path))
    lines, headings = [], set()
    for p in doc.paragraphs:
        if text := p.text.strip():
            lines.append(text)
            if _is_docx_heading(p):
                headings.add(text)

    # Content that lives outside the body paragraphs gets its own section.
    extras: list[tuple[str, list[str]]] = []
    package = doc.part.package
    parts = {str(part.partname): part for part in package.iter_parts()} if package else {}
    for partname, tag, title in (("/word/footnotes.xml", "footnote", "Footnotes"),
                                 ("/word/endnotes.xml", "endnote", "Endnotes")):
        if partname in parts:
            extras.append((title, note_lines(parts[partname].blob, tag)))
    boxes = (" ".join("".join(t.text or "" for t in bp.iter(f"{W}t")).split())
             for bp in doc.element.body.iter(f"{W}txbxContent"))
    extras.append(("Text boxes", list(dict.fromkeys(b for b in boxes if b))))  # dedupe fallbacks
    header_footer = (hp.text.strip() for s in doc.sections for hf in (s.header, s.footer)
                     if not hf.is_linked_to_previous for hp in hf.paragraphs)
    extras.append(("Headers and footers", list(dict.fromkeys(h for h in header_footer if h))))
    for title, extra in extras:
        if extra:
            lines.extend([title, *extra])
            headings.add(title)

    text = "\n".join(lines)
    tables = [
        (1, "\n".join(" | ".join(c.text for c in row.cells) for row in t.rows))
        for t in doc.tables
    ]
    return ParsedDoc(new_doc_id(path), path.name, classify(path),
                     [(1, text)] if text else [], tables, headings=headings)


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
