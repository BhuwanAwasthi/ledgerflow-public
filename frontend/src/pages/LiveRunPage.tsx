import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, ArrowRight, Download, FileSearch, Pause, Radio, RotateCcw, Route, WifiOff } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { api, unwrap, type StageEvent } from "@/api/client";
import { FileLink } from "@/components/FileLink";
import { EmptyState, ErrorNote, PageHeader, StatusBadge } from "@/components/common";
import { StageCard } from "@/components/live/StageCard";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/primitives";
import { Tip } from "@/components/ui/overlays";
import { useCaseParam } from "@/hooks/useCaseParam";
import { useCaseStream, type Connection } from "@/hooks/useCaseStream";
import { stageOf, useConfig, type ResolvedConf } from "@/lib/config";
import { caseLink } from "@/lib/nav";
import { count, dateTime, duration, money, timeOfDay, usd } from "@/lib/format";
import { ACTIVE_CASE_STATUSES, statusDef, toneStyle } from "@/lib/status";
import { buildCards, groupRuns, totals, type RunGroup } from "@/lib/timeline";
import { cn } from "@/lib/utils";
import { SampleBadge } from "@/components/caseColumns";

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(t);
  }, [active]);
  return now;
}

function ConnectionPill({ connection, replaying, attempts, finished }: { connection: Connection; replaying: boolean; attempts: number; finished: boolean }) {
  if (replaying) {
    const t = toneStyle("indigo");
    return (
      <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset", t.badge)}>
        <RotateCcw className="size-3" /> Replaying
      </span>
    );
  }
  if (finished && connection === "live") {
    const t = toneStyle("slate");
    return (
      <Tip content="The run has stopped; the stream stays open in case a reviewer or event resumes it">
        <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset", t.badge)}>
          <span className={cn("size-2 rounded-full", t.dot)} /> Run complete
        </span>
      </Tip>
    );
  }
  const map = {
    live: { tone: "emerald" as const, icon: Radio, label: "Live" },
    connecting: { tone: "slate" as const, icon: Activity, label: "Connecting…" },
    reconnecting: { tone: "amber" as const, icon: WifiOff, label: `Reconnecting${attempts > 1 ? ` (try ${attempts})` : "…"}` },
  }[connection];
  const t = toneStyle(map.tone);
  return (
    <Tip content={connection === "live" ? "Streaming stage events over SSE" : "Connection dropped; resuming from the last event received"}>
      <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset", t.badge)}>
        <span className="relative flex size-2">
          {connection === "live" && <span className={cn("absolute inline-flex size-full animate-ping rounded-full opacity-60", t.dot)} />}
          <span className={cn("relative inline-flex size-2 rounded-full", t.dot)} />
        </span>
        {map.label}
      </span>
    </Tip>
  );
}

function runCaption(group: RunGroup, index: number, cfg: ResolvedConf | undefined): string {
  if (index === 0) return "Submitted";
  const first = group.cards[0];
  const stage = stageOf(cfg, first.stageId);
  if (stage?.hitl) return first.outcome ? `Reviewer: ${first.outcome.replace(/_/g, " ")}` : "Resumed by reviewer";
  if (stage?.wait_for) return `Resumed by event ${stage.wait_for}`;
  return `Rerun from ${first.stageId}`;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5 text-[13px]">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className="min-w-0 truncate text-right font-medium">{children}</span>
    </div>
  );
}

