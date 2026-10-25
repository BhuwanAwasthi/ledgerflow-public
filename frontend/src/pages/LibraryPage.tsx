import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Archive, Download, FileArchive, FileSpreadsheet, FileText, History, Library, Loader2, PanelRightOpen, TriangleAlert } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { api, downloadFile, unwrap, type Case, type ExportBatch } from "@/api/client";
import { FileLink } from "@/components/FileLink";
import { caseCols } from "@/components/caseColumns";
import { DataTable } from "@/components/DataTable";
import { EmptyState, ErrorNote, PageHeader, StatusBadge } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle, Tip } from "@/components/ui/overlays";
import { Checkbox, Segmented, Select } from "@/components/ui/primitives";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useConfig } from "@/lib/config";
import { dateTime, money, relative } from "@/lib/format";
import { useActor } from "@/lib/auth";
import { toneStyle } from "@/lib/status";
import { cn } from "@/lib/utils";
import { caseLink } from "@/lib/nav";

type Filter = "false" | "true" | "all";

/** Exact sum of decimal money strings (display only): integer cents, never float addition. */
function toCents(v: string | null | undefined): bigint {
  const m = /^(-?)(\d+)(?:\.(\d{0,2})\d*)?$/.exec(String(v ?? "0").trim());
  if (!m) return 0n;
  const cents = BigInt(m[2]) * 100n + BigInt((m[3] ?? "").padEnd(2, "0") || "0");
  return m[1] ? -cents : cents;
}
function centsToDecimal(c: bigint): string {
  const neg = c < 0n;
  const a = neg ? -c : c;
  return `${neg ? "-" : ""}${a / 100n}.${String(a % 100n).padStart(2, "0")}`;
}
type Mode = "zip" | "batch";

function ExportHistory({ refs }: { refs: Map<number, string> }) {
  const q = useQuery({ queryKey: ["exports"], queryFn: async () => unwrap(await api.GET("/api/exports")) });
  const cols = useMemo<ColumnDef<ExportBatch, unknown>[]>(
    () => [
      {
        id: "ref",
        header: "Export",
        accessorFn: (b) => b.ref ?? `#${b.id}`,
        cell: ({ getValue }) => <span className="font-medium tabular">{String(getValue())}</span>,
      },
      {
        id: "kind",
        header: "Type",
        accessorFn: (b) => (b.filename?.endsWith(".zip") ? "ZIP of invoice files" : "Batch import file"),
        cell: ({ row, getValue }) => (
          <span className="inline-flex items-center gap-1.5 text-muted-foreground">
            {row.original.filename?.endsWith(".zip") ? <FileArchive className="size-3.5" /> : <FileSpreadsheet className="size-3.5" />}
            {String(getValue())}
          </span>
        ),
      },
      { id: "profile", header: "ERP profile", accessorFn: (b) => b.erp_profile, cell: ({ getValue }) => <span className="font-mono text-xs">{String(getValue())}</span> },
      {
        id: "invoices",
        header: "Invoices",
        accessorFn: (b) => b.case_ids.map((id) => refs.get(id) ?? `#${id}`).join(", "),
        cell: ({ row, getValue }) => (
          <Tip content={String(getValue())}>
            <span className="tabular">
              {row.original.case_ids.length} <span className="text-muted-foreground">· {String(getValue()).slice(0, 48)}{String(getValue()).length > 48 ? "…" : ""}</span>
            </span>
          </Tip>
        ),
      },
      { id: "by", header: "By", accessorFn: (b) => b.created_by ?? "—", cell: ({ getValue }) => <span className="text-muted-foreground">{String(getValue())}</span> },
      {
        id: "at",
        header: "Created",
        accessorFn: (b) => b.created_at,
        enableColumnFilter: false,
        cell: ({ getValue }) => <span className="whitespace-nowrap tabular text-muted-foreground">{dateTime(String(getValue()))}</span>,
      },
      {
        id: "dl",
        header: "",
        enableSorting: false,
        enableColumnFilter: false,
        cell: ({ row }) =>
          row.original.download_url ? (
            <Button size="xs" variant="outline" asChild>
              <FileLink path={row.original.download_url} aria-label={`Download ${row.original.ref}`}>
                <Download /> {row.original.filename}
              </FileLink>
            </Button>
          ) : null,
      },
    ],
    [refs],
  );
  return (
    <DataTable
      id="exports"
      data={q.data}
      loading={q.isLoading}
      columns={cols}
      empty={
        <EmptyState icon={History} title="No exports yet" className="border-0">
          Select approved invoices and export them; every export gets a reference (EXP-0001…) and can be downloaded again here.
        </EmptyState>
      }
    />
  );
}

