import type { ColumnDef } from "@tanstack/react-table";
import type { Case } from "@/api/client";
import { StatusBadge } from "@/components/common";
import { Badge } from "@/components/ui/primitives";
import { money, relative } from "@/lib/format";

type Col = ColumnDef<Case, unknown>;

const text = (v: unknown, filter: unknown) => String(v ?? "").toLowerCase().includes(String(filter).toLowerCase());

export const caseCols = {
  ref: {
    id: "ref",
    header: "Case",
    accessorFn: (c) => c.ref ?? `#${c.id}`,
    filterFn: (row, id, f) => text(row.getValue(id), f),
    cell: ({ row }) => (
      <div className="flex items-center gap-2 whitespace-nowrap">
        <span className="font-medium tabular text-foreground">{row.original.ref}</span>
        {row.original.seed && <Badge className="px-1.5 py-0 text-[10px]">seeded</Badge>}
      </div>
    ),
  } satisfies Col,
  refCompact: {
    id: "ref",
    header: "Case",
    accessorFn: (c) => c.ref ?? `#${c.id}`,
    filterFn: (row, id, f) => text(row.getValue(id), f),
    cell: ({ row }) => (
      <span className="inline-flex items-center gap-1.5 whitespace-nowrap font-medium tabular" title={row.original.seed ? "Seeded demo data" : undefined}>
        {row.original.ref}
        {row.original.seed && <span className="size-1.5 rounded-full bg-muted-foreground/40" aria-label="seeded demo data" />}
      </span>
    ),
  } satisfies Col,
  vendor: {
    id: "vendor",
    header: "Vendor",
    accessorFn: (c) => c.vendor_name ?? c.submitter_name ?? "",
    filterFn: (row, id, f) => text(row.getValue(id), f),
    cell: ({ row }) => (
      <div className="max-w-[180px] truncate">
        {row.original.vendor_name ?? <span className="text-muted-foreground">{row.original.submitter_name ?? "—"}</span>}
      </div>
    ),
  } satisfies Col,
  invoice: {
    id: "invoice",
    header: "Invoice #",
    accessorFn: (c) => c.invoice_number ?? "",
    filterFn: (row, id, f) => text(row.getValue(id), f),
    cell: ({ getValue }) => <span className="tabular text-muted-foreground">{String(getValue() || "—")}</span>,
  } satisfies Col,
  total: {
    id: "total",
    header: "Total",
    accessorFn: (c) => (c.total === null ? null : Number(c.total)),
    enableColumnFilter: false,
    meta: { align: "right" },
    cell: ({ row }) => <span className="tabular font-medium">{money(row.original.total, row.original.currency ?? "USD")}</span>,
  } satisfies Col,
  status: {
    id: "status",
    header: "Status",
    accessorFn: (c) => c.status,
    filterFn: (row, id, f) => text(row.getValue(id), f),
    cell: ({ row }) => <StatusBadge kind="case" value={row.original.status} pulse={row.original.status === "processing"} />,
  } satisfies Col,
  decision: {
    id: "decision",
    header: "Decision",
    accessorFn: (c) => c.decision ?? "",
    filterFn: (row, id, f) => text(row.getValue(id), f),
    cell: ({ row }) => (row.original.decision ? <StatusBadge kind="decision" value={row.original.decision} /> : <span className="text-muted-foreground">—</span>),
  } satisfies Col,
  stage: {
    id: "stage",
    header: "Stage",
    accessorFn: (c) => c.status_label ?? "",
    filterFn: (row, id, f) => text(row.getValue(id), f),
    cell: ({ getValue }) => <span className="text-muted-foreground">{String(getValue() || "—")}</span>,
  } satisfies Col,
  reason: {
    id: "reason",
    header: "Main reason",
    accessorFn: (c) => c.decision_summary ?? "",
    filterFn: (row, id, f) => text(row.getValue(id), f),
    cell: ({ getValue }) => <div className="line-clamp-1 max-w-[420px] text-muted-foreground">{String(getValue() || "—")}</div>,
  } satisfies Col,
  created: {
    id: "created",
    header: "Received",
    accessorFn: (c) => c.created_at,
    enableColumnFilter: false,
    cell: ({ getValue }) => <span className="whitespace-nowrap tabular text-muted-foreground">{relative(String(getValue()))}</span>,
  } satisfies Col,
  sla: {
    id: "sla",
    header: "SLA",
    accessorFn: (c) => c.sla_due_at ?? "",
    enableColumnFilter: false,
    cell: ({ row }) => {
      const due = row.original.sla_due_at;
      if (!due) return <span className="text-muted-foreground">—</span>;
      const late = new Date(due).getTime() < Date.now();
      return <span className={late ? "font-medium text-destructive" : "tabular text-muted-foreground"}>{late ? `overdue ${relative(due).replace(" ago", "")}` : `due ${relative(due)}`}</span>;
    },
  } satisfies Col,
};
