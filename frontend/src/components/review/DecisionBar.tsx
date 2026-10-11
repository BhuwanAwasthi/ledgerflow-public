import { Building2, FileText, Receipt, TriangleAlert } from "lucide-react";
import { useState } from "react";
import type { CaseDetail, POBalance, RuleResult } from "@/api/client";
import { StatusBadge } from "@/components/common";
import { Tip } from "@/components/ui/overlays";
import { money, plainMoney } from "@/lib/format";
import { statusDef, toneStyle } from "@/lib/status";
import { cn } from "@/lib/utils";

/** Consumed by other invoices | this invoice | what is left, on one bar. Amounts are display-only (decimal strings). */
export function POBalanceBar({ b, className }: { b: POBalance; className?: string }) {
  const amount = Number(b.amount);
  const others = Number(b.committed_by_others);
  const self = Number(b.this_invoice);
  const scale = Math.max(amount, others + self, 1);
  const over = Number(b.remaining_after) < 0;
  const slate = toneStyle("slate");
  const thisTone = statusDef("balance", over ? "over" : "within").style;
  const pctOf = (v: number) => `${Math.max(0, Math.min(100, (v / scale) * 100))}%`;
  return (
    <div className={cn("min-w-[220px]", className)}>
      <div className="flex items-baseline justify-between gap-3 text-[11px] text-muted-foreground">
        <span>
          <span className="font-semibold text-foreground">{b.po_number}</span> · {plainMoney(b.amount)}
          {b.amended && <span className="ml-1">(amended from {plainMoney(b.master_amount)})</span>}
        </span>
        <span className={cn("font-medium tabular", over ? thisTone.text : "text-foreground")}>
          {over ? `over by ${plainMoney(-Number(b.remaining_after))}` : `${plainMoney(b.remaining_after)} left after`}
        </span>
      </div>
      <Tip content={`Invoiced before ${money(b.committed_by_others, b.currency)} · this invoice ${money(b.this_invoice, b.currency)} · ${money(b.remaining_before, b.currency)} was left`}>
        <div className="relative mt-1.5 flex h-2 overflow-hidden rounded-full bg-muted">
          <div className={cn("h-full", slate.dot)} style={{ width: pctOf(others) }} />
          <div className={cn("h-full", thisTone.dot)} style={{ width: pctOf(self) }} />
          {over && <div className="absolute inset-y-0 border-l-2 border-foreground/70" style={{ left: pctOf(amount) }} aria-hidden />}
        </div>
      </Tip>
      <div className="mt-1 flex gap-3 text-[11px] text-muted-foreground tabular">
        <span className="inline-flex items-center gap-1">
          <span className={cn("size-2 rounded-full", slate.dot)} /> before {plainMoney(b.committed_by_others)}
        </span>
        <span className="inline-flex items-center gap-1">
          <span className={cn("size-2 rounded-full", thisTone.dot)} /> this {plainMoney(b.this_invoice)}
          {b.this_state && <span>({b.this_state})</span>}
        </span>
      </div>
    </div>
  );
}

function Fact({ icon: Icon, label, children }: { icon: typeof FileText; label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-1 text-[11px] text-muted-foreground">
        <Icon className="size-3" /> {label}
      </div>
      <div className="mt-0.5 truncate text-[13px] font-medium">{children}</div>
    </div>
  );
}

/** A failed rule the reviewer must see first (app.yaml ui.review.callout_rules), e.g. an uncertain classification. */
function Callout({ r, name }: { r: RuleResult; name: string }) {
  const t = statusDef("severity", r.severity).style;
  return (
    <div role="alert" className={cn("mt-2 flex max-w-3xl items-start gap-2.5 rounded-md border px-3 py-2.5", t.border, t.soft)}>
      <TriangleAlert className={cn("mt-0.5 size-4 shrink-0", t.text)} />
      <div className="min-w-0 text-[13px] leading-relaxed">
        <span className="font-semibold">{name}: </span>
        {r.explanation}
      </div>
    </div>
  );
}

