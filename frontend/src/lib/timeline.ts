/** Folds append-only stage_events into one card per stage execution (started → completed/failed/waiting). */
import type { StageEvent } from "@/api/client";

export interface StageCardModel {
  key: string;
  runNo: number;
  seq: number;
  stageId: string;
  agentId: string | null;
  action: string | null;
  status: string; // started | completed | failed | skipped | waiting
  outcome: string | null;
  output: Record<string, unknown>;
  reasoning: string[];
  metrics: Record<string, unknown>;
  inputSummary: Record<string, unknown>;
  startedAt: string;
  endedAt: string | null;
}

export interface RunGroup {
  runNo: number;
  cards: StageCardModel[];
}

export function buildCards(events: StageEvent[]): StageCardModel[] {
  const cards: StageCardModel[] = [];
  const open = new Map<string, StageCardModel>(); // stage id -> card still running/waiting
  for (const ev of events) {
    const existing = open.get(ev.stage_id);
    if (ev.status !== "started" && existing && (existing.status === "started" || existing.status === "waiting")) {
      existing.status = ev.status;
      existing.outcome = ev.outcome ?? existing.outcome;
      if (Object.keys(ev.output).length) existing.output = ev.output;
      if (ev.reasoning.length) existing.reasoning = ev.reasoning;
      if (Object.keys(ev.metrics).length) existing.metrics = { ...existing.metrics, ...ev.metrics };
      if (Object.keys(ev.input_summary).length) existing.inputSummary = ev.input_summary;
      existing.endedAt = ev.ended_at;
      if (ev.status !== "waiting") open.delete(ev.stage_id);
      continue;
    }
    const card: StageCardModel = {
      key: `${ev.run_no}-${ev.seq}`,
      runNo: ev.run_no,
      seq: ev.seq,
      stageId: ev.stage_id,
      agentId: ev.agent_id,
      action: ev.action,
      status: ev.status,
      outcome: ev.outcome,
      output: ev.output,
      reasoning: ev.reasoning,
      metrics: ev.metrics,
      inputSummary: ev.input_summary,
      startedAt: ev.started_at,
      endedAt: ev.ended_at,
    };
    cards.push(card);
    if (ev.status === "started" || ev.status === "waiting") open.set(ev.stage_id, card);
  }
  return cards;
}

export function groupRuns(cards: StageCardModel[]): RunGroup[] {
  const groups: RunGroup[] = [];
  for (const c of cards) {
    const last = groups[groups.length - 1];
    if (last && last.runNo === c.runNo) last.cards.push(c);
    else groups.push({ runNo: c.runNo, cards: [c] });
  }
  return groups;
}

export function num(v: unknown): number {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
}

export function totals(cards: StageCardModel[]) {
  let tokensIn = 0;
  let tokensOut = 0;
  let cost = 0;
  let llmCalls = 0;
  for (const c of cards) {
    tokensIn += num(c.metrics.tokens_in);
    tokensOut += num(c.metrics.tokens_out);
    cost += num(c.metrics.cost_usd);
    if (Array.isArray(c.metrics.llm_calls)) llmCalls += c.metrics.llm_calls.length;
  }
  return { tokensIn, tokensOut, cost, llmCalls };
}
