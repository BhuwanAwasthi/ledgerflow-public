import { useQuery } from "@tanstack/react-query";
import { Inbox } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api, unwrap, type Case } from "@/api/client";
import { caseCols } from "@/components/caseColumns";
import { DataTable } from "@/components/DataTable";
import { EmptyState, ErrorNote, PageHeader } from "@/components/common";

/** All cases, or the reviewer work queue (needs review + on hold, oldest SLA first). */
export default function CasesPage({ queue }: { queue?: boolean }) {
  const navigate = useNavigate();
  const q = useQuery({
    queryKey: ["cases", queue ? "queue" : "all"],
    queryFn: async () =>
      unwrap(await api.GET("/api/cases", { params: { query: { status: queue ? "needs_review,on_hold" : undefined, page_size: 200 } } })).items,
    refetchInterval: 10_000,
  });
  const rows: Case[] | undefined = queue
    ? q.data?.slice().sort((a, b) => (a.sla_due_at ?? "9").localeCompare(b.sla_due_at ?? "9"))
    : q.data?.filter((c) => c.status !== "split");
  const open = (c: Case) => navigate(c.status === "needs_review" ? `/cases/${c.id}/review` : `/cases/${c.id}/live`);

  return (
    <div>
      <PageHeader
        title={queue ? "Work queue" : "All cases"}
        description={queue ? "Cases waiting for a reviewer or for information, oldest SLA first." : "Every invoice the engine has seen."}
      />
      {q.error && <ErrorNote error={q.error} />}
      {queue && !q.isLoading && rows?.length === 0 ? (
        <EmptyState icon={Inbox} title="Queue is clear">
          Nothing needs review or is on hold.
        </EmptyState>
      ) : (
        <DataTable
          id={queue ? "queue" : "cases"}
          data={rows}
          loading={q.isLoading}
          onRowClick={open}
          columns={
            queue
              ? [caseCols.ref, caseCols.vendor, caseCols.total, caseCols.status, caseCols.reason, caseCols.sla]
              : [caseCols.ref, caseCols.vendor, caseCols.invoice, caseCols.total, caseCols.decision, caseCols.status, caseCols.stage, caseCols.created]
          }
        />
      )}
    </div>
  );
}
