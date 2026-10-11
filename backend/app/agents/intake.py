"""Intake (code): check, convert and render every uploaded document of a case.

- sniff the format; images and DOCX become PDFs (stored next to the original)
- reject empty uploads and files over the size or page limits
- render page PNGs (render_dpi, max_pages) and store the text layer per page
- flag scanned PDFs (text layer shorter than min_text_chars)
Bundles are split later, once the classifier has marked page ranges.
"""

from __future__ import annotations

import asyncio
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import select

from app import pdf
from app.agents.base import Agent, AgentContext, AgentResult
from app.models import Document

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class IntakeAgent(Agent):
    async def run(self, action: str, ctx: AgentContext) -> AgentResult:
        assert ctx.storage is not None, "intake needs storage"
        p = ctx.params
        limits = ctx.config.app.limits
        max_pages = min(int(p.get("max_pages", limits.max_pages_per_document)), limits.max_pages_per_document)
        dpi = int(p.get("render_dpi", limits.render_dpi))
        min_text = int(p["min_text_chars"])
        accepted = set(p.get("accepted_mime", []))
        max_bytes = limits.max_upload_mb * 1024 * 1024

        with ctx.session_factory() as s:
            docs = s.scalars(
                select(Document).where(Document.case_id == ctx.case_id).order_by(Document.id)
            ).all()
            if not docs:
                return AgentResult("empty", {"documents": []}, ["No files were uploaded with this case."])

            summary: list[dict[str, Any]] = []
            reasoning: list[str] = []
            skipped: list[str] = []
            for doc in docs:
                data = ctx.storage.get(doc.storage_key)
                if not data:
                    skipped.append(f"{doc.filename} is empty")
                    continue
                if len(data) > max_bytes:
                    return AgentResult(
                        "too_large",
                        {"documents": summary, "file": doc.filename, "bytes": len(data)},
                        [
                            f"{doc.filename} is {len(data) / 1048576:.1f} MB; the limit is {limits.max_upload_mb:g} MB."
                        ],
                    )
                kind = pdf.sniff(data, doc.filename)
                image_mime = doc.mime if (doc.mime or "").startswith("image/") else "image/png"
                mime = {"pdf": "application/pdf", "image": image_mime, "docx": DOCX_MIME}.get(kind or "")
                if mime is None or (accepted and mime not in accepted):
                    skipped.append(f"{doc.filename} is not a PDF, image or Word file")
                    continue

                if kind != "pdf":
                    converted = await asyncio.to_thread(
                        pdf.image_to_pdf if kind == "image" else pdf.docx_to_pdf, data
                    )
                    doc.storage_key = ctx.storage.put(
                        str(PurePosixPath(doc.storage_key).with_suffix(".pdf")), converted, "application/pdf"
                    )
                    reasoning.append(f"Converted {doc.filename} ({kind}) to PDF.")
                    data = converted
                doc.mime = "application/pdf"

                rendered = await asyncio.to_thread(pdf.render, data, dpi, max_pages)
                if rendered.pages > max_pages:
                    return AgentResult(
                        "too_large",
                        {"documents": summary, "file": doc.filename, "pages": rendered.pages},
                        [
                            f"{doc.filename} has {rendered.pages} pages; the limit is {max_pages} per document."
                        ],
                    )
                for i, png in enumerate(rendered.pngs, start=1):
                    ctx.storage.put(pdf.page_png_key(doc.sha256, i), png, "image/png")
                ctx.storage.put(pdf.text_key(doc.sha256), pdf.dump_texts(rendered.texts), "application/json")
                text_chars = sum(len(t.strip()) for t in rendered.texts)
                doc.pages = rendered.pages
                doc.has_text_layer = text_chars >= min_text
                summary.append(
                    {
                        "document_id": doc.id,
                        "filename": doc.filename,
                        "pages": rendered.pages,
                        "has_text_layer": doc.has_text_layer,
                        "text_chars": text_chars,
                        "sha256": doc.sha256[:12],
                    }
                )
                layer = "text layer found" if doc.has_text_layer else "no text layer (scanned), images only"
                reasoning.append(f"Stored {doc.filename}: {rendered.pages} page(s), {layer}.")
                await ctx.emit(f"rendered {doc.filename}")
            s.commit()

        reasoning += [f"Skipped: {x}." for x in skipped]
        if not summary:
            return AgentResult(
                "empty", {"documents": [], "skipped": skipped}, reasoning or ["No readable files."]
            )
        scanned = sum(1 for d in summary if not d["has_text_layer"])
        return AgentResult(
            "ok",
            {"documents": summary, "skipped": skipped, "scanned_documents": scanned},
            reasoning[:5],
            confidence=1.0,
        )
