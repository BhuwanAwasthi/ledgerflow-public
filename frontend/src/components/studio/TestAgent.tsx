/** "Test agent": run one action on a chosen case as a dry run (nothing is stored) and show the output JSON. */
import { useMutation, useQuery } from "@tanstack/react-query";
import { FlaskConical, Loader2, Play, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { api, unwrap, type TestAgentResult } from "@/api/client";
import { StatusBadge } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Badge, Checkbox, Label, Select } from "@/components/ui/primitives";
import type { AgentConf } from "@/lib/config";
import { duration, usd } from "@/lib/format";
import { statusDef } from "@/lib/status";
import { errorsOf, useDrafts } from "@/lib/studio";
import { cn } from "@/lib/utils";

interface RuleOut {
  rule_id: string;
  name: string;
  status: string;
  explanation: string;
}

function Output({ r, showConfidence }: { r: TestAgentResult; showConfidence: boolean }) {
  const rules = Array.isArray(r.data?.results) ? (r.data.results as RuleOut[]) : [];
  const tone = statusDef("stage", r.ok ? "completed" : "failed").style;
  const m = r.metrics as { tokens_in?: number; tokens_out?: number; cost_usd?: number; cache_hit?: boolean };
  return (
    <div className="space-y-3">
      <div className={cn("flex flex-wrap items-center gap-2 rounded-md border px-3 py-2", tone.border, tone.soft)}>
        <StatusBadge kind="stage" value={r.ok ? "completed" : "failed"} />
        {r.outcome && (
          <span className="text-[13px]">
            outcome <span className="font-mono font-medium">{r.outcome}</span>
          </span>
        )}
        {showConfidence && r.confidence !== null && r.confidence !== undefined && <Badge>{Math.round(r.confidence * 100)}% confident</Badge>}
        <span className="ml-auto flex flex-wrap gap-1.5 text-xs text-muted-foreground tabular">
          <span>{duration(r.duration_ms)}</span>
          {(m.tokens_in || m.tokens_out) && (
            <span>
              · {m.tokens_in ?? 0} in / {m.tokens_out ?? 0} out
            </span>
          )}
          {m.cost_usd !== undefined && <span>· {usd(m.cost_usd)}</span>}
          {m.cache_hit && <span>· cache hit</span>}
        </span>
      </div>
      {r.error && <div className={cn("rounded-md border px-3 py-2 font-mono text-xs", tone.border, tone.text)}>{r.error}</div>}
      {r.reasoning.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5 text-[13px]">
          {r.reasoning.map((x, i) => (
            <li key={i}>{x}</li>
          ))}
        </ul>
      )}
      {r.progress.length > 0 && <div className="text-xs text-muted-foreground">Steps: {r.progress.join(" → ")}</div>}
      {r.storage_writes.length > 0 && (
        <div className="text-xs text-muted-foreground">
          Would have written {r.storage_writes.length} file(s) (kept in memory, discarded): <span className="font-mono">{r.storage_writes.join(", ")}</span>
        </div>
      )}
      {rules.length > 0 && (
        <ul className="divide-y rounded-md border">
          {rules.map((x) => (
            <li key={x.rule_id} className="flex items-start gap-2 px-3 py-1.5 text-[13px]">
              <StatusBadge kind="rule" value={x.status} className="mt-px" />
              <span className="w-44 shrink-0 truncate font-medium" title={x.rule_id}>
                {x.name}
              </span>
              <span className="min-w-0 text-muted-foreground">{x.explanation}</span>
            </li>
          ))}
        </ul>
      )}
      {r.data && (
        <details className="rounded-md border" open={rules.length === 0}>
          <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-muted-foreground">Raw output (JSON)</summary>
          <pre className="max-h-[420px] overflow-auto border-t bg-muted/40 p-3 font-mono text-[11.5px] leading-relaxed">{JSON.stringify(r.data, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}

export function TestAgent({ agent }: { agent: AgentConf }) {
  const actions = Object.keys(agent.actions);
  const [action, setAction] = useState(actions[0] ?? "");
  const [caseId, setCaseId] = useState<number | null>(null);
  const [useDraftsFlag, setUseDraftsFlag] = useState(true);
  const { drafts } = useDrafts();
  const cases = useQuery({
    queryKey: ["cases", "studio-picker"],
    queryFn: async () => unwrap(await api.GET("/api/cases", { params: { query: { page_size: 50 } } })).items.filter((c) => c.status !== "split"),
  });
  const chosen = caseId ?? cases.data?.[0]?.id ?? null;
  const pending = Object.keys(drafts);
  const run = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/config/test-agent", {
          body: { agent: agent.id, action: action || actions[0], case_id: chosen ?? 0, edits: useDraftsFlag ? drafts : {} },
        }),
      ),
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">Action</Label>
          <Select value={action} onChange={(e) => setAction(e.target.value)} aria-label="Action" className="block">
            {actions.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">Case</Label>
          <Select value={chosen ?? ""} onChange={(e) => setCaseId(Number(e.target.value))} aria-label="Case" className="block min-w-[360px] max-w-[460px]">
            {(cases.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.ref} · {c.vendor_name ?? c.submitter_name ?? "unknown vendor"} · {statusDef("case", c.status).label}
              </option>
            ))}
          </Select>
        </div>
        <Button onClick={() => run.mutate()} disabled={!chosen || run.isPending}>
          {run.isPending ? <Loader2 className="animate-spin" /> : <Play />} Test {agent.name}
        </Button>
      </div>
      {pending.length > 0 && (
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          <Checkbox label="Use unsaved edits" checked={useDraftsFlag} onChange={setUseDraftsFlag} />
          Use my unsaved edits ({pending.join(", ")})
        </label>
      )}
      <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
        <FlaskConical className="mt-px size-3.5 shrink-0" />
        Dry run: the agent runs on a throwaway copy of the case data with the current config{pending.length && useDraftsFlag ? " plus your edits" : ""}. Nothing it writes is kept, and the case does not move. LLM agents do call the model.
      </p>
      {cases.data?.length === 0 && <div className="text-[13px] text-muted-foreground">No cases yet. Run a scenario first.</div>}
      {run.error && (
        <div className={cn("rounded-md border px-3 py-2 text-xs", statusDef("config", "invalid").style.border, statusDef("config", "invalid").style.soft)}>
          <div className="mb-1 flex items-center gap-1.5 font-medium">
            <TriangleAlert className="size-3.5" /> Could not run
          </div>
          <ul className="space-y-0.5 font-mono">
            {errorsOf(run.error).map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      )}
      {run.data && <Output r={run.data} showConfidence={agent.kind !== "code"} />}
    </div>
  );
}
