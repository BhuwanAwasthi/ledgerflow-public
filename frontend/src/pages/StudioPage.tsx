import { useMutation } from "@tanstack/react-query";
import { Bot, FileCode2, FileSpreadsheet, FileType2, FlaskConical, GitBranch, ListChecks, Settings, Workflow } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { parseDocument } from "yaml";
import { api, unwrap } from "@/api/client";
import { ErrorNote, PageHeader, StatusBadge } from "@/components/common";
import { ConfigEditor } from "@/components/studio/ConfigEditor";
import { RulesEditor } from "@/components/studio/RulesEditor";
import { TestAgent } from "@/components/studio/TestAgent";
import { WorkflowGraph } from "@/components/studio/WorkflowGraph";
import { Tip } from "@/components/ui/overlays";
import { Badge, Segmented, Skeleton, Switch } from "@/components/ui/primitives";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useConfig, type AgentConf } from "@/lib/config";
import { duration, usd } from "@/lib/format";
import { DraftsProvider, errorsOf, useAgentStats, useConfigFiles, useDrafts, useSaveConfig } from "@/lib/studio";
import { cn } from "@/lib/utils";

const KIND_LABEL: Record<AgentConf["kind"], string> = { llm: "LLM", code: "Code", hybrid: "Hybrid" };