export default function LiveRunPage() {
  const param = useCaseParam();
  const id = param.id ?? NaN;
  const qc = useQueryClient();
  const { data: cfg } = useConfig();
  const detail = useQuery({
    queryKey: ["case", id],
    queryFn: async () => unwrap(await api.GET("/api/cases/{case_id}", { params: { path: { case_id: id } } })),
    enabled: Number.isFinite(id),
  });
  const stream = useCaseStream(Number.isFinite(id) ? id : null);

  // ---- replay: re-animate stored events with compressed gaps
  const [replay, setReplay] = useState<StageEvent[] | null>(null);
  const timers = useRef<number[]>([]);
  const stopReplay = () => {
    timers.current.forEach((t) => window.clearTimeout(t));
    timers.current = [];
    setReplay(null);
  };
  const startReplay = () => {
    stopReplay();
    const all = stream.events;
    setReplay([]);
    let at = 300;
    all.forEach((ev, i) => {
      if (i > 0) {
        const gap = new Date(ev.started_at).getTime() - new Date(all[i - 1].started_at).getTime();
        at += Math.min(Math.max(gap / 8, 140), 900);
      }
      timers.current.push(window.setTimeout(() => setReplay((r) => (r ? [...r, ev] : r)), at));
    });
    timers.current.push(window.setTimeout(() => setReplay(null), at + 1200));
  };
  useEffect(() => () => timers.current.forEach((t) => window.clearTimeout(t)), []);

  const replaying = replay !== null;
  const events = replay ?? stream.events;
  const cards = useMemo(() => buildCards(events), [events]);
  const runs = useMemo(() => groupRuns(cards), [cards]);
  const anyActive = cards.some((c) => c.status === "started");
  const now = useNow(anyActive);
  const sums = totals(cards);

  const c = detail.data;
  const status = replaying ? (anyActive ? "processing" : c?.status) : stream.summary?.status ?? c?.status;
  const currency = c?.currency ?? cfg?.rules.currency ?? "USD";
  const firstAt = events[0]?.started_at;
  const lastEnd = [...events].reverse().find((e) => e.ended_at)?.ended_at;
  const elapsed = firstAt ? (status && ACTIVE_CASE_STATUSES.has(status) ? now : new Date(lastEnd ?? firstAt).getTime()) - new Date(firstAt).getTime() : 0;

  const retry = useMutation({
    mutationFn: async (fromStage: string) => unwrap(await api.POST("/api/cases/{case_id}/rerun", { params: { path: { case_id: id } }, body: { from_stage: fromStage } })),
    onSuccess: (r) => {
      toast.success(`Retrying ${r.current_stage}`, { description: "A new run started from the failed stage." });
      qc.invalidateQueries({ queryKey: ["case", id] });
    },
    onError: (e) => toast.error("Retry failed", { description: e instanceof Error ? e.message : undefined }),
  });

  // auto-follow the newest card while the run is live
  const bottom = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  useEffect(() => {
    const onScroll = () => {
      follow.current = window.innerHeight + window.scrollY >= document.body.scrollHeight - 160;
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  useEffect(() => {
    if (follow.current && (anyActive || replaying) && cards.length > 3) bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [cards.length, anyActive, replaying]);

  if (param.error || detail.error) return <ErrorNote error={param.error ?? detail.error} />;

  const decision = statusDef("decision", c?.decision);
  return (
    <div>
      <PageHeader
        eyebrow={
          <span className="inline-flex items-center gap-1.5">
            <Activity className="size-3.5" /> Live run
          </span>
        }
        title={
          c ? (
            <span className="flex items-center gap-3">
              <span className="tabular">{c.ref}</span>
              {c.is_sample && <SampleBadge />}
              <StatusBadge kind="case" value={status} pulse={status === "processing"} className="text-[13px]" />
            </span>
          ) : (
            <Skeleton className="h-7 w-56" />
          )
        }
        description={c ? [c.vendor_name ?? c.submitter_name, c.invoice_number && `invoice ${c.invoice_number}`, c.total && money(c.total, currency)].filter(Boolean).join(" · ") || "Waiting for the first stage…" : undefined}
        actions={
          <>
            <ConnectionPill connection={stream.connection} replaying={replaying} attempts={stream.attempts} finished={!!status && !ACTIVE_CASE_STATUSES.has(status)} />
            {cfg?.app.features.replay !== false &&
              (replaying ? (
                <Button variant="outline" size="sm" onClick={stopReplay}>
                  <Pause /> Stop replay
                </Button>
              ) : (
                <Button variant="outline" size="sm" onClick={startReplay} disabled={!stream.events.length || anyActive}>
                  <RotateCcw /> Replay
                </Button>
              ))}
          </>
        }
      />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0">
          {!cards.length && (stream.connection === "connecting" || detail.isLoading) && (
            <div className="space-y-4">
              {[0, 1, 2].map((i) => (
                <div key={i} className="flex gap-4">
                  <Skeleton className="size-7 rounded-full" />
                  <Skeleton className="h-28 flex-1 rounded-lg" />
                </div>
              ))}
            </div>
          )}
          {!cards.length && stream.connection !== "connecting" && !detail.isLoading && (
            <EmptyState icon={Activity} title="Queued">
              The case is waiting for a free worker. Stages appear here as soon as they start.
            </EmptyState>
          )}
          {runs.map((g, gi) => (
            <section key={g.runNo} aria-label={`Run ${g.runNo}`}>
              <div className="mb-3 flex items-center gap-3 text-xs">
                <span className="rounded-full bg-muted px-2 py-0.5 font-semibold text-foreground/80">Run {g.runNo}</span>
                <span className="text-muted-foreground">
                  {runCaption(g, gi, cfg)} · {timeOfDay(g.cards[0].startedAt)}
                </span>
                <div className="h-px flex-1 bg-border" />
              </div>
              <ol>
                {g.cards.map((card, i) => (
                  <StageCard
                    key={card.key}
                    card={card}
                    cfg={cfg}
                    now={now}
                    progress={stream.progress[card.stageId]}
                    isLast={gi === runs.length - 1 && i === g.cards.length - 1}
                    caseId={id}
                    caseStatus={status}
                    currency={currency}
                    onRetry={(s) => retry.mutate(s)}
                    retrying={retry.isPending}
                    replaying={replaying}
                    canRerun
                  />
                ))}
              </ol>
            </section>
          ))}
          <div ref={bottom} />
        </div>

        <aside className="space-y-4 xl:sticky xl:top-20 xl:self-start">
          <Card className={cn("overflow-hidden", c?.decision && !replaying && decision.style.border)}>
            <div className={cn("border-b px-5 py-4", c?.decision && !replaying && decision.style.soft)}>
              <div className="text-xs font-medium text-muted-foreground">Decision</div>
              <div className="mt-1 flex items-baseline gap-2">
                {c?.decision && !(replaying && anyActive) ? (
                  <span className={cn("text-lg font-semibold", decision.style.text)}>{decision.label}</span>
                ) : (
                  <span className="text-lg font-semibold text-muted-foreground">{status === "failed" ? "Not reached" : "Pending…"}</span>
                )}
              </div>
              {c?.total && <div className="mt-1 text-2xl font-semibold tracking-tight tabular">{money(c.total, currency)}</div>}
              {c?.decision_summary && !(replaying && anyActive) && <p className="mt-2.5 text-[13px] leading-relaxed">{c.decision_summary}</p>}
            </div>
            <div className="divide-y px-5 py-1.5">
              {c ? (
                <>
                  <Row label="Vendor">{c.vendor_name ?? "—"}</Row>
                  <Row label="Invoice">{c.invoice_number ?? "—"}</Row>
                  <Row label="Extraction confidence">
                    <span className="tabular">{c.confidence === null ? "—" : `${Math.round(c.confidence * 100)}%`}</span>
                  </Row>
                  <Row label="Submitted by">{c.submitter_name ?? c.submitter_email ?? "—"}</Row>
                  <Row label="Received">{dateTime(c.created_at)}</Row>
                </>
              ) : (
                <div className="space-y-2 py-3">
                  <Skeleton />
                  <Skeleton className="w-2/3" />
                </div>
              )}
            </div>
            <div className="flex flex-wrap gap-2 border-t px-5 py-3">
              {status && !ACTIVE_CASE_STATUSES.has(status) && (c?.templates.length || c?.rule_results.length) ? (
                <Button size="sm" variant={status === "needs_review" ? "default" : "outline"} asChild>
                  <Link to={caseLink({ id, ref: c?.ref }, "review")}>
                    <FileSearch /> {status === "needs_review" ? "Open review" : "Workspace"}
                  </Link>
                </Button>
              ) : null}
              <Button size="sm" variant="outline" asChild>
                <Link to={`/submissions?case=${id}`}>
                  <Route /> Journey
                </Link>
              </Button>
              <Button size="sm" variant="outline" asChild>
                <FileLink path={`/api/cases/${id}/audit-pack`}>
                  <Download /> Audit pack
                </FileLink>
              </Button>
            </div>
          </Card>

          <Card className="px-5 py-4">
            <div className="mb-2 text-xs font-medium text-muted-foreground">This case so far</div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-3">
              <Metric label="Elapsed" value={elapsed > 0 ? duration(elapsed) : "—"} />
              <Metric label="Stages" value={`${cards.filter((x) => x.status !== "started").length}${anyActive ? " + 1" : ""}`} />
              <Metric label="Tokens" value={count(sums.tokensIn + sums.tokensOut)} />
              <Metric label="LLM cost" value={sums.cost === 0 && sums.tokensIn + sums.tokensOut > 0 ? "—" : usd(sums.cost)} />
            </div>
            <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
              Models only read and explain; every decision above comes from rules in config and is reproducible from the config snapshot.
            </p>
          </Card>

          {status && !ACTIVE_CASE_STATUSES.has(status) && !replaying && (
            <Link to="/launcher" className="flex items-center justify-between rounded-lg border px-4 py-3 text-[13px] text-muted-foreground hover:bg-muted/60 hover:text-foreground">
              Run another scenario <ArrowRight className="size-4" />
            </Link>
          )}
        </aside>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="text-base font-semibold tabular">{value}</div>
    </div>
  );
}
