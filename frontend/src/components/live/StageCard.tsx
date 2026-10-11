import { AlertTriangle, Braces, Check, ChevronDown, Clock3, Hourglass, Loader2, RotateCcw, SkipForward, UserCheck, X, Zap } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { StatusBadge } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Tip } from "@/components/ui/overlays";
import { renderChips } from "@/lib/chips";
import { stageOf, type ResolvedConf } from "@/lib/config";
import { count, duration, humanise, timeOfDay, usd } from "@/lib/format";
import { statusDef } from "@/lib/status";
import { num, type StageCardModel } from "@/lib/timeline";
import { cn } from "@/lib/utils";

const ICONS = { started: Loader2, completed: Check, failed: X, skipped: SkipForward, waiting: Hourglass } as const;

function Node({ status, active }: { status: string; active: boolean }) {
  const d = statusDef("stage", status);
  const Icon = ICONS[status as keyof typeof ICONS] ?? Clock3;
  return (
    <div
      className={cn(
        "relative z-10 flex size-7 items-center justify-center rounded-full text-white ring-4 ring-background",
        d.style.dot,
        active && "animate-pulse-ring",
      )}
    >
      <Icon className={cn("size-3.5", status === "started" && "animate-spin")} strokeWidth={2.75} />
    </div>
  );
}

