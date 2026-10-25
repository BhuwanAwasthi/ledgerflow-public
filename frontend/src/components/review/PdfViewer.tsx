import { FileText, Minus, Plus, ScanLine } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import type { DocumentInfo } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Tip } from "@/components/ui/overlays";
import { Badge, Skeleton } from "@/components/ui/primitives";
import { useSignedUrl } from "@/hooks/useSignedUrl";
import { humanise } from "@/lib/format";
import { toneStyle } from "@/lib/status";
import { cn } from "@/lib/utils";

pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();
// one plain GET per document: range/stream requests would come back after the 60 s file token expired
const PDF_OPTIONS = { disableRange: true, disableStream: true, disableAutoFetch: true };

/** A rendered page PNG, through its own file token (the fallback for files the browser can't read). */
function PagePng({ path, alt }: { path: string; alt: string }) {
  const { url } = useSignedUrl(path);
  return url ? <img src={url} alt={alt} className="block w-full" /> : <Skeleton className="aspect-[1/1.294] w-full rounded-none" />;
}

export interface FieldBox {
  field: string;
  page: number;
  bbox: [number, number, number, number]; // normalised 0..1, x0 y0 x1 y1
  confidence: number | null;
  flagged: boolean;
}

/** field_meta {field: {page, bbox, confidence}} -> boxes the viewer can draw (fields without a location are skipped). */
export function boxesFrom(fieldMeta: Record<string, unknown> | undefined, flagged: Set<string>): FieldBox[] {
  const out: FieldBox[] = [];
  for (const [field, raw] of Object.entries(fieldMeta ?? {})) {
    const m = raw as { page?: number | null; bbox?: number[] | null; confidence?: number | null } | null;
    if (!m?.bbox || m.bbox.length !== 4 || !m.page) continue;
    out.push({ field, page: m.page, bbox: m.bbox as FieldBox["bbox"], confidence: m.confidence ?? null, flagged: flagged.has(field) });
  }
  return out;
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.floor(e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

function Highlights({ boxes, active, onHover }: { boxes: FieldBox[]; active: string | null; onHover: (f: string | null) => void }) {
  const amber = toneStyle("amber");
  const accent = toneStyle("indigo");
  return (
    <>
      {boxes.map((b) => {
        const [x0, y0, x1, y1] = b.bbox;
        const isActive = active === b.field;
        return (
          <Tip key={b.field} content={`${humanise(b.field)}${b.confidence !== null ? ` · ${Math.round(b.confidence * 100)}% confidence` : ""}`}>
            <div
              onMouseEnter={() => onHover(b.field)}
              onMouseLeave={() => onHover(null)}
              className={cn(
                "absolute rounded-[3px] transition-all duration-150",
                // idle: a faint outline (amber when flagged); the fill appears only on the linked (hovered) field
                b.flagged ? amber.ring : cn(accent.ring, "opacity-25"),
                isActive && cn("z-10 opacity-100 ring-2 shadow-[0_0_0_4px_hsl(var(--ring)/0.2)]", b.flagged ? amber.soft : "bg-primary/20"),
              )}
              style={{ left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%`, margin: "-2px", padding: "2px", boxSizing: "content-box" }}
            />
          </Tip>
        );
      })}
    </>
  );
}

/** One document: every page stacked (intake caps documents at a few pages), boxes drawn on the invoice document. */
function DocumentPages({
  caseId,
  doc,
  boxes,
  active,
  onHover,
  zoom,
}: {
  caseId: number;
  doc: DocumentInfo;
  boxes: FieldBox[];
  active: string | null;
  onHover: (f: string | null) => void;
  zoom: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [pages, setPages] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);
  const signed = useSignedUrl(`/api/cases/${caseId}/documents/${doc.id}/file`);
  const file = useMemo(() => (signed.url ? { url: signed.url } : null), [signed.url]);
  const pageWidth = Math.max(240, Math.round(width * zoom));
  const count = pages ?? doc.pages ?? 1;

  // scroll the active box into view when the reviewer hovers a grid cell
  const activeBox = boxes.find((b) => b.field === active);
  const pageRefs = useRef<Record<number, HTMLDivElement | null>>({});
  useEffect(() => {
    if (!activeBox) return;
    const el = pageRefs.current[activeBox.page];
    const scroller = ref.current?.parentElement;
    if (!el || !scroller) return;
    const top = el.offsetTop + activeBox.bbox[1] * el.offsetHeight - scroller.clientHeight / 3;
    const visibleTop = scroller.scrollTop;
    const y = el.offsetTop + activeBox.bbox[1] * el.offsetHeight;
    if (y < visibleTop + 40 || y > visibleTop + scroller.clientHeight - 40) scroller.scrollTo({ top, behavior: "smooth" });
  }, [activeBox, ref]);

  const pageBlock = (n: number, content: React.ReactNode) => (
    <div key={n} ref={(el) => { pageRefs.current[n] = el; }} className="relative mx-auto mb-4 overflow-hidden rounded-sm bg-white shadow-[0_1px_3px_rgba(16,24,40,0.12),0_8px_24px_-8px_rgba(16,24,40,0.18)]" style={{ width: pageWidth }}>
      {content}
      <Highlights boxes={boxes.filter((b) => b.page === n)} active={active} onHover={onHover} />
    </div>
  );

  return (
    <div ref={ref} className="px-4 pt-4">
      {failed || signed.error ? (
        // not a PDF the browser can read: fall back to the PNGs intake rendered
        Array.from({ length: doc.pages ?? 1 }, (_, i) =>
          pageBlock(i + 1, <PagePng path={`/api/cases/${caseId}/documents/${doc.id}/pages/${i + 1}.png`} alt={`${doc.filename} page ${i + 1}`} />),
        )
      ) : !file ? (
        <Skeleton className="mx-auto aspect-[1/1.294] rounded-sm" style={{ width: pageWidth || "100%" }} />
      ) : (
        <Document
          file={file}
          options={PDF_OPTIONS}
          onLoadSuccess={(d) => setPages(d.numPages)}
          onLoadError={() => setFailed(true)}
          loading={<Skeleton className="mx-auto aspect-[1/1.294] rounded-sm" style={{ width: pageWidth || "100%" }} />}
          error={null}
        >
          {width > 0 &&
            Array.from({ length: count }, (_, i) => i + 1).map((n) =>
                pageBlock(
                  n,
                  <Page
                    pageNumber={n}
                    width={pageWidth}
                    renderTextLayer={false}
                    renderAnnotationLayer={false}
                    loading={<Skeleton className="aspect-[1/1.294] w-full rounded-none" />}
                  />,
                ),
              )}
        </Document>
      )}
    </div>
  );
}

export function PdfViewer({
  caseId,
  documents,
  invoiceDocId,
  boxes,
  active,
  onHover,
  className,
  compact,
}: {
  caseId: number;
  documents: DocumentInfo[];
  invoiceDocId: number | null;
  boxes: FieldBox[];
  active: string | null;
  onHover: (f: string | null) => void;
  className?: string;
  compact?: boolean;
}) {
  const ordered = useMemo(
    () => [...documents].sort((a, b) => (a.id === invoiceDocId ? -1 : b.id === invoiceDocId ? 1 : a.id - b.id)),
    [documents, invoiceDocId],
  );
  const [docId, setDocId] = useState<number | null>(null);
  const current = ordered.find((d) => d.id === docId) ?? ordered[0];
  const [zoom, setZoom] = useState(1);
  useEffect(() => {
    if (active && invoiceDocId && current?.id !== invoiceDocId && boxes.some((b) => b.field === active)) setDocId(invoiceDocId);
  }, [active, invoiceDocId, current, boxes]);

  if (!current) {
    return <div className={cn("flex items-center justify-center text-sm text-muted-foreground", className)}>No documents on this case.</div>;
  }
  return (
    <div className={cn("flex min-h-0 flex-col", className)}>
      <div className="flex items-center gap-1 border-b px-2">
        <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto" role="tablist" aria-label="Documents">
          {ordered.map((d) => (
            <button
              key={d.id}
              role="tab"
              aria-selected={d.id === current.id}
              title={d.filename}
              onClick={() => setDocId(d.id)}
              className={cn(
                "relative -mb-px inline-flex h-10 max-w-[240px] shrink-0 items-center gap-1.5 border-b-2 border-transparent px-2.5 text-[13px] text-muted-foreground hover:text-foreground",
                d.id === current.id && "border-primary font-medium text-foreground",
              )}
            >
              <FileText className="size-3.5 shrink-0" />
              <span className="truncate">{d.filename}</span>
              {d.label && !compact && <Badge className="px-1.5 py-0 text-[10px]">{humanise(d.label)}</Badge>}
            </button>
          ))}
        </div>
        {current.has_text_layer === false && (
          <Tip content="Scanned PDF: no text layer, so field locations come from the vision model">
            <span className="inline-flex items-center gap-1 rounded-md px-1.5 text-[11px] text-muted-foreground">
              <ScanLine className="size-3.5" /> Scan
            </span>
          </Tip>
        )}
        <div className="flex items-center">
          <Button variant="ghost" size="icon-sm" aria-label="Zoom out" onClick={() => setZoom((z) => Math.max(0.6, +(z - 0.15).toFixed(2)))}>
            <Minus />
          </Button>
          <span className="w-10 text-center text-[11px] tabular text-muted-foreground">{Math.round(zoom * 100)}%</span>
          <Button variant="ghost" size="icon-sm" aria-label="Zoom in" onClick={() => setZoom((z) => Math.min(2, +(z + 0.15).toFixed(2)))}>
            <Plus />
          </Button>
        </div>
      </div>
      {/* positioned, so page offsetTop is measured from the scroller (hover-to-scroll) */}
      <div className="relative min-h-0 flex-1 overflow-auto bg-muted/50">
        <DocumentPages
          key={current.id}
          caseId={caseId}
          doc={current}
          boxes={current.id === invoiceDocId ? boxes : []}
          active={active}
          onHover={onHover}
          zoom={zoom}
        />
      </div>
    </div>
  );
}
