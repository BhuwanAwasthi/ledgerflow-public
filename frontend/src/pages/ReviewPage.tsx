import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Library, Columns2, Download, FileSearch, GitCompareArrows, History, ListChecks, Loader2, Table2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { api, unwrap, type GridOp, type Workspace } from "@/api/client";
import { FileLink } from "@/components/FileLink";
import { ErrorNote, StatusBadge } from "@/components/common";
import { ActionBar, type Draft, type ReviewActionName } from "@/components/review/ActionBar";
import { ComparePanel, invoiceDocumentId } from "@/components/review/ComparePanel";
import { DecisionBar } from "@/components/review/DecisionBar";
import { boxesFrom, PdfViewer } from "@/components/review/PdfViewer";
import { HistoryTab, MatchTab, RulesTab } from "@/components/review/ReviewTabs";
import { opsFrom, TemplateGrid, type PendingEdits } from "@/components/review/TemplateGrid";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/primitives";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useConfig } from "@/lib/config";
import { caseLink } from "@/lib/nav";
import { dateTime, money } from "@/lib/format";
import { useCaseParam } from "@/hooks/useCaseParam";
import { useActor } from "@/lib/auth";
import { ACTIVE_CASE_STATUSES, toneStyle } from "@/lib/status";
import { cn } from "@/lib/utils";
import { SampleBadge } from "@/components/caseColumns";

type Tab = "template" | "rules" | "match" | "history";

/** Header fields (not line/po/vendor bindings) behind the flagged cells, so the PDF can mark them amber too. */
function flaggedFields(ws: Workspace | undefined): Set<string> {
  const out = new Set<string>();
  if (!ws) return out;
  const byHeader = new Map(ws.columns.map((c) => [c.header, c.binds]));
  ws.cells.forEach((f) => (byHeader.get(f.column) ?? []).forEach((b) => !b.startsWith("lines.") && b !== "po" && b !== "vendor" && out.add(b)));
  return out;
}

