import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import {
  ArrowLeft,
  Brain,
  Check,
  CheckCircle2,
  CircleAlert,
  Copy,
  Download,
  ExternalLink,
  FileCheck2,
  Inbox,
  Loader2,
  ShieldCheck,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { api, apiUrl, unwrap, type Vendor, type VendorRequest } from "@/api/client";
import { caseCols } from "@/components/caseColumns";
import { DataTable } from "@/components/DataTable";
import { EmptyState, ErrorNote, PageHeader, StatusBadge } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogTitle, Tip } from "@/components/ui/overlays";
import { Badge, Skeleton, Textarea } from "@/components/ui/primitives";
import { dateTime, humanise, relative } from "@/lib/format";
import { roleLabel, usePrefs } from "@/lib/prefs";
import { useConfig } from "@/lib/config";
import { statusDef } from "@/lib/status";
import { cn } from "@/lib/utils";

const text = (v: unknown, f: unknown) => String(v ?? "").toLowerCase().includes(String(f).toLowerCase());

export function VendorsPage() {
  const navigate = useNavigate();
  const q = useQuery({ queryKey: ["vendors"], queryFn: async () => unwrap(await api.GET("/api/vendors")) });
  const cols = useMemo<ColumnDef<Vendor, unknown>[]>(
    () => [
      { id: "code", header: "Code", accessorFn: (v) => v.code, filterFn: (r, id, f) => text(r.getValue(id), f), cell: ({ getValue }) => <span className="font-medium tabular">{String(getValue())}</span> },
      {
        id: "name",
        header: "Legal name",
        accessorFn: (v) => v.legal_name,
        filterFn: (r, id, f) => text(r.getValue(id), f),
        cell: ({ row }) => (
          <div className="min-w-0">
            <div className="truncate font-medium">{row.original.legal_name}</div>
            {row.original.aliases.length > 0 && <div className="truncate text-xs text-muted-foreground">aka {row.original.aliases.join(", ")}</div>}
          </div>
        ),
      },
      { id: "status", header: "Status", accessorFn: (v) => v.status, filterFn: (r, id, f) => text(r.getValue(id), f), cell: ({ getValue }) => <StatusBadge kind="vendor" value={String(getValue())} /> },
      { id: "category", header: "Category", accessorFn: (v) => v.category ?? "", filterFn: (r, id, f) => text(r.getValue(id), f), cell: ({ getValue }) => <span className="text-muted-foreground">{humanise(String(getValue()))}</span> },
      { id: "tax", header: "Tax ID", accessorFn: (v) => v.tax_id ?? "", cell: ({ getValue }) => <span className="tabular text-muted-foreground">{String(getValue() || "—")}</span> },
      { id: "bank", header: "Bank", accessorFn: (v) => v.bank_last4 ?? "", cell: ({ getValue }) => <span className="tabular text-muted-foreground">{getValue() ? `···${String(getValue())}` : "—"}</span> },
      { id: "gl", header: "Default GL", accessorFn: (v) => v.default_gl ?? "", cell: ({ getValue }) => <span className="tabular text-muted-foreground">{String(getValue() || "—")}</span> },
      {
        id: "nonpo",
        header: "Non-PO",
        accessorFn: (v) => (v.non_po_allowed ? "allowed" : "PO required"),
        cell: ({ row }) => <span className="text-muted-foreground">{row.original.non_po_allowed ? "allowed" : "PO required"}</span>,
      },
    ],
    [],
  );
  const pending = (q.data ?? []).filter((v) => v.status === "pending_docs").length;
  return (
    <div>
      <PageHeader
        title="Vendors"
        description="Vendor master. New vendors arrive as pending until their documents are verified and an admin approves them."
        actions={
          <>
            {pending > 0 && <StatusBadge kind="vendor" value="pending_docs" className="mr-1" />}
            <Button variant="outline" size="sm" asChild>
              <a href={apiUrl("/api/master/vendors.csv")}>
                <Download /> Export CSV
              </a>
            </Button>
          </>
        }
      />
      {q.error && <ErrorNote error={q.error} />}
      <DataTable id="vendors" data={q.data} loading={q.isLoading} columns={cols} onRowClick={(v) => navigate(`/vendors/${v.id}`)} />
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[11px] text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 truncate text-[13px] font-medium">{children}</dd>
    </div>
  );
}

