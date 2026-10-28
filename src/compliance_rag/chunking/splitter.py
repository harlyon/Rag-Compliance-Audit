"""Structure-aware chunking: heading-delimited clause chunks, atomic tables."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Iterator

from compliance_rag import config
from compliance_rag.ingestion.parsers import ParsedDoc


@dataclass
class Chunk:
    doc_id: str
    page: int
    section: str
    chunk_type: str          # clause | table | narrative
    text: str
    chunk_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def metadata(self) -> dict:
        return {"chunk_id": self.chunk_id, "doc_id": self.doc_id, "page": self.page,
                "section": self.section, "chunk_type": self.chunk_type}


def _sliding_window(text: str, size: int, overlap: int) -> Iterator[str]:
    step = size - overlap
    for start in range(0, len(text), step):
        yield text[start:start + size]
        if start + size >= len(text):
            return


def split_page(page_text: str, page: int, doc: ParsedDoc) -> Iterator[Chunk]:
    lines = page_text.splitlines()
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in lines:
        if line.strip() in doc.headings or config.HEADING_RE.match(line.strip()):
            sections.append((line.strip(), []))
        else:
            sections[-1][1].append(line)

    for heading, body in sections:
        text = " ".join(" ".join(body).split())
        if not text:
            continue
        if len(text) <= config.CHUNK_TARGET:
            yield Chunk(doc.doc_id, page, heading or "(untitled)",
                        "clause" if heading else "narrative", text)
        else:
            for part in _sliding_window(text, config.CHUNK_TARGET, config.CHUNK_OVERLAP):
                yield Chunk(doc.doc_id, page, heading or "(untitled)",
                            "clause" if heading else "narrative", part)


def chunk_doc(doc: ParsedDoc) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page, text in doc.pages:
        chunks.extend(split_page(text, page, doc))
    for page, table_text in doc.tables:
        chunks.append(Chunk(doc.doc_id, page, "(table)", "table", table_text))
    return chunks
