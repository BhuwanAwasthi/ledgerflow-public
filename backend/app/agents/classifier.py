"""Classifier (LLM): label every document with a document_types.yaml label and find bundles.

Per file: the first `pages_per_document` page images + the text layer of every page -> ClassifierOutput
(label enum injected from config). Several documents in one file become page-range segments. A case
holding more than one invoice is split: one child case per invoice, and this case ends as `split`.
Documents that already carry a label (a bundle's child case) are not classified again.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from app.agents.base import Agent, AgentContext, AgentResult
from app.cases import create_child_case
from app.documents import page_pngs, page_texts, segment_of, text_block
from app.llm import image_part, strict_json_schema, sum_metrics, text_part
from app.llm_schemas import ClassifiedDocument, ClassifierOutput
from app.models import Case, Document

INVOICE_ROUTE = "case_invoice"


class ClassifierAgent(Agent):
    async def classify_document(
        self, ctx: AgentContext, doc: Document, schema: dict[str, Any] | None = None
    ) -> tuple[list[ClassifiedDocument], dict[str, Any]]:
        """One model call for one rendered file: cleaned page-range entries + call metrics. Also used by the
        vendor portal for uploaded onboarding documents."""
        assert ctx.storage is not None, "classifier needs storage"
        n_images = int(ctx.params["pages_per_document"])
        max_chars = int(ctx.params["max_text_chars"])
        pages = list(range(1, (doc.pages or 1) + 1))
        texts = await asyncio.to_thread(page_texts, ctx.storage, doc)
        pngs = await asyncio.to_thread(page_pngs, ctx.storage, doc, pages[:n_images])
        content = [
            text_part(
                f"File: {doc.filename} ({doc.pages} page(s)). "
                + (
                    f"Text layer:\n{text_block(texts, pages, max_chars)}"
                    if doc.has_text_layer
                    else "No text layer (scanned): read the page images."
                )
            ),
            *(image_part(p) for p in pngs),
        ]
        out, metrics = await self.call_llm(
            ctx,
            "classify",
            ClassifierOutput,
            content,
            prompt_vars={"labels": ctx.config.document_types.labels},
            json_schema=schema or label_schema(ctx),
            context={"document": doc.filename},
        )
        assert isinstance(out, ClassifierOutput)
        return _clean(out.documents, doc.pages or 1), metrics

    async def run(self, action: str, ctx: AgentContext) -> AgentResult:
        assert ctx.storage is not None, "classifier needs storage"
        dt = ctx.config.document_types
        routes = {lbl.id: lbl.route for lbl in dt.labels}
        fallback = "other" if "other" in routes else dt.labels[-1].id
        schema = label_schema(ctx)

        calls: list[dict[str, Any]] = []
        reasoning: list[str] = []
        confidences: list[float] = []
        uncertain: list[tuple[Document, ClassifiedDocument]] = []  # below min_confidence, labelled `fallback`
        with ctx.session_factory() as s:
            docs = s.scalars(
                select(Document)
                .where(Document.case_id == ctx.case_id, Document.pages.is_not(None))
                .order_by(Document.id)
            ).all()
            for doc in list(docs):
                if doc.label:
                    reasoning.append(
                        f"{doc.filename} p{doc.page_from}-{doc.page_to}: already labelled '{doc.label}'."
                    )
                    continue
                await ctx.emit(f"classifying {doc.filename}")
                entries, metrics = await self.classify_document(ctx, doc, schema)
                calls.append(metrics)
                entries = entries or [
                    ClassifiedDocument(
                        label=fallback, confidence=0.0, page_from=1, page_to=doc.pages or 1, reason=""
                    )
                ]
                for i, e in enumerate(entries):
                    label = e.label if e.label in routes and e.confidence >= dt.min_confidence else fallback
                    note = (
                        ""
                        if label == e.label
                        else f" (model said '{e.label}' at {e.confidence:.0%}; below {dt.min_confidence:.0%})"
                    )
                    if i == 0:
                        target = doc
                        doc.label, doc.label_confidence = label, e.confidence
                        doc.page_from, doc.page_to = e.page_from, e.page_to
                    else:
                        target = segment_of(
                            doc,
                            case_id=ctx.case_id,
                            label=label,
                            confidence=e.confidence,
                            page_from=e.page_from,
                            page_to=e.page_to,
                        )
                        s.add(target)
                    if label != e.label and routes.get(e.label) == INVOICE_ROUTE:
                        uncertain.append((target, e))
                    confidences.append(e.confidence)
                    reasoning.append(
                        f"{doc.filename} p{e.page_from}-{e.page_to}: {label} ({e.confidence:.0%}){note}. {e.reason}".strip()
                    )
                dropped = uncovered_pages(entries, doc.pages or 1)
                if dropped:
                    reasoning.append(
                        f"{doc.filename}: page(s) {', '.join(dropped)} matched no document type and were left out."
                    )
            s.flush()

            docs = s.scalars(
                select(Document).where(Document.case_id == ctx.case_id).order_by(Document.id)
            ).all()
            summary = [
                {
                    "document_id": d.id,
                    "filename": d.filename,
                    "label": d.label,
                    "confidence": d.label_confidence,
                    "page_from": d.page_from,
                    "page_to": d.page_to,
                    "route": routes.get(d.label or "", "ignore"),
                }
                for d in docs
                if d.label
            ]
            invoices = [d for d in docs if routes.get(d.label or "") == INVOICE_ROUTE]
            metrics = sum_metrics(calls)
            confidence = min(confidences) if confidences else None

            if len(invoices) > 1:
                parent = s.get(Case, ctx.case_id)
                assert parent is not None
                existing = s.scalars(
                    select(Case).where(Case.parent_case_id == parent.id).order_by(Case.id)
                ).all()
                if existing:
                    # A restart after the split committed: the children exist and resume_pending already
                    # re-queued them, so don't create (or queue) a second set.
                    return AgentResult(
                        "split",
                        {"documents": summary, "child_cases": [{"id": c.id, "ref": c.ref} for c in existing]},
                        [f"Already split into {', '.join(c.ref or '' for c in existing)}."],
                        confidence,
                        metrics,
                    )
                children = []
                for n, inv in enumerate(invoices, start=1):
                    seg = segment_of(
                        inv,
                        case_id=parent.id,
                        label=inv.label or "invoice",
                        confidence=inv.label_confidence or 0.0,
                        page_from=inv.page_from or 1,
                        page_to=inv.page_to or (inv.pages or 1),
                    )
                    children.append(
                        create_child_case(
                            s,
                            ctx.config,
                            parent,
                            [seg],
                            note=f"Invoice {n} of {len(invoices)} from {parent.ref} ({inv.filename} p{seg.page_from}-{seg.page_to})",
                        )
                    )
                s.commit()
                refs = [c.ref for c in children]
                return AgentResult(
                    "split",
                    {"documents": summary, "child_cases": [{"id": c.id, "ref": c.ref} for c in children]},
                    [
                        *reasoning[:3],
                        f"Found {len(invoices)} invoices; created one case each: {', '.join(refs)}.",
                    ],
                    confidence,
                    metrics,
                    spawn=[c.id for c in children],
                )
            low = None
            if not invoices and uncertain:
                # A probable invoice below min_confidence: read it anyway and let the document_classification
                # rule (severity review) put the case in front of a reviewer, instead of ending unsupported.
                target, e = max(uncertain, key=lambda pair: pair[1].confidence)
                target.label = e.label  # keep the model's label (and its low confidence) on this document
                low = {
                    "document_id": target.id,
                    "filename": target.filename,
                    "label": e.label,
                    "confidence": e.confidence,
                    "min_confidence": dt.min_confidence,
                    "page_from": target.page_from,
                    "page_to": target.page_to,
                }
                for item in summary:
                    if item["document_id"] == target.id:
                        item.update(label=e.label, route=routes[e.label])
            s.commit()

        if low is not None:
            return AgentResult(
                "low_confidence",
                {"documents": summary, "invoice_document_id": low["document_id"], "invoice_label": low["label"],
                 "uncertain_invoice": low},
                [
                    *reasoning[:3],
                    (
                        f"No document is a confident invoice; reading {low['filename']} p{low['page_from']}-"
                        f"{low['page_to']} ({low['label']} at {low['confidence']:.0%}, below "
                        f"{low['min_confidence']:.0%}) and flagging it for review."
                    ),
                ],
                confidence,
                metrics,
            )
        if not invoices:
            return AgentResult(
                "no_invoice",
                {"documents": summary},
                [*reasoning[:4], "No invoice or credit note found in this upload."],
                confidence,
                metrics,
            )
        inv = invoices[0]
        return AgentResult(
            "has_invoice",
            {"documents": summary, "invoice_document_id": inv.id, "invoice_label": inv.label},
            reasoning[:5],
            confidence,
            metrics,
        )


def label_schema(ctx: AgentContext) -> dict[str, Any]:
    """ClassifierOutput's strict schema with the label enum from document_types.yaml."""
    schema = strict_json_schema(ClassifierOutput)
    schema["$defs"]["ClassifiedDocument"]["properties"]["label"]["enum"] = [lbl.id for lbl in ctx.config.document_types.labels]
    return schema


def uncovered_pages(entries: list[ClassifiedDocument], pages: int) -> list[str]:
    """Pages of the file no classified range covers, as compact ranges ('3', '5-7')."""
    covered = {p for e in entries for p in range(e.page_from, e.page_to + 1)}
    missing = [p for p in range(1, pages + 1) if p not in covered]
    out: list[str] = []
    for p in missing:
        if out and p - 1 in missing:
            lo = out[-1].split("-")[0]
            out[-1] = f"{lo}-{p}"
        else:
            out.append(str(p))
    return out


def _clean(entries: list[ClassifiedDocument], pages: int) -> list[ClassifiedDocument]:
    """Clamp page ranges to the file, sort them and drop overlaps (first range wins)."""
    out: list[ClassifiedDocument] = []
    covered: set[int] = set()
    for e in sorted(entries, key=lambda x: (x.page_from, x.page_to)):
        lo, hi = max(1, min(e.page_from, pages)), max(1, min(e.page_to, pages))
        if hi < lo:
            lo, hi = hi, lo
        span = set(range(lo, hi + 1))
        if span & covered:
            continue
        covered |= span
        out.append(e.model_copy(update={"page_from": lo, "page_to": hi}))
    return out
