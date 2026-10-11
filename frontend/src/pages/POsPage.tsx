import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { ArrowLeft, Download, GitFork, History, ListOrdered, Receipt } from "lucide-react";
import { useMemo } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, apiUrl, unwrap, type PO } from "@/api/client";
import { DataTable } from "@/components/DataTable";
import { ErrorNote, PageHeader, StatusBadge } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tip } from "@/components/ui/overlays";
import { Badge, Skeleton } from "@/components/ui/primitives";
import { dateTime, money, plainMoney } from "@/lib/format";
import { statusDef } from "@/lib/status";
import { cn } from "@/lib/utils";

const text = (v: unknown, f: unknown) => String(v ?? "").toLowerCase().includes(String(f).toLowerCase());

/** Consumed | reserved | remaining on one bar (display only: the amounts come from the PO ledger). */
export function ConsumptionBar({ po, showLegend = false, className }: { po: Pick<PO, "amount" | "consumed" | "reserved" | "remaining" | "currency">; showLegend?: boolean; className?: string }) {
  const amount = Number(po.amount);
  const consumed = Number(po.consumed);
  const reserved = Number(po.reserved);
  const over = Number(po.remaining) < 0;
  const scale = Math.max(amount, consumed + reserved, 1);
  const w = (v: number) => `${Math.max(0, Math.min(100, (v / scale) * 100))}%`;
  const seg = (k: string) => statusDef("consumption", k).style;
  const parts = [
    { k: "consumed", v: consumed },
    { k: "reserved", v: reserved },
    { k: over ? "over" : "remaining", v: Math.abs(Number(po.remaining)) },
  ];
  return (
    <div className={cn("min-w-[180px]", className)}>
      <Tip content={parts.map((p) => `${statusDef("consumption", p.k).label} ${money(p.v, po.currency)}`).join(" · ")}>
        <div className="relative flex h-2 overflow-hidden rounded-full bg-muted">
          <div className={cn("h-full", seg("consumed").dot)} style={{ width: w(consumed) }} />
          <div className={cn("h-full", seg("reserved").dot)} style={{ width: w(reserved) }} />
          {!over && <div className={cn("h-full opacity-50", seg("remaining").dot)} style={{ width: w(Number(po.remaining)) }} />}
          {over && <div className="absolute inset-y-0 border-l-2 border-foreground/70" style={{ left: w(amount) }} aria-hidden />}
        </div>
      </Tip>
      {showLegend && (
        <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground tabular">
          {parts.map((p) => (
            <span key={p.k} className="inline-flex items-center gap-1">
              <span className={cn("size-2 rounded-full", seg(p.k).dot, p.k === "remaining" && "opacity-50")} />
              {statusDef("consumption", p.k).label} {money(p.v, po.currency)}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function POsPage() {
  const navigate = useNavigate();
  const q = useQuery({ queryKey: ["pos"], queryFn: async () => unwrap(await api.GET("/api/pos")) });
  const cols = useMemo<ColumnDef<PO, unknown>[]>(
    () => [
      { id: "po", header: "PO", accessorFn: (p) => p.po_number, filterFn: (r, id, f) => text(r.getValue(id), f), cell: ({ getValue }) => <span className="font-medium tabular">{String(getValue())}</span> },
      { id: "vendor", header: "Vendor", accessorFn: (p) => p.vendor_name ?? "", filterFn: (r, id, f) => text(r.getValue(id), f), cell: ({ getValue }) => <div className="max-w-[200px] truncate">{String(getValue() || "—")}</div> },
      { id: "amount", header: "Amount", accessorFn: (p) => Number(p.amount), enableColumnFilter: false, meta: { align: "right" }, cell: ({ row }) => money(row.original.amount, row.original.currency) },
      {
        id: "bar",
        header: "Consumed · reserved · remaining",
        enableSorting: false,
        enableColumnFilter: false,
        size: 280,
        cell: ({ row }) => <ConsumptionBar po={row.original} />,
      },
      {
        id: "remaining",
        header: "Remaining",
        accessorFn: (p) => Number(p.remaining),
        enableColumnFilter: false,
        meta: { align: "right" },
        cell: ({ row }) => (
          <span className={cn("font-medium", Number(row.original.remaining) < 0 && statusDef("consumption", "over").style.text)}>{money(row.original.remaining, row.original.currency)}</span>
        ),
      },
      { id: "status", header: "Status", accessorFn: (p) => p.status, filterFn: (r, id, f) => text(r.getValue(id), f), cell: ({ getValue }) => <Badge>{String(getValue())}</Badge> },
    ],
    [],
  );
  return (
    <div>
      <PageHeader
        title="Purchase orders"
        description="How much of each PO is consumed by approved invoices, reserved by invoices in flight, and still open."
        actions={
          <Button variant="outline" size="sm" asChild>
            <a href={apiUrl("/api/master/purchase_orders.csv")}>
              <Download /> Export CSV
            </a>
          </Button>
        }
      />
      {q.error && <ErrorNote error={q.error} />}
      <DataTable id="pos" data={q.data} loading={q.isLoading} columns={cols} onRowClick={(p) => navigate(`/pos/${p.id}`)} />
    </div>
  );
}

export function PODetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const q = useQuery({ queryKey: ["po", Number(id)], queryFn: async () => unwrap(await api.GET("/api/pos/{po_id}", { params: { path: { po_id: Number(id) } } })) });
  const po = q.data;
  if (q.error) return <ErrorNote error={q.error} />;
  if (!po)
    return (
      <div className="space-y-4">
        <Skeleton className="h-7 w-64" />
        <Skeleton className="h-32 rounded-lg" />
        <Skeleton className="h-64 rounded-lg" />
      </div>
    );
  const lineNo = new Map(po.lines.map((l) => [l.id, l.line_no]));
  const byLine = (lineId: number) => po.consumption.filter((c) => c.po_line_id === lineId && c.state !== "released").reduce((s, c) => s + Number(c.qty ?? 0), 0);
  const splits = po.consumption.filter((c) => c.split_from);

  return (
    <div>
      <Link to="/pos" className="mb-3 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-3.5" /> Purchase orders
      </Link>
      <PageHeader
        eyebrow={po.vendor_name}
        title={po.po_number}
        description={po.notes ?? undefined}
        actions={<Badge>{po.status}</Badge>}
      />
      <Card className="mb-4">
        <CardContent className="grid gap-6 pt-5 md:grid-cols-[1fr_2fr]">
          <div className="grid grid-cols-2 gap-4">
            {(
              [
                ["PO amount", po.amount],
                ["Consumed", po.consumed],
                ["Reserved", po.reserved],
                ["Remaining", po.remaining],
              ] as const
            ).map(([l, v]) => (
              <div key={l}>
                <div className="text-[11px] text-muted-foreground">{l}</div>
                <div className={cn("mt-0.5 text-lg font-semibold tabular", l === "Remaining" && Number(v) < 0 && statusDef("consumption", "over").style.text)}>{money(v, po.currency)}</div>
              </div>
            ))}
          </div>
          <div className="flex flex-col justify-center">
            <ConsumptionBar po={po} showLegend />
            {splits.length > 0 && (
              <div className="mt-3 inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                <GitFork className="size-3.5" /> {splits.length} invoice(s) here came from a split bundle
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-1.5">
              <ListOrdered className="size-4" /> Lines
            </CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto px-0 pb-2">
            <table className="w-full text-[13px]">
              <thead className="text-xs text-muted-foreground">
                <tr className="border-b">
                  <th className="px-5 py-2 text-left font-medium">#</th>
                  <th className="py-2 text-left font-medium">Description</th>
                  <th className="py-2 text-right font-medium">Qty</th>
                  <th className="whitespace-nowrap py-2 pl-3 text-right font-medium">Invoiced qty</th>
                  <th className="whitespace-nowrap py-2 pl-3 text-right font-medium">Unit price</th>
                  <th className="px-5 py-2 text-right font-medium">Amount</th>
                </tr>
              </thead>
              <tbody>
                {po.lines.map((l) => (
                  <tr key={l.id} className="border-b last:border-0">
                    <td className="px-5 py-2 tabular text-muted-foreground">{l.line_no}</td>
                    <td className="py-2">
                      {l.description}
                      {l.gl_code && <div className="text-xs text-muted-foreground">GL {l.gl_code}</div>}
                    </td>
                    <td className="py-2 text-right tabular">{Number(l.qty)}</td>
                    <td className="py-2 text-right tabular text-muted-foreground">{byLine(l.id) || "—"}</td>
                    <td className="py-2 text-right tabular">{plainMoney(l.unit_price)}</td>
                    <td className="px-5 py-2 text-right font-medium tabular">{plainMoney(l.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-1.5">
                <Receipt className="size-4" /> Linked invoices
              </CardTitle>
              <CardDescription>Every invoice that consumed or reserved part of this PO.</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="overflow-x-auto px-0 pb-2">
            {po.consumption.length === 0 ? (
              <div className="px-5 text-[13px] text-muted-foreground">No invoices against this PO yet.</div>
            ) : (
              <table className="w-full text-[13px]">
                <thead className="text-xs text-muted-foreground">
                  <tr className="border-b">
                    <th className="px-5 py-2 text-left font-medium">Case</th>
                    <th className="py-2 text-left font-medium">Invoice #</th>
                    <th className="py-2 pl-4 text-left font-medium">Line</th>
                    <th className="py-2 text-left font-medium">State</th>
                    <th className="px-5 py-2 text-right font-medium">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {po.consumption.map((c) => (
                    <tr key={c.id} className="cursor-pointer border-b last:border-0 hover:bg-muted/50" onClick={() => navigate(`/cases/${c.case_id}/review`)}>
                      <td className="px-5 py-2">
                        <span className="font-medium tabular">{c.case_ref ?? `#${c.case_id}`}</span>
                        {c.split_from && (
                          <Tip content={`Split from bundle ${c.split_from}`}>
                            <Badge className="ml-2 px-1.5 py-0 text-[10px]">
                              <GitFork className="size-3" /> split
                            </Badge>
                          </Tip>
                        )}
                        {c.case_status && <StatusBadge kind="case" value={c.case_status} className="ml-2" />}
                      </td>
                      <td className="py-2 tabular text-muted-foreground">{c.invoice_number ?? "—"}</td>
                      <td className="py-2 pl-4 tabular text-muted-foreground">{c.po_line_id ? `#${lineNo.get(c.po_line_id) ?? "?"}${c.qty ? ` · ${Number(c.qty)}` : ""}` : "header"}</td>
                      <td className="py-2">
                        <StatusBadge kind="consumption" value={c.state} />
                      </td>
                      <td className={cn("px-5 py-2 text-right tabular", c.state === "released" && "text-muted-foreground line-through")}>{money(c.amount, po.currency)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>

        {po.amendments.length > 0 && (
          <Card className="xl:col-span-2">
            <CardHeader>
              <div>
                <CardTitle className="flex items-center gap-1.5">
                  <History className="size-4" /> Amendments
                </CardTitle>
                <CardDescription>Changes to the PO's master amount, from approved reviewer remarks or a CSV import.</CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              <ul className="space-y-2 text-[13px]">
                {po.amendments.map((a) => (
                  <li key={a.id} className="flex flex-wrap items-baseline gap-x-2">
                    <span className="font-medium tabular">
                      {plainMoney(a.old_amount)} → {plainMoney(a.new_amount)}
                    </span>
                    <span className="text-muted-foreground">
                      by {a.amended_by} · {a.source} · {dateTime(a.created_at)}
                    </span>
                    {a.reason && <span className="w-full text-muted-foreground">“{a.reason}”</span>}
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
