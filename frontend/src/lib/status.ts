/**
 * The only place status colours live (SPEC §10): approved emerald, needs review amber, on hold sky,
 * rejected rose, processing violet. Every badge, dot, timeline node and chart series reads from here.
 */

export type Tone = "emerald" | "amber" | "sky" | "rose" | "violet" | "slate" | "indigo" | "teal";

interface ToneStyle {
  badge: string; // pill background + text + ring
  dot: string; // solid fill
  text: string; // foreground only
  soft: string; // tinted panel background
  border: string;
  chart: string; // hex for recharts
}

const TONES: Record<Tone, ToneStyle> = {
  emerald: {
    badge: "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-400/25",
    dot: "bg-emerald-500",
    text: "text-emerald-600 dark:text-emerald-400",
    soft: "bg-emerald-50/70 dark:bg-emerald-500/[0.07]",
    border: "border-emerald-300 dark:border-emerald-500/40",
    chart: "#10b981",
  },
  teal: {
    badge: "bg-teal-50 text-teal-700 ring-teal-600/20 dark:bg-teal-500/10 dark:text-teal-300 dark:ring-teal-400/25",
    dot: "bg-teal-500",
    text: "text-teal-600 dark:text-teal-400",
    soft: "bg-teal-50/70 dark:bg-teal-500/[0.07]",
    border: "border-teal-300 dark:border-teal-500/40",
    chart: "#14b8a6",
  },
  amber: {
    badge: "bg-amber-50 text-amber-800 ring-amber-600/25 dark:bg-amber-400/10 dark:text-amber-300 dark:ring-amber-400/25",
    dot: "bg-amber-500",
    text: "text-amber-600 dark:text-amber-400",
    soft: "bg-amber-50/70 dark:bg-amber-400/[0.07]",
    border: "border-amber-300 dark:border-amber-400/40",
    chart: "#f59e0b",
  },
  sky: {
    badge: "bg-sky-50 text-sky-700 ring-sky-600/20 dark:bg-sky-400/10 dark:text-sky-300 dark:ring-sky-400/25",
    dot: "bg-sky-500",
    text: "text-sky-600 dark:text-sky-400",
    soft: "bg-sky-50/70 dark:bg-sky-400/[0.07]",
    border: "border-sky-300 dark:border-sky-400/40",
    chart: "#0ea5e9",
  },
  rose: {
    badge: "bg-rose-50 text-rose-700 ring-rose-600/20 dark:bg-rose-500/10 dark:text-rose-300 dark:ring-rose-400/25",
    dot: "bg-rose-500",
    text: "text-rose-600 dark:text-rose-400",
    soft: "bg-rose-50/70 dark:bg-rose-500/[0.07]",
    border: "border-rose-300 dark:border-rose-500/40",
    chart: "#f43f5e",
  },
  violet: {
    badge: "bg-violet-50 text-violet-700 ring-violet-600/20 dark:bg-violet-500/10 dark:text-violet-300 dark:ring-violet-400/25",
    dot: "bg-violet-500",
    text: "text-violet-600 dark:text-violet-400",
    soft: "bg-violet-50/70 dark:bg-violet-500/[0.07]",
    border: "border-violet-300 dark:border-violet-500/40",
    chart: "#8b5cf6",
  },
  indigo: {
    badge: "bg-indigo-50 text-indigo-700 ring-indigo-600/20 dark:bg-indigo-500/10 dark:text-indigo-300 dark:ring-indigo-400/25",
    dot: "bg-indigo-500",
    text: "text-indigo-600 dark:text-indigo-400",
    soft: "bg-indigo-50/70 dark:bg-indigo-500/[0.07]",
    border: "border-indigo-300 dark:border-indigo-500/40",
    chart: "#6366f1",
  },
  slate: {
    badge: "bg-slate-100 text-slate-700 ring-slate-500/20 dark:bg-slate-400/10 dark:text-slate-300 dark:ring-slate-400/20",
    dot: "bg-slate-400",
    text: "text-slate-500 dark:text-slate-400",
    soft: "bg-slate-50 dark:bg-slate-400/[0.06]",
    border: "border-slate-300 dark:border-slate-500/40",
    chart: "#94a3b8",
  },
};

interface StatusDef {
  label: string;
  tone: Tone;
}

/** Case status (cases.status). */
const CASE: Record<string, StatusDef> = {
  received: { label: "Received", tone: "violet" },
  processing: { label: "Processing", tone: "violet" },
  needs_review: { label: "Needs review", tone: "amber" },
  on_hold: { label: "On hold", tone: "sky" },
  approved: { label: "Approved", tone: "emerald" },
  exported: { label: "Exported", tone: "teal" },
  rejected: { label: "Rejected", tone: "rose" },
  failed: { label: "Failed", tone: "rose" },
  unsupported: { label: "No invoice", tone: "slate" },
  split: { label: "Split", tone: "indigo" },
};

/** Decision (cases.decision, decide outcome). */
const DECISION: Record<string, StatusDef> = {
  auto_approve: { label: "Auto-approve", tone: "emerald" },
  needs_review: { label: "Needs review", tone: "amber" },
  on_hold: { label: "On hold", tone: "sky" },
  reject: { label: "Reject", tone: "rose" },
};

/** Stage event status (stage_events.status) and journey step state. */
const STAGE: Record<string, StatusDef> = {
  started: { label: "Running", tone: "violet" },
  current: { label: "In progress", tone: "violet" },
  completed: { label: "Done", tone: "emerald" },
  done: { label: "Done", tone: "emerald" },
  failed: { label: "Failed", tone: "rose" },
  skipped: { label: "Skipped", tone: "slate" },
  waiting: { label: "Waiting", tone: "amber" },
  pending: { label: "Pending", tone: "slate" },
};

/** Rule result status. */
const RULE: Record<string, StatusDef> = {
  pass: { label: "Pass", tone: "emerald" },
  warn: { label: "Warn", tone: "amber" },
  fail: { label: "Fail", tone: "rose" },
};

/** Rule severity (what a failure does). */
const SEVERITY: Record<string, StatusDef> = {
  reject: { label: "Reject", tone: "rose" },
  hold: { label: "Hold", tone: "sky" },
  review: { label: "Review", tone: "amber" },
  info: { label: "Info", tone: "slate" },
};

export type StatusKind = "case" | "decision" | "stage" | "rule" | "severity";
const KINDS: Record<StatusKind, Record<string, StatusDef>> = {
  case: CASE,
  decision: DECISION,
  stage: STAGE,
  rule: RULE,
  severity: SEVERITY,
};

function humanise(value: string): string {
  const s = value.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function statusDef(kind: StatusKind, value: string | null | undefined): StatusDef & { style: ToneStyle } {
  const def = (value && KINDS[kind][value]) || { label: value ? humanise(value) : "—", tone: "slate" as Tone };
  return { ...def, style: TONES[def.tone] };
}

export function toneStyle(tone: Tone): ToneStyle {
  return TONES[tone];
}

/** Chart colour for a decision or case status series. */
export function chartColor(kind: StatusKind, value: string): string {
  return statusDef(kind, value).style.chart;
}

/** Case statuses where a run is still moving (live view keeps the stream open). */
export const ACTIVE_CASE_STATUSES = new Set(["received", "processing"]);