export default function LibraryPage() {
  const qc = useQueryClient();
  const actor = useActor();
  const { data: cfg } = useConfig();
  const [params, setParams] = useSearchParams();
  const filter = (params.get("exported") as Filter | null) ?? "false";
  const tab = params.get("tab") ?? "invoices";
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [profileId, setProfileId] = useState<string>("");
  const [confirm, setConfirm] = useState<{ mode: Mode; refs: string[] } | null>(null);
  const profiles = Object.values(cfg?.erp_profiles ?? {});
  const profile = profileId || cfg?.app.app.default_erp_profile || "";

  const q = useQuery({
    queryKey: ["approved", filter],
    queryFn: async () =>
      unwrap(await api.GET("/api/approved", { params: { query: { exported: filter === "all" ? undefined : filter === "true", page_size: 200 } } })).items,
  });
  const all = useQuery({
    queryKey: ["approved", "all"],
    queryFn: async () => unwrap(await api.GET("/api/approved", { params: { query: { page_size: 200 } } })).items,
  });
  const refs = useMemo(() => new Map((all.data ?? []).map((c) => [c.id, c.ref ?? `#${c.id}`])), [all.data]);
  const rows = q.data;
  const visibleIds = (rows ?? []).map((c) => c.id);
  const chosen = (rows ?? []).filter((c) => selected.has(c.id));

  const exp = useMutation({
    mutationFn: async ({ mode, force }: { mode: Mode; force: boolean }) =>
      unwrap(
        await api.POST("/api/exports", {
          body: { case_ids: chosen.map((c) => c.id), erp_profile: profile || null, mode, force, actor },
        }),
      ),
    onSuccess: (b) => {
      setConfirm(null);
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ["approved"] });
      qc.invalidateQueries({ queryKey: ["exports"] });
      qc.invalidateQueries({ queryKey: ["cases"] });
      if (b.download_url) void downloadFile(b.download_url).catch(() => toast.error("Download failed: open it from the export history"));
      toast.success(`${b.ref} created`, { description: `${b.case_ids.length} invoice(s) · ${b.filename ?? ""}` });
    },
    onError: (e) => toast.error("Export failed", { description: e instanceof Error ? e.message : undefined }),
  });

  const start = (mode: Mode) => {
    const done = chosen.filter((c) => c.status === "exported" || c.exported_at).map((c) => c.ref ?? `#${c.id}`);
    if (done.length) setConfirm({ mode, refs: done });
    else exp.mutate({ mode, force: false });
  };

  const toggle = (id: number, on: boolean) => {
    const next = new Set(selected);
    if (on) next.add(id);
    else next.delete(id);
    setSelected(next);
  };
  const allOn = visibleIds.length > 0 && visibleIds.every((id) => selected.has(id));

  const cols = useMemo<ColumnDef<Case, unknown>[]>(
    () => [
      {
        id: "select",
        size: 36,
        enableSorting: false,
        enableColumnFilter: false,
        header: () => (
          <Checkbox
            label="Select all"
            checked={allOn}
            indeterminate={selected.size > 0}
            onChange={(on) => setSelected(on ? new Set(visibleIds) : new Set())}
          />
        ),
        cell: ({ row }) => <Checkbox label={`Select ${row.original.ref}`} checked={selected.has(row.original.id)} onChange={(on) => toggle(row.original.id, on)} />,
      },
      caseCols.ref,
      caseCols.vendor,
      caseCols.invoice,
      caseCols.total,
      {
        id: "approved",
        header: "Approved",
        accessorFn: (c) => c.approved_at ?? "",
        enableColumnFilter: false,
        cell: ({ row }) => (
          <div className="whitespace-nowrap text-muted-foreground">
            <span className="tabular">{relative(row.original.approved_at)}</span>
            <span className="ml-1.5 text-xs">by {row.original.approved_by === "auto" ? "auto-approve" : row.original.approved_by ?? "—"}</span>
          </div>
        ),
      },
      {
        id: "export",
        header: "Export",
        accessorFn: (c) => c.status,
        filterFn: (row, id, f) => String(row.getValue(id)).includes(String(f)),
        cell: ({ row }) =>
          row.original.status === "exported" ? (
            <Tip content={`Exported ${dateTime(row.original.exported_at)}`}>
              <span>
                <StatusBadge kind="export" value="exported" />
              </span>
            </Tip>
          ) : (
            <StatusBadge kind="export" value="approved" />
          ),
      },
      {
        id: "files",
        header: () => <span className="block text-right">Files</span>,
        enableSorting: false,
        enableColumnFilter: false,
        cell: ({ row }) => {
          const c = row.original;
          return (
            <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
              {c.latest_template_version && (
                <Tip content={`Download template v${c.latest_template_version} (ERP file)`}>
                  <Button size="icon-sm" variant="ghost" asChild>
                    <FileLink path={`/api/cases/${c.id}/template/${c.latest_template_version}/download`} aria-label={`Download ${c.ref} template`}>
                      <FileSpreadsheet />
                    </FileLink>
                  </Button>
                </Tip>
              )}
              <Tip content="Audit pack (PDF): decision, rule results, every stage event">
                <Button size="icon-sm" variant="ghost" asChild>
                  <FileLink path={`/api/cases/${c.id}/audit-pack`} aria-label={`Audit pack for ${c.ref}`}>
                    <FileText />
                  </FileLink>
                </Button>
              </Tip>
              <Tip content="Open the case">
                <Button size="icon-sm" variant="ghost" asChild>
                  <Link to={caseLink(c, "review")} aria-label={`Open ${c.ref}`}>
                    <PanelRightOpen />
                  </Link>
                </Button>
              </Tip>
            </div>
          );
        },
      },
    ],
    [selected, allOn, rows],
  );

  const total = centsToDecimal(chosen.reduce((s, c) => s + toCents(c.total), 0n));
  const currency = chosen[0]?.currency ?? cfg?.rules.currency ?? "USD";
  const amber = toneStyle("amber");
  const busy = exp.isPending;

  return (
    <div>
      <PageHeader
        title="Approved library"
        description="Approved invoices waiting for the ERP. Download them one by one, as a ZIP of per-invoice files, or as one batch import file."
      />
      <Tabs value={tab} onValueChange={(v) => setParams((p) => (p.set("tab", v), p), { replace: true })}>
        <TabsList className="mb-4 px-0">
          <TabsTrigger value="invoices">
            <Library /> Invoices
          </TabsTrigger>
          <TabsTrigger value="history">
            <History /> Export history
          </TabsTrigger>
        </TabsList>
        <TabsContent value="invoices">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Segmented<Filter>
              label="Export status"
              value={filter}
              onChange={(v) => {
                setSelected(new Set());
                setParams((p) => (p.set("exported", v), p), { replace: true });
              }}
              options={[
                { value: "false", label: "Not exported" },
                { value: "true", label: "Exported" },
                { value: "all", label: "All" },
              ]}
            />
            <div className="ml-auto flex flex-wrap items-center gap-2">
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                ERP profile
                <Select value={profile} onChange={(e) => setProfileId(e.target.value)} aria-label="ERP profile">
                  {profiles.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} ({p.file_format})
                    </option>
                  ))}
                </Select>
              </label>
              <Button variant="outline" size="sm" disabled={!chosen.length || busy} onClick={() => start("zip")}>
                {busy && exp.variables?.mode === "zip" ? <Loader2 className="animate-spin" /> : <Archive />} Download selected (ZIP)
              </Button>
              <Button size="sm" disabled={!chosen.length || busy} onClick={() => start("batch")}>
                {busy && exp.variables?.mode === "batch" ? <Loader2 className="animate-spin" /> : <FileSpreadsheet />} Export batch
              </Button>
            </div>
          </div>
          <div
            className={cn(
              "mb-3 flex h-9 items-center gap-3 rounded-md border px-3 text-[13px] transition-opacity",
              !chosen.length && "border-dashed text-muted-foreground",
            )}
            aria-live="polite"
          >
            {!chosen.length && <span>Select invoices to download them as a ZIP or export them as one ERP batch file.</span>}
            {chosen.length > 0 && <span className="font-medium tabular">{chosen.length} selected</span>}
            {chosen.length > 0 && <span className="tabular text-muted-foreground">{money(total, currency)}</span>}
            {chosen.some((c) => c.status === "exported") && (
              <span className={cn("inline-flex items-center gap-1 text-xs", amber.text)}>
                <TriangleAlert className="size-3.5" /> includes already-exported invoices
              </span>
            )}
            {chosen.length > 0 && (
              <Button size="xs" variant="ghost" className="ml-auto" onClick={() => setSelected(new Set())}>
                Clear selection
              </Button>
            )}
          </div>
          {q.error && <ErrorNote error={q.error} />}
          <DataTable
            id="library"
            data={rows}
            loading={q.isLoading}
            columns={cols}
            onRowClick={(c) => toggle(c.id, !selected.has(c.id))}
            maxHeight="calc(100vh - 330px)"
            empty={
              <EmptyState icon={Library} title={filter === "false" ? "Nothing waiting for export" : "No approved invoices"} className="border-0">
                {filter === "false" ? "Every approved invoice has been exported. Switch to Exported or All to download them again." : "Approve an invoice in the review workspace, or run a scenario that auto-approves."}
              </EmptyState>
            }
          />
        </TabsContent>
        <TabsContent value="history">
          <ExportHistory refs={refs} />
        </TabsContent>
      </Tabs>

      <Dialog open={!!confirm} onOpenChange={(o) => !o && setConfirm(null)}>
        <DialogContent>
          <div className="px-6 pb-5 pt-6">
            <DialogTitle className="flex items-center gap-2 text-base font-semibold">
              <TriangleAlert className={cn("size-4", amber.text)} /> Export again?
            </DialogTitle>
            <DialogDescription className="mt-1.5 text-[13px] text-muted-foreground">
              {confirm?.refs.length === 1 ? "This invoice was" : "These invoices were"} already exported. Importing them into the ERP twice would post the bill twice.
              Only continue if the earlier file was never imported.
            </DialogDescription>
            <div className={cn("mt-3 rounded-md border px-3 py-2 text-[13px] tabular", amber.border, amber.soft)}>{confirm?.refs.join(", ")}</div>
          </div>
          <div className="flex justify-end gap-2 border-t bg-muted/40 px-6 py-3">
            <Button variant="outline" onClick={() => setConfirm(null)}>
              Cancel
            </Button>
            <Button autoFocus disabled={busy} onClick={() => confirm && exp.mutate({ mode: confirm.mode, force: true })}>
              {busy && <Loader2 className="animate-spin" />} Export again
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