export function StageCard({
  card,
  cfg,
  now,
  progress,
  isLast,
  caseId,
  caseStatus,
  currency,
  onRetry,
  retrying,
  replaying,
  canRerun,
}: {
  card: StageCardModel;
  cfg: ResolvedConf | undefined;
  now: number;
  progress?: string;
  isLast: boolean;
  caseId: number;
  caseStatus: string | undefined;
  currency: string;
  onRetry: (stageId: string) => void;
  retrying: boolean;
  replaying: boolean;
  canRerun: boolean;
}) {
  const [showJson, setShowJson] = useState(false);
  const stage = stageOf(cfg, card.stageId);
  const agent = card.agentId ? cfg?.agents[card.agentId] : undefined;
  const active = card.status === "started";
  const multiAction = agent && card.action && Object.keys(agent.actions ?? {}).length > 1;
  const title = agent
    ? multiAction
      ? `${agent.name} · ${humanise(card.action)}`
      : agent.name
    : stage?.hitl
      ? stage.status_label || "Human review"
      : humanise(card.stageId);
  const ms = active ? now - new Date(card.startedAt).getTime() : num(card.metrics.duration_ms) || num(card.metrics.latency_ms);
  const chips = card.status === "completed" || card.status === "waiting" ? renderChips(card.agentId ? cfg?.app.ui.stage_chips[card.agentId] : undefined, card.output, currency) : [];
  const tokensIn = num(card.metrics.tokens_in);
  const tokensOut = num(card.metrics.tokens_out);
  const cost = num(card.metrics.cost_usd);
  const isLlm = tokensIn + tokensOut > 0 || Array.isArray(card.metrics.llm_calls);
  const cacheHit = card.metrics.cache_hit === true;
  const error = typeof card.output.error === "string" ? card.output.error : null;
  const waitingFor = typeof card.inputSummary.waiting_for === "string" ? card.inputSummary.waiting_for : null;
  const failedLast = card.status === "failed" && isLast && caseStatus === "failed" && !replaying;
  const canRetry = failedLast && canRerun;
  const d = statusDef("stage", card.status);

  return (
    <li className="relative flex animate-slide-in gap-4 pb-4">
      {!isLast && (
        <div
          className={cn("absolute left-[13.5px] top-10 -bottom-3.5 w-px", card.status === "completed" ? statusDef("stage", "completed").style.dot : "bg-border")}
          aria-hidden
        />
      )}
      <div className="flex flex-col items-center pt-3.5">
        <Node status={card.status} active={active} />
      </div>
      <div
        className={cn(
          "min-w-0 flex-1 rounded-lg border bg-card shadow-[0_1px_2px_rgba(16,24,40,0.04)] transition-colors",
          active && cn(d.style.border, "shadow-md"),
          card.status === "failed" && d.style.border,
          card.status === "waiting" && d.style.border,
        )}
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 pt-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              {stage?.hitl && <UserCheck className="size-4 text-muted-foreground" />}
              <span className="truncate text-sm font-semibold">{title}</span>
              {agent?.kind === "llm" || agent?.kind === "hybrid" ? (
                <Tip content={`${agent.kind === "llm" ? "LLM" : "Hybrid (LLM + code)"} agent: the model perceives, code decides`}>
                  <span className="inline-flex items-center gap-0.5 rounded bg-muted px-1 py-px text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                    <Zap className="size-2.5" />
                    {agent.kind}
                  </span>
                </Tip>
              ) : null}
            </div>
            <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
              {card.stageId}
              {card.action && ` · ${card.agentId}.${card.action}`}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {card.outcome && card.status !== "started" && (
              <span className="rounded-md border px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">→ {card.outcome}</span>
            )}
            <StatusBadge kind="stage" value={card.status} pulse={active} />
            <span className="w-16 text-right text-xs tabular text-muted-foreground">{ms > 0 ? duration(ms) : timeOfDay(card.startedAt)}</span>
          </div>
        </div>

        <div className="space-y-2.5 px-4 pb-3 pt-2.5">
          {active && (
            <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
              <Loader2 className={cn("size-3.5 animate-spin", d.style.text)} />
              <span>{progress ? progress.charAt(0).toUpperCase() + progress.slice(1) : "Working…"}</span>
            </div>
          )}

          {card.reasoning.length > 0 && (
            <ul className="space-y-1">
              {card.reasoning.slice(0, 5).map((r, i) => (
                <li key={i} className="flex gap-2 text-[13px] leading-snug">
                  <span className="mt-[7px] size-1 shrink-0 rounded-full bg-muted-foreground/50" />
                  <span>{r}</span>
                </li>
              ))}
            </ul>
          )}

          {error && (
            <div className={cn("flex items-start gap-2 rounded-md border px-3 py-2 text-[13px]", d.style.border, d.style.soft)}>
              <AlertTriangle className={cn("mt-0.5 size-4 shrink-0", d.style.text)} />
              <div className="min-w-0 flex-1 break-words font-mono text-xs">{error}</div>
              {canRetry && (
                <Button size="xs" variant="outline" onClick={() => onRetry(card.stageId)} disabled={retrying}>
                  <RotateCcw className={cn(retrying && "animate-spin")} /> Retry
                </Button>
              )}
              {failedLast && !canRerun && <span className="shrink-0 text-xs text-muted-foreground">Switch to Admin to retry</span>}
            </div>
          )}

          {card.status === "waiting" && waitingFor && (
            <div className={cn("flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-[13px]", d.style.border, d.style.soft)}>
              <Hourglass className={cn("size-4", d.style.text)} />
              <span className="flex-1">
                {waitingFor === "review" ? "Waiting for a reviewer to approve, remark, reject or ask the vendor." : (
                  <>
                    Waiting for event <span className="font-mono text-xs">{waitingFor}</span>
                    {stage?.resume_to && (
                      <>
                        , then resumes at <span className="font-mono text-xs">{stage.resume_to}</span>
                      </>
                    )}
                    .
                  </>
                )}
              </span>
              {waitingFor === "review" && isLast && (
                <Button size="xs" asChild>
                  <Link to={`/cases/${caseId}/review`}>Open review</Link>
                </Button>
              )}
            </div>
          )}

          {chips.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {chips.map((c) => (
                <span key={c} className="rounded-md bg-muted px-2 py-0.5 text-xs font-medium tabular text-foreground/80">
                  {c}
                </span>
              ))}
            </div>
          )}
        </div>

        {card.status !== "started" && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t px-4 py-2 text-[11px] text-muted-foreground">
            {isLlm ? (
              <>
                <span className="tabular">
                  {count(tokensIn)} in · {count(tokensOut)} out tokens
                </span>
                <span className="tabular">{usd(cost)}</span>
                {num(card.metrics.llm_latency_ms) > 0 && <span className="tabular">model {duration(num(card.metrics.llm_latency_ms))}</span>}
                {cacheHit && <span className="rounded bg-muted px-1 font-medium">cache hit</span>}
              </>
            ) : (
              <span>{card.status === "skipped" ? "Skipped by config" : stage?.hitl ? "Human step" : "Deterministic code, no model call"}</span>
            )}
            {num(card.metrics.attempts) > 1 && <span>{num(card.metrics.attempts)} attempts</span>}
            {card.metrics.confidence !== null && card.metrics.confidence !== undefined && (
              <span className="tabular">confidence {Math.round(num(card.metrics.confidence) * 100)}%</span>
            )}
            <button
              onClick={() => setShowJson((s) => !s)}
              className="ml-auto inline-flex items-center gap-1 rounded px-1 py-0.5 font-medium hover:bg-muted hover:text-foreground"
              aria-expanded={showJson}
            >
              <Braces className="size-3" /> JSON <ChevronDown className={cn("size-3 transition-transform", showJson && "rotate-180")} />
            </button>
          </div>
        )}
        {showJson && (
          <pre className="max-h-80 overflow-auto border-t bg-muted/40 px-4 py-3 font-mono text-[11px] leading-relaxed">
            {JSON.stringify({ outcome: card.outcome, output: card.output, metrics: card.metrics, input: card.inputSummary }, null, 2)}
          </pre>
        )}
      </div>
    </li>
  );
}
