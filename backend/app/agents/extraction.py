"""Extraction (LLM): page images + text layer -> InvoiceExtraction, with per-field confidence and location.

For text PDFs each located value is searched in the text layer with PyMuPDF `search_for`, which gives an
exact bounding box (the model's box only picks between several hits). Scans keep the model's normalised
boxes. Learned vendor-memory hints are injected into the prompt when the vendor is already known.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import pdf
from app.agents.base import Agent, AgentContext, AgentResult
from app.documents import page_pngs, page_range, page_texts, text_block
from app.llm import image_part, sum_metrics, text_part
from app.llm_schemas import FieldConfidence, FieldLocations, InvoiceExtraction
from app.models import Case, Document, Extraction, Vendor, VendorMemory

INVOICE_ROUTE = "case_invoice"


class ExtractionAgent(Agent):
    async def run(self, action: str, ctx: AgentContext) -> AgentResult:
        assert ctx.storage is not None, "extraction needs storage"
        p = ctx.params
        with ctx.session_factory() as s:
            case = s.get(Case, ctx.case_id)
            assert case is not None
            doc = self._invoice_document(s, ctx)
            if doc is None:
                return AgentResult("failed", {}, ["No invoice document to read in this case."])
            pages = page_range(doc)
            texts = await asyncio.to_thread(page_texts, ctx.storage, doc)
            content = []
            if p.get("send_text_layer", True) and doc.has_text_layer:
                block = text_block(texts, pages, int(p["max_text_chars"]))
                content.append(
                    text_part(f"PDF text layer of {doc.filename}, pages {pages[0]}-{pages[-1]}:\n{block}")
                )
            else:
                content.append(
                    text_part(f"{doc.filename} has no text layer (scanned): read the page images.")
                )
            if p.get("send_images", True) or not doc.has_text_layer:
                pngs = await asyncio.to_thread(page_pngs, ctx.storage, doc, pages)
                content += [image_part(png) for png in pngs]
            if pages[0] != 1:
                content.append(
                    text_part(
                        f"Location page numbers refer to the original file: these images are pages {pages[0]}-{pages[-1]}."
                    )
                )

            vendor, hints = self._vendor_hints(s, ctx, case, texts)
            await ctx.emit(f"reading {doc.filename} p{pages[0]}-{pages[-1]}")
            out, call = await self.call_llm(
                ctx,
                action,
                InvoiceExtraction,
                content,
                prompt_vars={"vendor_hints": hints},
                context={"document": doc.filename, "pages": [pages[0], pages[-1]]},
            )
            assert isinstance(out, InvoiceExtraction)

            can_locate = bool(doc.has_text_layer and p.get("locate_values_in_text_layer", True))
            pdf_bytes = ctx.storage.get(doc.storage_key) if can_locate else b""
            located = 0
            boxes: dict[str, tuple[int, list[float], str]] = {}
            for name in FieldLocations.model_fields:
                loc = getattr(out.location, name)
                hint = (loc.page, loc.bbox) if loc and len(loc.bbox) == 4 else None
                exact = None
                value = getattr(out, name)
                if value is not None and can_locate:
                    needles = _needles(value, p.get("locate_date_formats", []))
                    exact = await asyncio.to_thread(pdf.locate, pdf_bytes, needles, pages, hint)
                if exact:
                    boxes[name] = (exact[0], exact[1], "text_layer")
                    located += 1
                elif hint:
                    boxes[name] = (hint[0], [round(v, 4) for v in hint[1]], "model")
            field_meta = _field_meta(out, boxes)

            version = (
                s.scalar(select(func.max(Extraction.version)).where(Extraction.case_id == case.id)) or 0
            ) + 1
            row = Extraction(
                case_id=case.id, version=version, data=out.model_dump(mode="json"), field_meta=field_meta
            )
            s.add(row)
            header = [f for f in p["confidence_fields"] if getattr(out.confidence, f) is not None]
            conf = min((getattr(out.confidence, f) for f in header), default=None)
            case.confidence = conf
            s.commit()
            extraction_id = row.id

        threshold = float(p["low_confidence_below"])
        weak = sorted(
            (f for f in header if getattr(out.confidence, f) < threshold),
            key=lambda f: getattr(out.confidence, f),
        )
        if out.invoice_number is None and out.total is None and not out.lines:
            outcome = "failed"
        elif weak:
            outcome = "low_confidence"
        else:
            outcome = "ok"

        reasoning = [_headline(out)]
        if doc.has_text_layer:
            reasoning.append(
                f"Exact positions found in the text layer for {located} of {len(boxes)} located fields."
            )
        else:
            reasoning.append("Scanned document: values and positions come from the page images.")
        if weak:
            reasoning.append(
                "Low confidence: "
                + ", ".join(f"{f} {getattr(out.confidence, f):.2f}" for f in weak)
                + f" (below {threshold:.2f})."
            )
        missing = [f for f in p["confidence_fields"] if getattr(out, f) is None]
        if missing:
            reasoning.append("Not found on the document: " + ", ".join(missing) + ".")
        if hints:
            reasoning.append(f"Used {len(hints)} learned hint(s) for {vendor}.")
        if outcome == "failed":
            reasoning.append("Nothing usable could be read (no invoice number, total or lines).")

        return AgentResult(
            outcome,
            {
                "extraction_id": extraction_id,
                "version": version,
                "document_id": doc.id,
                "pages": [pages[0], pages[-1]],
                "invoice": out.model_dump(mode="json"),
                "field_meta": field_meta,
                "vendor_hints": hints,
            },
            reasoning[:5],
            confidence=conf,
            metrics=sum_metrics([call]),
        )

    def _invoice_document(self, s: Session, ctx: AgentContext) -> Document | None:
        doc_id = ctx.state.get("classifier", {}).get("invoice_document_id")
        if doc_id:
            doc = s.get(Document, doc_id)
            if doc is not None:
                return doc
        routes = {lbl.id: lbl.route for lbl in ctx.config.document_types.labels}
        docs = s.scalars(select(Document).where(Document.case_id == ctx.case_id).order_by(Document.id)).all()
        return next((d for d in docs if routes.get(d.label or "") == INVOICE_ROUTE), None)

    def _vendor_hints(
        self, s: Session, ctx: AgentContext, case: Case, texts: list[str]
    ) -> tuple[str | None, list[str]]:
        """Hints for a vendor that is already known: from the vendor check (a re-extraction), else the
        submitter's email domain, else a master vendor name printed in the text layer."""
        if not (ctx.params.get("use_vendor_memory") and ctx.config.app.features.get("vendor_memory")):
            return None, []
        vendor_id = ctx.state.get("vendor", {}).get("vendor_id") or case.vendor_id
        vendor = s.get(Vendor, vendor_id) if vendor_id else None
        if vendor is None and case.submitter_email and "@" in case.submitter_email:
            domain = case.submitter_email.rsplit("@", 1)[1].lower()
            vendor = s.scalars(select(Vendor).where(func.lower(Vendor.email_domain) == domain)).first()
        if vendor is None and texts:
            flat = pdf.squash(" ".join(texts)).lower()
            vendor = next((v for v in s.scalars(select(Vendor)).all() if v.legal_name.lower() in flat), None)
        if vendor is None:
            return None, []
        rows = s.scalars(
            select(VendorMemory)
            .where(VendorMemory.vendor_id == vendor.id)
            .order_by(VendorMemory.created_at.desc())
            .limit(int(ctx.params["max_vendor_hints"]))
        ).all()
        return vendor.legal_name, [r.hint for r in rows]