function RequestCard({ r, labels }: { r: VendorRequest; labels: Record<string, string> }) {
  const portal = `${window.location.origin}/portal/${r.token}`;
  return (
    <div className="rounded-lg border">
      <div className="flex flex-wrap items-center gap-2 border-b px-4 py-2.5">
        <span className="text-[13px] font-medium">{r.kind === "onboarding" ? "Onboarding request" : "Information request"}</span>
        <StatusBadge kind="request" value={r.status} />
        <span className="text-xs text-muted-foreground">{dateTime(r.created_at)}</span>
        <div className="ml-auto flex gap-1">
          <Tip content="Copy the vendor's upload link">
            <Button
              size="icon-sm"
              variant="ghost"
              onClick={() => navigator.clipboard.writeText(portal).then(() => toast.success("Upload link copied"))}
              aria-label="Copy upload link"
            >
              <Copy />
            </Button>
          </Tip>
          <Tip content="Open the vendor portal page">
            <Button size="icon-sm" variant="ghost" asChild>
              <a href={`/portal/${r.token}`} target="_blank" rel="noreferrer" aria-label="Open portal">
                <ExternalLink />
              </a>
            </Button>
          </Tip>
        </div>
      </div>
      <div className="grid gap-4 px-4 py-3 md:grid-cols-2">
        <div>
          <div className="mb-1.5 text-xs font-medium text-muted-foreground">Documents</div>
          <ul className="space-y-1 text-[13px]">
            {r.docs_needed.map((d) => {
              const got = r.docs_received.includes(d);
              return (
                <li key={d} className="flex items-center gap-2">
                  {got ? <CheckCircle2 className={cn("size-4", statusDef("check", "pass").style.text)} /> : <span className="size-4 rounded-full border-2 border-dashed border-muted-foreground/40" />}
                  <span className={cn(!got && "text-muted-foreground")}>{labels[d] ?? humanise(d)}</span>
                  <span className="text-xs text-muted-foreground">{got ? "received" : "waiting"}</span>
                </li>
              );
            })}
          </ul>
        </div>
        <div>
          <div className="mb-1.5 text-xs font-medium text-muted-foreground">Verification checks</div>
          {r.checks.length === 0 ? (
            <div className="text-[13px] text-muted-foreground">Run when the vendor uploads.</div>
          ) : (
            <ul className="space-y-1.5">
              {r.checks.map((c, i) => (
                <li key={i} className="flex items-start gap-2 text-[13px]">
                  <StatusBadge kind="check" value={String(c.status)} className="mt-px" />
                  <div className="min-w-0">
                    <div>{String(c.detail ?? c.check)}</div>
                    <div className="truncate text-[11px] text-muted-foreground">{String(c.filename ?? "")}</div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

export function VendorDetailPage() {
  const { id } = useParams();
  const vendorId = Number(id);
  const qc = useQueryClient();
  const { role } = usePrefs();
  const navigate = useNavigate();
  const [rejectOpen, setRejectOpen] = useState(false);
  const [reason, setReason] = useState("");
  const q = useQuery({
    queryKey: ["vendor", vendorId],
    queryFn: async () => unwrap(await api.GET("/api/vendors/{vendor_id}", { params: { path: { vendor_id: vendorId } } })),
  });
  const done = (verb: string) => (r: { status: string; notes: string[]; resumed_case_ids: number[] }) => {
    qc.invalidateQueries({ queryKey: ["vendor", vendorId] });
    qc.invalidateQueries({ queryKey: ["vendors"] });
    setRejectOpen(false);
    const resumed = r.resumed_case_ids.length ? ` ${r.resumed_case_ids.length} paused case(s) resumed.` : "";
    toast.success(`Vendor ${verb}`, { description: [...r.notes, resumed].filter(Boolean).join(" ") || undefined });
  };
  const approve = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/vendors/{vendor_id}/approve", { params: { path: { vendor_id: vendorId } }, body: { actor: roleLabel(role) } })),
    onSuccess: done("approved"),
    onError: (e) => toast.error("Could not approve", { description: e instanceof Error ? e.message : undefined }),
  });
  const reject = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/api/vendors/{vendor_id}/reject", { params: { path: { vendor_id: vendorId } }, body: { actor: roleLabel(role), reason: reason || null } })),
    onSuccess: done("rejected"),
    onError: (e) => toast.error("Could not reject", { description: e instanceof Error ? e.message : undefined }),
  });
  const { data: cfg } = useConfig();
  const labels = Object.fromEntries((cfg?.document_types.labels ?? []).map((l) => [l.id, l.description]));

  const v = q.data;
  if (q.error) return <ErrorNote error={q.error} />;
  if (!v)
    return (
      <div className="space-y-4">
        <Skeleton className="h-7 w-64" />
        <Skeleton className="h-40 rounded-lg" />
        <Skeleton className="h-64 rounded-lg" />
      </div>
    );
  const openReqs = v.requests.filter((r) => r.status === "open" || r.status === "submitted");
  const checks = v.requests.flatMap((r) => r.checks);
  const failed = checks.filter((c) => c.status !== "pass");
  // only a vendor waiting on onboarding, or with docs just received, has something to decide
  const canDecide = v.status === "pending_docs" || openReqs.some((r) => r.status === "submitted");
  const hardFails = checks.filter((c) => c.status === "fail").length; // "manual" (a scan) is the admin's call
  const received = v.requests.some((r) => r.docs_received.length > 0);
  const approveBlocked = !received ? "No documents received yet" : hardFails ? `${hardFails} check(s) failed: review the documents, or reject` : null;
  const busy = approve.isPending || reject.isPending;

  return (
    <div>
      <Link to="/vendors" className="mb-3 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-3.5" /> Vendors
      </Link>
      <PageHeader
        eyebrow={<span className="tabular">{v.code}</span>}
        title={
          <span className="flex items-center gap-2">
            {v.legal_name} <StatusBadge kind="vendor" value={v.status} />
          </span>
        }
        description={v.aliases.length ? `Also invoices as ${v.aliases.join(", ")}` : undefined}
        actions={
          canDecide && (
            <>
              <Button variant="outline" disabled={busy} onClick={() => setRejectOpen(true)}>
                <X /> Reject
              </Button>
              <Tip content={approveBlocked ?? "Every verification check passed"}>
                <span>
                <Button disabled={busy || !!approveBlocked} onClick={() => approve.mutate()} className={statusDef("vendor", "approved").style.solid}>
                  {approve.isPending ? <Loader2 className="animate-spin" /> : <Check />} Approve vendor
                </Button>
                </span>
              </Tip>
            </>
          )
        }
      />

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-1">
          <CardHeader>
            <CardTitle>Master data</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
              <Field label="Tax ID">{v.tax_id ?? "—"}</Field>
              <Field label="Country">{v.country ?? "—"}</Field>
              <Field label="Email domain">{v.email_domain ?? "—"}</Field>
              <Field label="Bank account">{v.bank_last4 ? `···${v.bank_last4}` : "—"}</Field>
              <Field label="Category">{humanise(v.category)}</Field>
              <Field label="Default GL">{v.default_gl ?? "—"}</Field>
              <Field label="Non-PO invoices">{v.non_po_allowed ? "Allowed" : "PO required"}</Field>
              <Field label="In master since">{relative(v.created_at)}</Field>
            </dl>
          </CardContent>
        </Card>

        <Card className="xl:col-span-2">
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-1.5">
                <ShieldCheck className="size-4" /> Requests and verification
              </CardTitle>
              <CardDescription>Documents requested through the portal and the code checks run on each upload (name on the document vs legal name, tax ID format, bank account).</CardDescription>
            </div>
            {checks.length > 0 && (
              <Badge>
                {checks.length - failed.length}/{checks.length} checks pass
              </Badge>
            )}
          </CardHeader>
          <CardContent className="space-y-3">
            {v.requests.length === 0 ? (
              <EmptyState icon={FileCheck2} title="No document requests" className="py-8">
                This vendor was loaded from the master file; nothing was requested.
              </EmptyState>
            ) : (
              v.requests.map((r) => <RequestCard key={r.id} r={r} labels={labels} />)
            )}
          </CardContent>
        </Card>

        <Card className="xl:col-span-2">
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-1.5">
                <Inbox className="size-4" /> Invoices waiting
              </CardTitle>
              <CardDescription>Open cases from this vendor. Approving the vendor resumes the ones paused for onboarding.</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            {v.open_cases.length === 0 ? (
              <div className="text-[13px] text-muted-foreground">No open invoices.</div>
            ) : (
              <DataTable id="vendor-cases" data={v.open_cases} columns={[caseCols.ref, caseCols.invoice, caseCols.total, caseCols.status, caseCols.reason]} searchable={false} maxHeight="320px" onRowClick={(c) => navigate(`/cases/${c.id}/${c.status === "needs_review" ? "review" : "live"}`)} />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-1.5">
                <Brain className="size-4" /> Vendor memory
              </CardTitle>
              <CardDescription>Hints learned from reviewer corrections, given to extraction on this vendor's next invoices.</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            {v.memory.length === 0 ? (
              <div className="text-[13px] text-muted-foreground">Nothing learned yet.</div>
            ) : (
              <ul className="space-y-2 text-[13px]">
                {v.memory.map((m, i) => (
                  <li key={i} className="flex gap-2">
                    <CircleAlert className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" /> {m}
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      <Dialog open={rejectOpen} onOpenChange={setRejectOpen}>
        <DialogContent>
          <div className="space-y-3 px-6 pb-5 pt-6">
            <DialogTitle className="text-base font-semibold">Reject {v.legal_name}?</DialogTitle>
            <DialogDescription className="text-[13px] text-muted-foreground">The vendor stays out of the master and its open requests close. Say why for the audit trail.</DialogDescription>
            <Textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Bank letter name does not match the legal name" autoFocus />
          </div>
          <div className="flex justify-end gap-2 border-t bg-muted/40 px-6 py-3">
            <Button variant="outline" onClick={() => setRejectOpen(false)}>
              Cancel
            </Button>
            <Button disabled={!reason.trim() || busy} onClick={() => reject.mutate()} className={statusDef("vendor", "rejected").style.solid}>
              {reject.isPending && <Loader2 className="animate-spin" />} Reject vendor
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