export default function ReviewPage() {
  const param = useCaseParam();
  const id = param.id ?? NaN;
  const qc = useQueryClient();
  const { data: cfg } = useConfig();
  const [params, setParams] = useSearchParams();
  const [asked, setAsked] = useState(false);
  const actor = useActor();

  const detail = useQuery({
    queryKey: ["case", id],
    queryFn: async () => unwrap(await api.GET("/api/cases/{case_id}", { params: { path: { case_id: id } } })),
    enabled: Number.isFinite(id),
    refetchInterval: (q) => (q.state.data && (ACTIVE_CASE_STATUSES.has(q.state.data.status) || (asked && q.state.data.status !== "on_hold")) ? 1200 : false),
  });
  const c = detail.data;
  const ws = useQuery({
    queryKey: ["workspace", id, c?.updated_at],
    queryFn: async () => unwrap(await api.GET("/api/cases/{case_id}/workspace", { params: { path: { case_id: id } } })),
    enabled: !!c,
    placeholderData: (prev) => prev,
  });
  const w = ws.data;

  const [tab, setTab] = useState<Tab>("template");
  const [active, setActive] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingEdits>({});
  const processing = !!c && ACTIVE_CASE_STATUSES.has(c.status);
  const currency = c?.currency ?? cfg?.rules.currency ?? "";
  const latestTemplate = c?.templates[c.templates.length - 1];

  // tell the reviewer what the reconciler did once the rerun settles
  const wasProcessing = useRef(false);
  useEffect(() => {
    if (!c) return;
    if (processing) {
      wasProcessing.current = true;
      return;
    }
    if (!wasProcessing.current) return;
    wasProcessing.current = false;
    const last = [...c.review_actions].reverse().find((a) => a.action === "remark");
    const cs = last?.changeset as { status?: string; question?: string } | null | undefined;
    if (cs?.status === "needs_clarification") {
      toast.warning("The reconciler needs a clarification", { description: cs.question, duration: 10_000 });
      setTab("history");
    } else if (c.status === "needs_review" && latestTemplate) {
      toast.success(`Changes applied: template v${latestTemplate.version} is ready`, { description: c.decision_summary ?? undefined });
      setTab("template");
    } else if (c.status === "approved") {
      toast.success(`${c.ref} approved`, { description: "Template frozen and the PO reservation consumed." });
    }
  }, [c, processing, latestTemplate]);

  const review = useMutation({
    mutationFn: async (b: { action: ReviewActionName; text: string | null; ops?: GridOp[] }) =>
      unwrap(await api.POST("/api/cases/{case_id}/review", { params: { path: { case_id: id } }, body: { action: b.action, text: b.text, ops: b.ops ?? null, actor } })),
    onSuccess: (_r, b) => {
      if (b.action === "remark") setPending({});
      if (b.action === "request_info") setAsked(true);
      const label = { approve: "Approving", remark: "Applying your changes", reject: "Rejecting", request_info: "Putting the case on hold" }[b.action];
      toast.message(`${label}…`, { description: "The workflow picks it up now." });
      qc.invalidateQueries({ queryKey: ["case", id] });
    },
    onError: (e) => toast.error("Couldn't submit", { description: e instanceof Error ? e.message : undefined }),
  });

  const rows = (latestTemplate?.rows ?? []) as Record<string, string>[];
  const pendingOps = useMemo(() => (w ? opsFrom(pending, w, rows) : []), [pending, w, rows]);
  const flagged = useMemo(() => flaggedFields(w), [w]);
  const boxes = useMemo(() => boxesFrom(c?.extraction?.field_meta as Record<string, unknown> | undefined, flagged), [c?.extraction, flagged]);
  const duplicates = w?.duplicates ?? [];
  const view = params.get("view") ?? (duplicates.length ? "compare" : "review");
  const draft = (w?.draft as Draft | null | undefined) ?? null;
  const holdDraft = draft && draft.kind !== "rejection" ? draft : null;

  if (param.error || detail.error) return <ErrorNote error={param.error ?? detail.error} />;
  if (!c || !w) return <WorkspaceSkeleton />;

  const counts = {
    rules: c.rule_results.filter((r) => r.status === "fail").length,
    // invoice lines with a line-level rule issue (unmatched, price, GRN)
    match: new Set(w.cells.filter((f) => f.kind === "rule" && w.columns.find((col) => col.header === f.column)?.binds.some((b) => b.startsWith("lines."))).map((f) => f.row)).size,
  };
  const violet = toneStyle("violet");
  const final = !processing && !w.reviewable && c.status !== "on_hold";
  const closedLine = final ? (
    <>
      <StatusBadge kind="case" value={c.status} />
      <span className="text-muted-foreground">
        {c.approved_at
          ? `Approved by ${c.approved_by ?? "auto"} · ${dateTime(c.approved_at)}${c.exported_at ? ` · exported ${dateTime(c.exported_at)}` : ""}`
          : duplicates[0]
            ? `Duplicate of ${duplicates[0].ref ?? `case ${duplicates[0].case_id}`}: nothing will be paid twice.`
            : (c.decision_summary ?? "").split(/(?<=\.)\s+/)[0]}
      </span>
      {c.approved_at && (
        <Button size="sm" variant="outline" className="ml-auto" asChild>
          <Link to="/approved">
            <Library /> Approved library
          </Link>
        </Button>
      )}
    </>
  ) : undefined;

  return (
    // lg: the workspace fits the viewport (top bar 3.5rem + main padding 4rem): header, decision bar, the two
    // panes taking the rest, action bar last, so a 1280x720 screen share shows PDF, grid and actions at once
    <div className="flex flex-col gap-3 lg:h-[calc(100vh-7.5rem)] lg:min-h-[540px]">
      <div className="flex shrink-0 items-center gap-3">
        <FileSearch className="size-4 text-muted-foreground" aria-hidden />
        <h1 className="text-lg font-semibold tracking-tight tabular">{c.ref}</h1>
        {c.is_sample && <SampleBadge />}
        <StatusBadge kind="case" value={c.status} pulse={processing} />
        <span className="min-w-0 flex-1 truncate text-[13px] text-muted-foreground">
          {[c.vendor_name ?? c.submitter_name, c.invoice_number && `invoice ${c.invoice_number}`, c.submitter_email && `from ${c.submitter_email}`].filter(Boolean).join(" · ")}
        </span>
        <div className="flex shrink-0 items-center gap-2">
          {duplicates.length > 0 && (
            <Button variant="outline" size="sm" onClick={() => setParams(view === "compare" ? { view: "review" } : {}, { replace: true })}>
              {view === "compare" ? <Columns2 /> : <GitCompareArrows />} {view === "compare" ? "Review view" : "Compare duplicate"}
            </Button>
          )}
          <Button variant="outline" size="sm" asChild>
            <Link to={caseLink({ id, ref: c?.ref })}>
              <Activity /> Live run
            </Link>
          </Button>
          <Button variant="outline" size="sm" asChild>
            <FileLink path={`/api/cases/${id}/audit-pack`}>
              <Download /> Audit pack
            </FileLink>
          </Button>
        </div>
      </div>

      <div className="shrink-0">
        <DecisionBar
          c={c}
          balance={w.po_balance}
          currency={currency}
          lowConfidenceBelow={cfg?.app.ui.review?.low_confidence_below}
          callouts={c.rule_results
            .filter((r) => r.status === "fail" && cfg?.app.ui.review?.callout_rules?.includes(r.rule_id))
            .map((r) => ({ r, name: cfg?.rules.rules.find((x) => x.id === r.rule_id)?.name ?? r.rule_id }))}
        />
      </div>

      {processing && (
        <div className={cn("flex shrink-0 items-center gap-2.5 rounded-lg border px-4 py-2 text-[13px]", violet.border, violet.soft)}>
          <Loader2 className={cn("size-4 animate-spin", violet.text)} />
          <span>
            <span className="font-medium">{c.status_label ?? "Processing"}…</span> The checks, decision and template run again; this page updates when they finish.
          </span>
          <Link to={caseLink({ id, ref: c?.ref })} className="ml-auto text-xs font-medium text-primary hover:underline">
            Watch live
          </Link>
        </div>
      )}

      {view === "compare" && duplicates[0] ? (
        <div className="min-h-0 flex-1 overflow-auto">
          <ComparePanel current={c} link={duplicates[0]} currency={currency} />
        </div>
      ) : (
        <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
          <Card className="flex h-[70vh] min-h-0 flex-col overflow-hidden lg:h-auto">
            <PdfViewer caseId={id} documents={c.documents} invoiceDocId={invoiceDocumentId(c)} boxes={boxes} active={active} onHover={setActive} className="min-h-0 flex-1" />
          </Card>

          <Card className={cn("flex h-[70vh] min-h-0 flex-col overflow-hidden lg:h-auto", processing && "opacity-80")}>
            <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)} className="flex min-h-0 flex-1 flex-col">
              <TabsList>
                <TabsTrigger value="template">
                  <Table2 /> Template
                </TabsTrigger>
                <TabsTrigger value="rules">
                  <ListChecks /> Rules <Count n={counts.rules} />
                </TabsTrigger>
                <TabsTrigger value="match">
                  <Columns2 /> Match <Count n={counts.match} />
                </TabsTrigger>
                <TabsTrigger value="history">
                  <History /> History <Count n={c.templates.length + c.review_actions.filter((a) => a.action !== "override").length} muted />
                </TabsTrigger>
              </TabsList>
              <TabsContent value="template" className="min-h-0 flex-1 overflow-auto">
                <TemplateGrid caseId={id} ws={w} templates={c.templates} pending={pending} onPendingChange={setPending} activeField={active} onHoverField={setActive} />
              </TabsContent>
              <TabsContent value="rules" className="min-h-0 flex-1 overflow-auto">
                <RulesTab results={c.rule_results} cfg={cfg} />
              </TabsContent>
              <TabsContent value="match" className="min-h-0 flex-1 overflow-auto">
                <MatchTab ws={w} currency={currency} />
              </TabsContent>
              <TabsContent value="history" className="min-h-0 flex-1 overflow-auto">
                <HistoryTab detail={c} />
              </TabsContent>
            </Tabs>
          </Card>
        </div>
      )}

      {/* last row of the viewport layout on lg; sticky to the screen bottom when the page scrolls (narrow screens) */}
      <div className="sticky bottom-3 z-20 shrink-0 overflow-hidden rounded-lg border bg-card/95 shadow-[0_8px_30px_-6px_rgba(16,24,40,0.25)] backdrop-blur lg:static lg:shadow-sm">
        <ActionBar
          caseRef={c.ref ?? `case ${id}`}
          totalText={money(c.total, currency)}
          versionNo={latestTemplate?.version ?? null}
          reviewable={w.reviewable && !processing}
          busy={review.isPending || processing}
          pendingOps={pendingOps}
          onDiscardEdits={() => setPending({})}
          onSubmit={async (action, text, ops) => {
            await review.mutateAsync({ action, text, ops });
          }}
          draft={holdDraft}
          drafting={asked && c.status !== "on_hold"}
          onHold={c.status === "on_hold"}
          closed={closedLine}
        />
      </div>
    </div>
  );
}

function Count({ n, muted }: { n: number; muted?: boolean }) {
  if (!n) return null;
  return (
    <span className={cn("ml-0.5 rounded-full px-1.5 text-[11px] font-semibold tabular", muted ? "bg-muted text-muted-foreground" : toneStyle("amber").badge)}>{n}</span>
  );
}

function WorkspaceSkeleton() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-7 w-64" />
      <Skeleton className="h-28 rounded-lg" />
      <div className="grid gap-4 lg:grid-cols-[2fr_3fr]">
        <Skeleton className="h-[60vh] rounded-lg" />
        <Skeleton className="h-[60vh] rounded-lg" />
      </div>
    </div>
  );
}