export function DecisionBar({
  c,
  balance,
  currency,
  lowConfidenceBelow,
  callouts,
}: {
  c: CaseDetail;
  balance: POBalance | null | undefined;
  currency: string;
  lowConfidenceBelow: number | undefined;
  callouts: { r: RuleResult; name: string }[];
}) {
  const caseTone = statusDef("case", c.status).style;
  const confidence = c.confidence === null ? null : Math.round(c.confidence * 100);
  // sticky review: the rules pass now, but a reviewer touched the case, so it waits for a reviewer
  const sticky = c.status === "needs_review" && !!c.decision && c.decision !== "needs_review";
  const [lead, ...rest] = (c.decision_summary ?? "").split(/(?<=\.)\s+/);
  // a called-out rule's explanation is shown once, in its callout, not again in the detail
  const detail = callouts.reduce((t, x) => t.replace(x.r.explanation, "").trim(), (sticky ? c.decision_summary : rest.join(" ")) ?? "");
  // the detail is clamped to two lines so the panes keep their height on a 720px screen
  const [expanded, setExpanded] = useState(false);
  return (
    <div className={cn("overflow-hidden rounded-lg border bg-card", caseTone.border)}>
      <div className="flex flex-col gap-4 px-5 py-3 lg:flex-row lg:items-start">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-muted-foreground">Decision</span>
            {sticky ? (
              <span className="inline-flex items-center gap-1.5 text-xs font-medium">
                <span className={cn("size-2 rounded-full", statusDef("rule", "pass").style.dot)} /> All checks pass after the reviewer's changes
              </span>
            ) : (
              c.decision && <StatusBadge kind="decision" value={c.decision} />
            )}
            {confidence !== null && (
              <Tip content="Lowest confidence among the required fields the extractor read">
                <span className={cn("text-xs tabular", lowConfidenceBelow !== undefined && confidence < lowConfidenceBelow * 100 ? toneStyle("amber").text : "text-muted-foreground")}>· {confidence}% read confidence</span>
              </Tip>
            )}
          </div>
          {sticky ? (
            <p className="mt-2 max-w-3xl text-sm font-medium">A reviewer changed this invoice, so a reviewer closes it: check template v{c.templates.length} and approve to finalise.</p>
          ) : (
            lead && <p className="mt-2 max-w-3xl text-sm font-medium leading-relaxed">{lead}</p>
          )}
          {callouts.map((x) => (
            <Callout key={x.r.id} r={x.r} name={x.name} />
          ))}
          {detail && (
            <div className="mt-1 max-w-3xl text-[13px] leading-relaxed text-muted-foreground">
              <p className={cn(!expanded && "line-clamp-2")}>{detail}</p>
              {detail.length > 180 && (
                <button className="text-xs font-medium text-primary hover:underline" onClick={() => setExpanded((v) => !v)} aria-expanded={expanded}>
                  {expanded ? "Less" : "More"}
                </button>
              )}
            </div>
          )}
        </div>
        <div className="grid shrink-0 grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)_auto] gap-x-6 gap-y-3 lg:w-[400px] xl:w-[480px]">
          <Fact icon={Building2} label="Vendor">
            {c.vendor_name ?? "Unknown vendor"}
          </Fact>
          <Fact icon={Receipt} label="Invoice">
            {c.invoice_number ?? "—"}
          </Fact>
          <Fact icon={FileText} label="Total">
            <span className="text-base font-semibold tabular">{money(c.total, currency)}</span>
          </Fact>
          <div className="col-span-3">
            {balance && c.status === "rejected" ? (
              <div className="text-xs text-muted-foreground">Not posted against {balance.po_number}: the invoice was rejected and its reservation released.</div>
            ) : balance ? (
              <POBalanceBar b={balance} />
            ) : (
              <div className="text-xs text-muted-foreground">No PO linked: coded to a GL account instead.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
