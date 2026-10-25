import { useMutation, useQuery } from "@tanstack/react-query";
import { Download, FileWarning, Loader2, Play } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { api, unwrap, type Fixture } from "@/api/client";
import { FileLink } from "@/components/FileLink";
import { ErrorNote, PageHeader, StatusBadge } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/primitives";
import { Tip } from "@/components/ui/overlays";
import { caseLink } from "@/lib/nav";

function expectedDecision(f: Fixture): string | null {
  const d = f.expect.decision;
  return typeof d === "string" ? d : null;
}

export default function ScenariosPage() {
  const navigate = useNavigate();
  const q = useQuery({ queryKey: ["fixtures"], queryFn: async () => unwrap(await api.GET("/api/fixtures")) });
  const run = useMutation({
    mutationFn: async (id: string) => unwrap(await api.POST("/api/fixtures/{scenario_id}/run", { params: { path: { scenario_id: id } } })),
    onSuccess: (c) => {
      toast.success(`${c.ref} started`, { description: "Opening the live run." });
      navigate(caseLink(c));
    },
    onError: (e) => toast.error("Could not start the scenario", { description: e instanceof Error ? e.message : undefined }),
  });
  const items = [...(q.data ?? [])].sort((a, b) => (a.run_order ?? 99) - (b.run_order ?? 99));

  return (
    <div>
      <PageHeader title="Scenario launcher" description="Synthetic invoices from data/scenarios.yaml, one click each. Run them in order: later ones depend on PO balances the earlier ones use." />
      {q.error && <ErrorNote error={q.error} />}
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {q.isLoading && Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-36 rounded-lg" />)}
        {items.map((f) => {
          const decision = expectedDecision(f);
          const pending = run.isPending && run.variables === f.id;
          return (
            <Card key={f.id} className="flex flex-col p-4">
              <div className="flex items-start gap-3">
                <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted text-xs font-semibold tabular text-muted-foreground">{f.run_order ?? "–"}</span>
                <div className="min-w-0 flex-1">
                  <div className="font-mono text-[11px] text-muted-foreground">{f.id}</div>
                  <div className="mt-0.5 text-sm font-medium leading-snug">{f.title}</div>
                </div>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span>Expect</span>
                {decision ? <StatusBadge kind="decision" value={decision} /> : <span className="font-medium text-foreground">{String(f.expect.outcome ?? "see spec")}</span>}
                {f.render === "scan" && (
                  <Tip content="Scanned image without a text layer">
                    <span className="inline-flex items-center gap-1">
                      <FileWarning className="size-3.5" /> scan
                    </span>
                  </Tip>
                )}
              </div>
              <div className="mt-auto flex items-center gap-2 pt-4">
                <Button size="sm" onClick={() => run.mutate(f.id)} disabled={!f.available || run.isPending} className="flex-1">
                  {pending ? <Loader2 className="animate-spin" /> : <Play />} Run
                </Button>
                <Tip content={`Download ${f.file}`}>
                  <Button size="icon-sm" variant="outline" asChild>
                    <FileLink path={`/api/fixtures/${f.id}/files/${encodeURIComponent(f.file)}`} aria-label={`Download ${f.file}`}>
                      <Download />
                    </FileLink>
                  </Button>
                </Tip>
              </div>
              {!f.available && <div className="mt-2 text-xs text-destructive">Fixture not generated: run make fixtures.</div>}
            </Card>
          );
        })}
      </div>
    </div>
  );
}