def _needles(value: Any, date_formats: list[str]) -> list[str]:
    """Printed forms of a value to search for: amounts with and without thousands separators, ISO dates
    in the configured print formats, strings as-is."""
    if isinstance(value, Decimal):
        return [f"{value:,.2f}", f"{value:.2f}"]
    text = str(value).strip()
    try:
        d = date.fromisoformat(text)
    except ValueError:
        return [text]
    out = [text]
    for fmt in date_formats:
        printed = d.strftime(fmt)
        out += [printed, printed.replace(" 0", " ")]  # "Oct 02, 2026" and "Oct 2, 2026"
    return list(dict.fromkeys(out))


def _field_meta(out: InvoiceExtraction, boxes: dict[str, tuple[int, list[float], str]]) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    for name in FieldConfidence.model_fields:
        page, bbox, source = boxes.get(name, (None, None, None))
        meta[name] = {
            "confidence": getattr(out.confidence, name),
            "page": page,
            "bbox": bbox,
            "source": source,
        }
    return meta


def _headline(out: InvoiceExtraction) -> str:
    total = f"{out.total:,.2f} {out.currency or ''}".strip() if out.total is not None else "no total"
    return (
        f"Read invoice {out.invoice_number or '(no number)'} from {out.vendor_name or 'an unnamed vendor'}"
        f" dated {out.invoice_date or '(no date)'}: {total}, {len(out.lines)} line(s)."
    )
