import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Check, Copy as CopyIcon, Equal, EqualNot } from "lucide-react";
import { Link } from "react-router-dom";
import { api, unwrap, type CaseDetail } from "@/api/client";
import { StatusBadge } from "@/components/common";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/primitives";
import { dateTime, money } from "@/lib/format";
import { toneStyle } from "@/lib/status";
import { cn } from "@/lib/utils";
import { PdfViewer } from "./PdfViewer";

export interface DuplicateLink {
  rule_id: string;
  case_id: number;
  ref: string | null;
  explanation: string;
}

/** The document the extractor read (last extraction stage output), else the first document. */
export function invoiceDocumentId(c: CaseDetail): number | null {
  for (const e of [...c.events].reverse()) {
    const id = (e.output as { document_id?: number }).document_id;
    if (e.agent_id === "extraction" && e.status === "completed" && typeof id === "number") return id;
  }
  return c.documents[0]?.id ?? null;
}

function norm(v: unknown): string {
  return String(v ?? "")
    .toUpperCase()
    .replace(/[^A-Z0-9.]/g, "");
}

function Side({ c, title }: { c: CaseDetail; title: string }) {
  const doc = c.documents.find((d) => d.id === invoiceDocumentId(c));
  return (
    <Card className="flex min-h-0 flex-col overflow-hidden">
      <div className="flex items-center gap-2 border-b px-4 py-2.5">
        <span className="text-xs font-medium text-muted-foreground">{title}</span>
        <Link to={`/cases/${c.id}/review`} className="inline-flex items-center gap-0.5 text-[13px] font-semibold hover:underline">
          {c.ref} <ArrowUpRight className="size-3.5" />
        </Link>
        <StatusBadge kind="case" value={c.status} />
        <span className="ml-auto text-xs text-muted-foreground">
          {doc?.has_text_layer === false ? "scanned PDF" : "text PDF"} · received {dateTime(c.created_at)}
        </span>
      </div>
      <PdfViewer caseId={c.id} documents={doc ? [doc] : c.documents} invoiceDocId={doc?.id ?? null} boxes={[]} active={null} onHover={() => undefined} className="min-h-0 flex-1" compact />
    </Card>
  );
}

export function ComparePanel({ current, link, currency }: { current: CaseDetail; link: DuplicateLink; currency: string }) {
  const other = useQuery({
    queryKey: ["case", link.case_id],
    queryFn: async () => unwrap(await api.GET("/api/cases/{case_id}", { params: { path: { case_id: link.case_id } } })),
  });
  const o = other.data;
  const rows: { label: string; a: string; b: string; same: boolean }[] = o
    ? [
        { label: "Vendor", a: current.vendor_name ?? "—", b: o.vendor_name ?? "—", same: current.vendor_id !== null && current.vendor_id === o.vendor_id },
        { label: "Invoice number", a: current.invoice_number ?? "—", b: o.invoice_number ?? "—", same: norm(current.invoice_number) !== "" && norm(current.invoice_number) === norm(o.invoice_number) },
        { label: "Invoice date", a: current.invoice_date ?? "—", b: o.invoice_date ?? "—", same: !!current.invoice_date && current.invoice_date === o.invoice_date },
        { label: "Total", a: money(current.total, currency), b: money(o.total, o.currency ?? currency), same: current.total !== null && Number(current.total) === Number(o.total) },
        { label: "Submitted by", a: current.submitter_email ?? "—", b: o.submitter_email ?? "—", same: current.submitter_email === o.submitter_email },
        { label: "Status", a: current.status, b: o.status, same: false },
      ]
    : [];
  const rose = toneStyle("rose");
  const same = rose; // matching values are the evidence of duplication
  return (
    <div className="space-y-4">
      <div className={cn("flex flex-wrap items-start gap-3 rounded-lg border px-4 py-3", rose.border, rose.soft)}>
        <CopyIcon className={cn("mt-0.5 size-4 shrink-0", rose.text)} />
        <div className="min-w-0 flex-1 text-[13px]">
          <div className="font-semibold">Possible double payment: compare with {link.ref ?? `case ${link.case_id}`}</div>
          <div className="mt-0.5 text-foreground/80">{link.explanation}</div>
        </div>
      </div>

      <Card className="overflow-hidden">
        <table className="w-full text-[13px]">
          <thead className="bg-muted/70 text-[11px] uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="w-40 px-4 py-2 text-left font-semibold">Field</th>
              <th className="px-4 py-2 text-left font-semibold">This case · {current.ref}</th>
              <th className="w-10 px-2 py-2" aria-label="Match" />
              <th className="px-4 py-2 text-left font-semibold">Earlier case · {link.ref}</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {!o &&
              [0, 1, 2, 3].map((i) => (
                <tr key={i}>
                  <td colSpan={4} className="px-4 py-2.5">
                    <Skeleton />
                  </td>
                </tr>
              ))}
            {rows.map((r) => (
              <tr key={r.label} className={cn(r.same && same.soft)}>
                <td className="px-4 py-2 text-muted-foreground">{r.label}</td>
                <td className="px-4 py-2 font-medium tabular">{r.label === "Status" ? <StatusBadge kind="case" value={r.a} /> : r.a}</td>
                <td className="px-2 py-2 text-center">
                  {r.label === "Status" ? null : r.same ? <Equal className={cn("mx-auto size-4", same.text)} aria-label="same" /> : <EqualNot className="mx-auto size-4 text-muted-foreground" aria-label="different" />}
                </td>
                <td className="px-4 py-2 font-medium tabular">{r.label === "Status" ? <StatusBadge kind="case" value={r.b} /> : r.b}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {o && (
          <div className="flex items-center gap-1.5 border-t px-4 py-2 text-xs text-muted-foreground">
            <Check className="size-3.5" /> Highlighted rows hold the same value (invoice numbers ignoring case, spaces and punctuation). The rejection itself comes from the duplicate rule above.
          </div>
        )}
      </Card>

      <div className="grid h-[calc(100vh-15rem)] min-h-[520px] gap-4 lg:grid-cols-2">
        <Side c={current} title="This case" />
        {o ? <Side c={o} title="Earlier case" /> : <Skeleton className="h-full rounded-lg" />}
      </div>
    </div>
  );
}
