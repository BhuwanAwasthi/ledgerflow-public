"""Read access to a document's renders (page PNGs + text layer) written by the intake agent."""

from __future__ import annotations

from app import pdf
from app.models import Document
from app.storage import BASELINE_PREFIX, Storage


def render_root(doc: Document) -> str:
    """Where a document's renders live: next to a shared baseline file, or in the owner's storage."""
    return BASELINE_PREFIX if doc.storage_key.startswith(BASELINE_PREFIX) else ""


def is_baseline(doc: Document) -> bool:
    return bool(render_root(doc))


def png_key(doc: Document, page_no: int) -> str:
    return pdf.page_png_key(doc.sha256, page_no, render_root(doc))


def page_range(doc: Document) -> list[int]:
    """1-based pages this document row covers (a classifier segment may cover part of a file)."""
    last = doc.pages or 1
    first = doc.page_from or 1
    return list(range(first, min(doc.page_to or last, last) + 1))


def page_pngs(storage: Storage, doc: Document, pages: list[int]) -> list[bytes]:
    return [storage.get(png_key(doc, n)) for n in pages]


def page_texts(storage: Storage, doc: Document) -> list[str]:
    """Text layer of every rendered page of the file (index 0 = page 1)."""
    return pdf.load_texts(storage.get(pdf.text_key(doc.sha256, render_root(doc))))


def text_block(texts: list[str], pages: list[int], max_chars: int) -> str:
    """The text layer of `pages` with page markers, truncated to `max_chars`."""
    parts = [f"--- page {n} ---\n{pdf.squash_lines(texts[n - 1])}" for n in pages if 0 < n <= len(texts)]
    return "\n".join(parts)[:max_chars]


def segment_of(
    doc: Document, *, case_id: int, label: str, confidence: float, page_from: int, page_to: int
) -> Document:
    """A new document row for a page range of an already stored file (no copy of the bytes)."""
    return Document(
        case_id=case_id,
        filename=doc.filename,
        mime=doc.mime,
        sha256=doc.sha256,
        storage_key=doc.storage_key,
        pages=doc.pages,
        has_text_layer=doc.has_text_layer,
        label=label,
        label_confidence=confidence,
        page_from=page_from,
        page_to=page_to,
    )