function AgentList({ agents, order, selected, onSelect }: { agents: AgentConf[]; order: Map<string, number>; selected: string; onSelect: (id: string) => void }) {
  const stats = useAgentStats();
  const files = useConfigFiles();
  const save = useSaveConfig();
  const { drafts, setDraft } = useDrafts();
  const overridden = new Set((files.data ?? []).filter((f) => f.overridden).map((f) => f.path));
  const toggle = useMutation({
    mutationFn: async ({ agent, on }: { agent: AgentConf; on: boolean }) => {
      const path = `agents/${agent.id}.yaml`;
      const saved = unwrap(await api.GET("/api/config/{path}", { params: { path: { path } } })).content;
      const doc = parseDocument(saved);
      doc.set("enabled", on);
      const result = await save.mutateAsync({ path, content: String(doc) });
      // an unsaved draft of this file keeps its edits but takes the new flag, so saving it later can't undo the toggle
      const draft = drafts[path];
      if (draft !== undefined) {
        const d = parseDocument(draft);
        d.set("enabled", on);
        setDraft(path, String(d));
      }
      return result;
    },
    onSuccess: (_d, v) => toast.success(`${v.agent.name} ${v.on ? "enabled" : "disabled"}`, { description: "New cases run with this change." }),
    onError: (e, v) => toast.error(`Could not ${v.on ? "enable" : "disable"} ${v.agent.name}`, { description: errorsOf(e).join(" · ") }),
  });
  return (
    <div className="overflow-hidden rounded-lg border bg-card">
      <div className="border-b px-3 py-2 text-xs font-medium text-muted-foreground">{agents.length} agents</div>
      <ul className="h-[calc(100vh-330px)] min-h-[360px] overflow-y-auto">
        {agents.map((a) => {
          const s = stats.data?.[a.id];
          const path = `agents/${a.id}.yaml`;
          return (
            <li key={a.id}>
              <div
                role="button"
                tabIndex={0}
                onClick={() => onSelect(a.id)}
                onKeyDown={(e) => e.key === "Enter" && onSelect(a.id)}
                className={cn(
                  "flex cursor-pointer items-start gap-2.5 border-b px-3 py-2.5 last:border-0 hover:bg-muted/50 focus-visible:bg-muted focus-visible:outline-none",
                  a.id === selected && "bg-accent hover:bg-accent",
                )}
              >
                <span className="mt-0.5 w-4 shrink-0 text-right text-[11px] tabular text-muted-foreground">{order.get(a.id) ?? "·"}</span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className={cn("truncate text-[13px] font-medium", !a.enabled && "text-muted-foreground line-through")}>{a.name}</span>
                    {(overridden.has(path) || drafts[path] !== undefined) && (
                      <span className="size-1.5 shrink-0 rounded-full bg-primary" aria-label={drafts[path] !== undefined ? "unsaved edits" : "edited in Studio"} />
                    )}
                    <Tip content={a.enabled ? "Enabled. Turn off to skip its stage (the workflow must map skipped)" : "Disabled: its stage is skipped"}>
                      <span className="ml-auto">
                        <Switch label={`${a.enabled ? "Disable" : "Enable"} ${a.name}`} checked={a.enabled} disabled={toggle.isPending} onChange={(on) => toggle.mutate({ agent: a, on })} />
                      </span>
                    </Tip>
                  </div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted-foreground tabular">
                    <Badge className="px-1.5 py-0 text-[10px]">{KIND_LABEL[a.kind]}</Badge>
                    {s ? (
                      <>
                        <span>{s.runs} runs</span>
                        <span>avg {duration(s.avg_latency_ms)}</span>
                        {a.kind !== "code" && <span>{s.avg_cost_usd ? `${usd(s.avg_cost_usd)}/run` : "no cost"}</span>}
                      </>
                    ) : (
                      <span>not run yet</span>
                    )}
                  </div>
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function AgentPane({ agent }: { agent: AgentConf }) {
  const prompts = [...new Set(Object.values(agent.actions).map((a) => a.prompt).filter((p): p is string => !!p))];
  const [tab, setTab] = useState("yaml");
  const yamlPath = `agents/${agent.id}.yaml`;
  return (
    <div className="min-w-0">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="text-base font-semibold">{agent.name}</h2>
            <Badge>{KIND_LABEL[agent.kind]}</Badge>
            {!agent.enabled && <StatusBadge kind="stage" value="skipped" />}
          </div>
          <p className="mt-0.5 max-w-3xl text-[13px] text-muted-foreground">{agent.description}</p>
          <div className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-muted-foreground">
            <span className="font-mono">{agent.handler}</span>
            <span>actions: {Object.entries(agent.actions).map(([k, v]) => `${k} → ${v.outcomes.join(" | ")}`).join(";  ")}</span>
            {agent.model && (
              <span>
                model: {agent.model.deployment ?? "default deployment"} · effort {agent.model.reasoning_effort ?? "default"} · {agent.model.max_output_tokens ?? "—"} max tokens
              </span>
            )}
          </div>
        </div>
      </div>
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="mb-3 px-0">
          <TabsTrigger value="yaml">
            <FileCode2 /> {yamlPath}
          </TabsTrigger>
          {prompts.map((p) => (
            <TabsTrigger key={p} value={p}>
              <FileType2 /> {p}
            </TabsTrigger>
          ))}
          <TabsTrigger value="test">
            <FlaskConical /> Test agent
          </TabsTrigger>
        </TabsList>
        <TabsContent value="yaml">
          <ConfigEditor path={yamlPath} height="calc(100vh - 470px)" />
        </TabsContent>
        {prompts.map((p) => (
          <TabsContent key={p} value={p}>
            <ConfigEditor path={p} height="calc(100vh - 470px)" />
          </TabsContent>
        ))}
        <TabsContent value="test">
          <TestAgent agent={agent} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function FilePicker({ kind, label }: { kind: string; label: string }) {
  const files = useConfigFiles();
  const list = (files.data ?? []).filter((f) => f.kind === kind);
  const [path, setPath] = useState<string | null>(null);
  const current = path ?? list[0]?.path ?? null;
  if (files.isLoading) return <Skeleton className="h-96 rounded-lg" />;
  return (
    <div className="grid gap-4 lg:grid-cols-[240px_1fr]">
      <div className="overflow-hidden rounded-lg border bg-card">
        <div className="border-b px-3 py-2 text-xs font-medium text-muted-foreground">{label}</div>
        <ul>
          {list.map((f) => (
            <li key={f.path}>
              <button
                onClick={() => setPath(f.path)}
                className={cn("flex w-full items-center gap-2 border-b px-3 py-2 text-left text-[13px] last:border-0 hover:bg-muted/50", f.path === current && "bg-accent hover:bg-accent")}
              >
                <span className="min-w-0 flex-1 truncate font-mono text-xs">{f.path.split("/").pop()}</span>
                {f.overridden && <StatusBadge kind="config" value="override" className="text-[10px]" />}
              </button>
            </li>
          ))}
        </ul>
      </div>
      {current && <ConfigEditor key={current} path={current} />}
    </div>
  );
}

function StudioInner() {
  const cfgQ = useConfig();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "agents";
  const [wfView, setWfView] = useState<"graph" | "yaml">("graph");
  const [rulesView, setRulesView] = useState<"table" | "yaml">("table");
  const { drafts } = useDrafts();
  const set = (k: string, v: string) => setParams((p) => (p.set(k, v), p), { replace: true });
  const cfg = cfgQ.data;
  // pipeline order: the first workflow stage that uses each agent
  const order = new Map<string, number>();
  cfg?.workflow.stages.forEach((s) => s.agent && !order.has(s.agent) && order.set(s.agent, order.size + 1));
  const agents = cfg ? Object.values(cfg.agents).sort((a, b) => (order.get(a.id) ?? 99) - (order.get(b.id) ?? 99)) : [];
  const selected = params.get("agent") ?? agents[0]?.id ?? "";
  const agent = cfg?.agents[selected];
  const unsaved = Object.keys(drafts).length;

  return (
    <div>
      <PageHeader
        title="Agent Studio"
        description="Agents, prompts, rules and the workflow are config. Every edit is validated against the whole config before it is saved, versioned, and used by new cases. Adding an agent also needs a Python class in backend/app/agents/."
      />
      {cfgQ.error && <ErrorNote error={cfgQ.error} />}
      <Tabs value={tab} onValueChange={(v) => set("tab", v)}>
        <TabsList className="mb-4 px-0">
          <TabsTrigger value="agents">
            <Bot /> Agents
          </TabsTrigger>
          <TabsTrigger value="workflow">
            <Workflow /> Workflow
          </TabsTrigger>
          <TabsTrigger value="rules">
            <ListChecks /> Rules
          </TabsTrigger>
          <TabsTrigger value="erp">
            <FileSpreadsheet /> ERP profiles
          </TabsTrigger>
          <TabsTrigger value="doctypes">
            <GitBranch /> Document types
          </TabsTrigger>
          <TabsTrigger value="app">
            <Settings /> App settings
          </TabsTrigger>
          <div className="ml-auto flex items-center gap-3 pb-1">
            {unsaved > 0 && (
              <Tip content={Object.keys(drafts).join(", ")}>
                <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                  <StatusBadge kind="config" value="unsaved" /> {unsaved} file{unsaved === 1 ? "" : "s"}
                </span>
              </Tip>
            )}
            {cfg && (
              <Tip content="Hash of the running config. Each case stores the config it ran with.">
                <span className="font-mono text-[11px] text-muted-foreground">config {cfg.hash.slice(0, 10)}</span>
              </Tip>
            )}
          </div>
        </TabsList>

        <TabsContent value="agents">
          {!cfg ? (
            <Skeleton className="h-96 rounded-lg" />
          ) : (
            <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
              <AgentList agents={agents} order={order} selected={selected} onSelect={(id) => set("agent", id)} />
              {agent && <AgentPane key={agent.id} agent={agent} />}
            </div>
          )}
        </TabsContent>

        <TabsContent value="workflow" className="space-y-3">
          <div className="flex items-center gap-3">
            <Segmented
              label="Workflow view"
              value={wfView}
              onChange={setWfView}
              options={[
                { value: "graph", label: "Graph" },
                { value: "yaml", label: "Edit as YAML" },
              ]}
            />
            {wfView === "graph" && <span className="text-xs text-muted-foreground">Read-only: stages from workflow.yaml, edges labelled with the outcome that takes them. Dashed blue = resume after a wait.</span>}
          </div>
          {wfView === "graph" ? cfg ? <WorkflowGraph cfg={cfg} /> : <Skeleton className="h-96 rounded-lg" /> : <ConfigEditor path="workflow.yaml" />}
        </TabsContent>

        <TabsContent value="rules" className="space-y-3">
          <Segmented
            label="Rules view"
            value={rulesView}
            onChange={setRulesView}
            options={[
              { value: "table", label: "Table" },
              { value: "yaml", label: "Edit as YAML" },
            ]}
          />
          {rulesView === "table" ? <RulesEditor /> : <ConfigEditor path="rules.yaml" />}
        </TabsContent>

        <TabsContent value="erp">
          <FilePicker kind="erp_profile" label="ERP profiles" />
        </TabsContent>
        <TabsContent value="doctypes">
          <ConfigEditor path="document_types.yaml" />
        </TabsContent>
        <TabsContent value="app">
          <ConfigEditor path="app.yaml" />
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default function StudioPage() {
  return (
    <DraftsProvider>
      <StudioInner />
    </DraftsProvider>
  );
}
