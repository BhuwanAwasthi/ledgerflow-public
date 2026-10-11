import { Bot, Check, ChevronDown, CircleAlert, CircleCheck, CircleX, FileDiff, GitCommitVertical, MessageSquareText, ShieldAlert, TriangleAlert, UserRound } from "lucide-react";
import { useMemo, useState } from "react";
import type { CaseDetail, ReviewActionRow, RuleResult, StageEvent, TemplateVersion, Workspace } from "@/api/client";
import { StatusBadge } from "@/components/common";
import { Badge } from "@/components/ui/primitives";
import type { ResolvedConf } from "@/lib/config";
import { dateTime, humanise, money, plainMoney } from "@/lib/format";
import { statusDef, toneStyle } from "@/lib/status";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ Rules */

const ORDER: Record<string, number> = { fail: 0, warn: 1, pass: 2 };
const COUNT_LABEL = { fail: "failed", warn: "warned", pass: "passed" } as const;
const RULE_ICON = { fail: CircleX, warn: TriangleAlert, pass: CircleCheck } as const;

export function RulesTab({ results, cfg }: { results: RuleResult[]; cfg: ResolvedConf | undefined }) {
  const [showPassed, setShowPassed] = useState(false);
  const names = useMemo(() => new Map(cfg?.rules.rules.map((r) => [r.id, r.name]) ?? []), [cfg]);
  const sorted = [...results].sort((a, b) => (ORDER[a.status] ?? 3) - (ORDER[b.status] ?? 3));
  const open = sorted.filter((r) => r.status !== "pass");
  const passed = sorted.filter((r) => r.status === "pass");
  if (!results.length) return <Empty>No rule results yet: the policy stage has not run.</Empty>;
  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap gap-2 text-xs">
        {(["fail", "warn", "pass"] as const).map((s) => {
          const n = results.filter((r) => r.status === s).length;
          return (
            <span key={s} className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-medium ring-1 ring-inset", statusDef("rule", s).style.badge)}>
              <span className="tabular">{n}</span> {COUNT_LABEL[s]}
            </span>
          );
        })}
      </div>
      {open.length === 0 && <div className="rounded-lg border border-dashed px-4 py-3 text-[13px] text-muted-foreground">Every rule passed.</div>}
      <ul className="space-y-2">
        {open.map((r) => (
          <RuleRow key={r.id} r={r} name={names.get(r.rule_id)} />
        ))}
      </ul>
      {passed.length > 0 && (
        <div>
          <button onClick={() => setShowPassed((v) => !v)} className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground" aria-expanded={showPassed}>
            <ChevronDown className={cn("size-3.5 transition-transform", !showPassed && "-rotate-90")} /> {passed.length} passed checks
          </button>
          {showPassed && (
            <ul className="mt-2 divide-y rounded-lg border">
              {passed.map((r) => (
                <li key={r.id} className="flex items-center gap-2.5 px-3 py-2 text-[13px]">
                  <Check className={cn("size-3.5 shrink-0", statusDef("rule", "pass").style.text)} />
                  <span>{names.get(r.rule_id) ?? humanise(r.rule_id)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function RuleRow({ r, name }: { r: RuleResult; name: string | undefined }) {
  const st = statusDef("rule", r.status);
  const Icon = RULE_ICON[r.status as keyof typeof RULE_ICON] ?? CircleAlert;
  return (
    <li className={cn("rounded-lg border px-3.5 py-3", st.style.border, st.style.soft)}>
      <div className="flex items-start gap-2.5">
        <Icon className={cn("mt-0.5 size-4 shrink-0", st.style.text)} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[13px] font-semibold">{name ?? humanise(r.rule_id)}</span>
            {r.status === "fail" && <StatusBadge kind="severity" value={r.severity} />}
            {r.status === "warn" && <Badge>Waived / info</Badge>}
          </div>
          <p className="mt-1 text-[13px] leading-relaxed text-foreground/85">{r.explanation}</p>
          <div className="mt-1 font-mono text-[11px] text-muted-foreground">{r.rule_id}</div>
        </div>
      </div>
    </li>
  );
}

/* ------------------------------------------------------------------ Match */

interface MatchLine {
  index: number;
  description: string;
  qty: string | null;
  unit_price: string | null;
  amount: string | null;
  po_line_no: number | null;
  po_description: string | null;
  po_qty: string | null;
  po_unit_price: string | null;
  grn_received: string | null;
  invoiced_before: string | null;
}
interface Candidate {
  po_number: string;
  score: number;
  components?: Record<string, number>;
  reason?: string;
}

function num(v: string | null | undefined): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString("en-US", { maximumFractionDigits: 4 }) : v;
}

export function MatchTab({ ws, currency }: { ws: Workspace; currency: string }) {
  const m = ws.match as { match_type?: string; linked_by?: string; po?: { po_number: string } | null; lines?: MatchLine[]; candidates?: Candidate[]; explicit_issue?: string | null; score?: number | null } | null;
  const issues = useMemo(() => {
    const byRow = new Map<number, string[]>();
    const lineCols = new Set(ws.columns.filter((c) => c.binds.some((b) => b.startsWith("lines."))).map((c) => c.header));
    ws.cells
      .filter((c) => c.kind === "rule" && lineCols.has(c.column))
      .forEach((c) => byRow.set(c.row, [...new Set([...(byRow.get(c.row) ?? []), ...c.reasons])]));
    return byRow;
  }, [ws.cells, ws.columns]);
  if (!m) return <Empty>PO matching has not run for this case.</Empty>;
  const lines = m.lines ?? [];
  const rose = toneStyle("rose");
  const amber = toneStyle("amber");
  const emerald = toneStyle("emerald");
  return (
    <div className="space-y-5 p-4">
      <div className="flex flex-wrap items-center gap-2 text-[13px]">
        <span className="font-semibold">{m.po?.po_number ?? "No PO linked"}</span>
        {m.match_type && <Badge>{humanise(m.match_type)} match</Badge>}
        {m.linked_by === "reviewer" && <Badge>Linked by reviewer</Badge>}
        {typeof m.score === "number" && <span className="text-xs text-muted-foreground tabular">score {Math.round(m.score * 100)}%</span>}
        {m.explicit_issue && <span className={cn("text-xs", rose.text)}>{m.explicit_issue}</span>}
      </div>

      {lines.length > 0 && (
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full min-w-[720px] table-fixed text-[13px]">
            <thead className="bg-muted/70 text-[11px] uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="w-[26%] px-3 py-2 text-left font-semibold">Invoice line</th>
                <th className="w-32 whitespace-nowrap px-3 py-2 text-right font-semibold">Qty × price</th>
                <th className="w-[24%] px-3 py-2 text-left font-semibold">PO line</th>
                <th className="w-32 whitespace-nowrap px-3 py-2 text-right font-semibold">PO qty × price</th>
                <th className="w-20 whitespace-nowrap px-3 py-2 text-right font-semibold">GRN</th>
                <th className="px-3 py-2 text-left font-semibold">Result</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {lines.map((ln) => {
                const rowIssues = issues.get(ln.index) ?? [];
                const onPo = ln.po_line_no !== null;
                return (
                  <tr key={ln.index} className="align-top">
                    <td className="px-3 py-2.5">
                      <div className="font-medium">{ln.description}</div>
                      <div className="text-xs text-muted-foreground tabular">{money(ln.amount, currency)}</div>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-right tabular">
                      {num(ln.qty)} × {plainMoney(ln.unit_price)}
                    </td>
                    <td className="px-3 py-2.5">
                      {onPo ? (
                        <>
                          <div>
                            <span className="mr-1.5 text-xs text-muted-foreground tabular">#{ln.po_line_no}</span>
                            {ln.po_description}
                          </div>
                          {ln.invoiced_before && Number(ln.invoiced_before) > 0 && <div className="text-xs text-muted-foreground tabular">{num(ln.invoiced_before)} already invoiced</div>}
                        </>
                      ) : (
                        <span className={cn("font-medium", amber.text)}>Not on the PO</span>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-right tabular">{onPo ? `${num(ln.po_qty)} × ${plainMoney(ln.po_unit_price)}` : "—"}</td>
                    <td className="px-3 py-2.5 text-right tabular">{ln.grn_received === null ? <span className="text-muted-foreground">no GRN</span> : num(ln.grn_received)}</td>
                    <td className="px-3 py-2.5">
                      {rowIssues.length ? (
                        <div className="flex flex-col gap-1">
                          {rowIssues.map((i) => (
                            <StatusBadgeText key={i} tone="amber" text={i} />
                          ))}
                        </div>
                      ) : (
                        <span className={cn("inline-flex items-center gap-1 text-xs font-medium", emerald.text)}>
                          <Check className="size-3.5" /> OK
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {(m.candidates?.length ?? 0) > 0 && m.match_type !== "explicit" && (
        <div>
          <div className="mb-2 text-xs font-medium text-muted-foreground">Candidate POs (ranked)</div>
          <ul className="space-y-2">
            {m.candidates!.map((c, i) => (
              <li key={c.po_number} className={cn("rounded-lg border px-3.5 py-2.5", i === 0 && "border-primary/40 bg-accent/40")}>
                <div className="flex items-center gap-3">
                  <span className="text-[13px] font-semibold">{c.po_number}</span>
                  {c.po_number === m.po?.po_number && <Badge>Pre-linked</Badge>}
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                    <div className="h-full rounded-full bg-primary" style={{ width: `${Math.round(c.score * 100)}%` }} />
                  </div>
                  <span className="w-10 text-right text-xs font-medium tabular">{Math.round(c.score * 100)}%</span>
                </div>
                {c.components && (
                  <div className="mt-1.5 flex gap-3 text-[11px] text-muted-foreground tabular">
                    {Object.entries(c.components).map(([k, v]) => (
                      <span key={k}>
                        {humanise(k)} {Math.round(v * 100)}%
                      </span>
                    ))}
                  </div>
                )}
                {c.reason && <p className="mt-1 text-xs text-foreground/80">{c.reason}</p>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function StatusBadgeText({ tone, text }: { tone: "amber" | "rose"; text: string }) {
  return <span className={cn("inline-flex w-fit items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset", toneStyle(tone).badge)}>{text}</span>;
}

/* ------------------------------------------------------------------ History */

interface Changed {
  row: number;
  column: string;
  old: string;
  new: string;
}

export function DiffTable({ changed }: { changed: Changed[] }) {
  if (!changed.length) return <div className="text-xs text-muted-foreground">No cell changed.</div>;
  const indigo = toneStyle("indigo");
  return (
    <table className="w-full text-xs">
      <thead className="text-[11px] uppercase tracking-wide text-muted-foreground">
        <tr>
          <th className="py-1 pr-3 text-left font-medium">Row</th>
          <th className="py-1 pr-3 text-left font-medium">Column</th>
          <th className="py-1 pr-3 text-left font-medium">Before</th>
          <th className="py-1 text-left font-medium">After</th>
        </tr>
      </thead>
      <tbody>
        {changed.map((c) => (
          <tr key={`${c.row}:${c.column}`} className="border-t">
            <td className="py-1.5 pr-3 tabular text-muted-foreground">{c.row + 1}</td>
            <td className="py-1.5 pr-3 font-mono text-[11px]">{c.column}</td>
            <td className="py-1.5 pr-3 tabular text-muted-foreground line-through">{c.old || "—"}</td>
            <td className={cn("py-1.5 tabular font-semibold", indigo.text)}>{c.new || "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

type Item =
  | { kind: "version"; at: string; tv: TemplateVersion }
  | { kind: "action"; at: string; a: ReviewActionRow };

function versionTimes(events: StageEvent[]): Map<number, string> {
  const m = new Map<number, string>();
  for (const e of events) {
    const v = (e.output as { template?: { version?: number } }).template?.version;
    if (e.agent_id === "template" && e.status === "completed" && typeof v === "number" && !m.has(v)) m.set(v, e.ended_at ?? e.started_at);
  }
  return m;
}

export function HistoryTab({ detail }: { detail: CaseDetail }) {
  const items = useMemo(() => {
    const times = versionTimes(detail.events);
    const out: Item[] = [
      ...detail.templates.map((tv) => ({ kind: "version" as const, at: times.get(tv.version) ?? detail.created_at, tv })),
      ...detail.review_actions.map((a) => ({ kind: "action" as const, at: a.created_at, a })),
    ];
    return out.sort((x, y) => new Date(x.at).getTime() - new Date(y.at).getTime());
  }, [detail]);
  if (!items.length) return <Empty>Nothing yet: versions and remarks appear here.</Empty>;
  return (
    <ol className="relative space-y-4 p-4 before:absolute before:bottom-6 before:left-[29px] before:top-6 before:w-px before:bg-border">
      {items.map((it) => (it.kind === "version" ? <VersionItem key={`v${it.tv.version}`} tv={it.tv} at={it.at} /> : <ActionItem key={`a${it.a.id}`} a={it.a} />))}
    </ol>
  );
}

function Node({ children, className }: { children: React.ReactNode; className?: string }) {
  return <span className={cn("relative z-[1] flex size-7 shrink-0 items-center justify-center rounded-full border bg-card [&_svg]:size-3.5", className)}>{children}</span>;
}

function VersionItem({ tv, at }: { tv: TemplateVersion; at: string }) {
  const [open, setOpen] = useState(true);
  const diff = tv.diff_from_prev as { changed?: Changed[] } | null;
  const n = diff?.changed?.length ?? 0;
  return (
    <li className="flex gap-3">
      <Node className={tv.status === "final" ? statusDef("case", "approved").style.text : "text-muted-foreground"}>{diff ? <FileDiff /> : <GitCommitVertical />}</Node>
      <div className="min-w-0 flex-1 pt-0.5">
        <div className="flex flex-wrap items-center gap-2 text-[13px]">
          <span className="font-semibold">Template v{tv.version}</span>
          <Badge>{tv.status}</Badge>
          <span className="text-xs text-muted-foreground">
            {tv.created_by === "reviewer" ? "after reviewer changes" : "drafted by the template agent"} · {dateTime(at)}
          </span>
        </div>
        {diff && (
          <div className="mt-2 rounded-lg border bg-card px-3 py-2">
            <button className="flex w-full items-center gap-1 text-xs font-medium" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
              <ChevronDown className={cn("size-3.5 transition-transform", !open && "-rotate-90")} />
              {n} cell{n === 1 ? "" : "s"} changed since v{tv.version - 1}
            </button>
            {open && (
              <div className="mt-1.5">
                <DiffTable changed={diff.changed ?? []} />
              </div>
            )}
          </div>
        )}
      </div>
    </li>
  );
}

const ACTION_LABEL: Record<string, string> = {
  remark: "requested changes",
  approve: "approved",
  reject: "rejected",
  request_info: "asked the vendor",
  override: "override logged",
};

function ActionItem({ a }: { a: ReviewActionRow }) {
  const cs = a.changeset as { status?: string; source?: string; changes?: string[]; question?: string; ops?: unknown[] } | null;
  const t = statusDef("review_action", a.action).style;
  if (a.action === "override") {
    return (
      <li className="flex gap-3">
        <Node className={t.text}>
          <ShieldAlert />
        </Node>
        <div className="min-w-0 flex-1 pt-1 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">{a.actor}</span> · {a.text} · {dateTime(a.created_at)}
        </div>
      </li>
    );
  }
  return (
    <li className="flex gap-3">
      <Node className={t.text}>{a.action === "remark" ? <MessageSquareText /> : <UserRound />}</Node>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2 pt-1 text-[13px]">
          <span className="font-semibold">{a.actor}</span>
          <span className="text-muted-foreground">{ACTION_LABEL[a.action] ?? a.action}</span>
          {cs?.source === "grid" && <Badge>edited in the grid</Badge>}
          <span className="text-xs text-muted-foreground">{dateTime(a.created_at)}</span>
        </div>
        {a.text && <div className={cn("mt-1.5 rounded-lg rounded-tl-sm border px-3 py-2 text-[13px] leading-relaxed", t.soft, t.border)}>{a.text}</div>}
        {cs?.status === "applied" && (cs.changes?.length ?? 0) > 0 && (
          <div className="mt-2 flex gap-2">
            <Bot className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
            <ul className="space-y-0.5 text-xs text-foreground/80">
              {cs.changes!.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          </div>
        )}
        {cs?.status === "pending" && <div className="mt-2 text-xs text-muted-foreground">Applying…</div>}
        {cs?.status === "needs_clarification" && cs.question && (
          <div className={cn("mt-2 flex gap-2 rounded-lg border px-3 py-2 text-[13px]", toneStyle("amber").border, toneStyle("amber").soft)}>
            <Bot className={cn("mt-0.5 size-3.5 shrink-0", toneStyle("amber").text)} />
            <div>
              <div className="text-xs font-medium text-muted-foreground">Reconciler needs a clarification, nothing was changed</div>
              <div className="mt-0.5">{cs.question}</div>
            </div>
          </div>
        )}
      </div>
    </li>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="px-5 py-12 text-center text-sm text-muted-foreground">{children}</div>;
}
