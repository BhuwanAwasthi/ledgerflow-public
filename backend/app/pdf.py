"""PDF helpers (PyMuPDF): format sniffing, image/DOCX -> PDF, page rendering, text layer, value location.

Pure functions over bytes; agents call them through asyncio.to_thread because rendering is CPU-bound.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree

import pymupdf

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def sniff(data: bytes, filename: str) -> str | None:
    """'pdf' | 'image' | 'docx' | None (unsupported)."""
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith((b"\x89PNG", b"\xff\xd8\xff", b"II*\x00", b"MM\x00*")):
        return "image"
    if data.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "word/document.xml" in z.namelist():
                    return "docx"
        except zipfile.BadZipFile:
            return None
    return None


def image_to_pdf(data: bytes) -> bytes:
    with pymupdf.open(stream=data) as img:
        return img.convert_to_pdf()


def docx_to_pdf(data: bytes) -> bytes:
    """Text-only conversion (PyMuPDF cannot open DOCX): paragraphs from word/document.xml, laid out on
    Letter pages. Enough for the classifier and extraction to read it, with a real text layer."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    paragraphs = ["".join(t.text or "" for t in p.iter(f"{_W}t")) for p in root.iter(f"{_W}p")]
    lines: list[str] = []
    for para in paragraphs:
        lines.extend(_wrap(para, 95) or [""])
    out = pymupdf.open()
    per_page = 60
    for i in range(0, max(len(lines), 1), per_page):
        page = out.new_page(width=612, height=792)
        page.insert_text((54, 60), "\n".join(lines[i : i + per_page]), fontsize=9, fontname="helv")
    return out.tobytes()


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


@dataclass
class Rendered:
    pages: int
    pngs: list[bytes]  # first `max_pages` pages
    texts: list[str]  # text layer per rendered page


def render(pdf: bytes, dpi: int, max_pages: int) -> Rendered:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        n = doc.page_count
        pngs, texts = [], []
        for i in range(min(n, max_pages)):
            page = doc[i]
            pngs.append(page.get_pixmap(dpi=dpi).tobytes("png"))
            texts.append(page.get_text())
        return Rendered(n, pngs, texts)


def page_count(pdf: bytes) -> int:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return doc.page_count


# ---------------------------------------------------------------- storage keys
# Content-addressed by the uploaded file's sha256, so page-range segments of one file and the child cases
# of a bundle share the renders.
def page_png_key(sha256: str, page_no: int) -> str:
    return f"renders/{sha256}/pages/{page_no}.png"


def text_key(sha256: str) -> str:
    return f"renders/{sha256}/text.json"


def dump_texts(texts: list[str]) -> bytes:
    return json.dumps(texts, ensure_ascii=False).encode()


def load_texts(raw: bytes) -> list[str]:
    return list(json.loads(raw.decode()))


# ---------------------------------------------------------------- locate values
def locate(
    pdf: bytes,
    needles: list[str],
    pages: list[int],
    hint: tuple[int, list[float]] | None = None,
) -> tuple[int, list[float]] | None:
    """Exact bbox of the first needle found in the text layer, normalised 0..1 from the top-left.

    `pages` are 1-based page numbers to search (the hinted page first). Among several hits (e.g. the same
    amount as subtotal and total) the one closest to the model's hinted box wins.
    """
    order = list(pages)
    if hint and hint[0] in order:
        order.remove(hint[0])
        order.insert(0, hint[0])
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        for page_no in order:
            if not 1 <= page_no <= doc.page_count:
                continue
            page = doc[page_no - 1]
            w, h = page.rect.width, page.rect.height
            for needle in needles:
                if not needle.strip():
                    continue
                hits = _hits(page, needle.strip())
                if not hits:
                    continue
                boxes = [[r.x0 / w, r.y0 / h, r.x1 / w, r.y1 / h] for r in hits]
                if hint and hint[0] == page_no and len(boxes) > 1:
                    hx, hy = (hint[1][0] + hint[1][2]) / 2, (hint[1][1] + hint[1][3]) / 2
                    boxes.sort(key=lambda b: ((b[0] + b[2]) / 2 - hx) ** 2 + ((b[1] + b[3]) / 2 - hy) ** 2)
                return page_no, [round(v, 4) for v in boxes[0]]
    return None


_EDGE = "$€£₹(),:;"  # currency symbols and punctuation glued to a printed value


def _hits(page: pymupdf.Page, needle: str) -> list[pymupdf.Rect]:
    """Rects of whole-word matches: a one-word needle must equal a word (bar currency/punctuation), so
    "100.00" does not hit inside "1,100.00" and "8.25" not inside "8.25%". Multi-word needles use
    search_for."""
    if " " in needle:
        return list(page.search_for(needle))
    return [pymupdf.Rect(w[:4]) for w in page.get_text("words") if w[4].strip(_EDGE) == needle]


_SPACE = re.compile(r"\s+")


def squash(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


def squash_lines(text: str) -> str:
    """Drop blank lines and trailing spaces (text layers are full of them)."""
    return "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())
