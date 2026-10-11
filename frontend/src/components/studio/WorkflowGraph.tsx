/** Read-only graph of workflow.yaml: stages layered by distance from `start`, outcome-labelled edges, terminals. */
import { Background, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Hand, Hourglass, Power } from "lucide-react";
import { useMemo } from "react";
import { usePrefs } from "@/lib/prefs";
import type { ResolvedConf, StageConf } from "@/lib/config";
import { statusDef, toneStyle } from "@/lib/status";
import { cn } from "@/lib/utils";

const END = "end:";
const COL_W = 216; // layers run left to right: the pane is wide, the graph is deep
const ROW_H = 92;

interface StageData extends Record<string, unknown> {
  stage?: StageConf;
  agentName?: string;
  enabled?: boolean;
  terminal?: string;
}

function StageNode({ data }: NodeProps<Node<StageData>>) {
  if (data.terminal) {
    const d = statusDef("case", data.terminal);
    return (
      <div className={cn("rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset", d.style.badge)}>
        <Handle type="target" position={Position.Left} className="!opacity-0" />
        end: {d.label}
      </div>
    );
  }
  const s = data.stage!;
  const indigo = toneStyle("indigo");
  return (
    <div
      className={cn(
        "w-[180px] rounded-lg border bg-card px-3 py-2 shadow-sm",
        s.hitl && cn(indigo.border, indigo.soft),
        data.enabled === false && "border-dashed opacity-60",
      )}
    >
      <Handle type="target" position={Position.Left} className="!size-1.5 !border-0 !bg-muted-foreground/50" />
      <div className="flex items-center gap-1.5">
        <span className="truncate text-[12px] font-semibold">{s.id}</span>
        {s.hitl && <Hand className={cn("size-3", indigo.text)} aria-label="human in the loop" />}
        {s.wait_for && <Hourglass className="size-3 text-muted-foreground" aria-label={`waits for ${s.wait_for}`} />}
        {data.enabled === false && <Power className="size-3 text-muted-foreground" aria-label="agent disabled" />}
      </div>
      <div className="truncate text-[11px] text-muted-foreground">{s.hitl ? "Human review" : `${data.agentName ?? s.agent} · ${s.action}`}</div>
      {s.wait_for && <div className="truncate text-[10px] text-muted-foreground">waits for {s.wait_for} → {s.resume_to}</div>}
      <Handle type="source" position={Position.Right} className="!size-1.5 !border-0 !bg-muted-foreground/50" />
    </div>
  );
}

const nodeTypes = { stage: StageNode };

export function WorkflowGraph({ cfg }: { cfg: ResolvedConf }) {
  const { dark } = usePrefs();
  const { nodes, edges } = useMemo(() => {
    const stages = cfg.workflow.stages;
    const byId = new Map(stages.map((s) => [s.id, s]));
    // layer = BFS distance from start along `next` (back edges keep the first layer found)
    const layer = new Map<string, number>([[cfg.workflow.start, 0]]);
    const queue = [cfg.workflow.start];
    while (queue.length) {
      const id = queue.shift()!;
      const s = byId.get(id);
      if (!s) continue;
      for (const t of Object.values(s.next)) {
        if (!layer.has(t)) {
          layer.set(t, (layer.get(id) ?? 0) + 1);
          if (!t.startsWith(END)) queue.push(t);
        }
      }
    }
    stages.forEach((s) => layer.has(s.id) || layer.set(s.id, 0));
    // terminals go one row below the deepest stage that targets them
    const maxStage = Math.max(...stages.map((s) => layer.get(s.id) ?? 0));
    for (const k of layer.keys()) if (k.startsWith(END)) layer.set(k, Math.min(layer.get(k)!, maxStage + 1));
    const rows = new Map<number, string[]>();
    for (const [k, l] of layer) rows.set(l, [...(rows.get(l) ?? []), k]);
    const height = Math.max(...[...rows.values()].map((r) => r.length));

    const nodes: Node<StageData>[] = [];
    for (const [l, ids] of rows) {
      const offset = ((height - ids.length) * ROW_H) / 2;
      ids.forEach((id, i) => {
        const terminal = id.startsWith(END) ? id.slice(END.length) : undefined;
        const s = byId.get(id);
        const agent = s?.agent ? cfg.agents[s.agent] : undefined;
        nodes.push({
          id,
          type: "stage",
          position: { x: l * COL_W, y: offset + i * ROW_H + (terminal ? 18 : 0) },
          data: { stage: s, terminal, agentName: agent?.name, enabled: agent ? agent.enabled : undefined },
          draggable: true,
        });
      });
    }
    const muted = "hsl(var(--muted-foreground) / 0.7)";
    const labelBg = { fill: "hsl(var(--card))" };
    const edges: Edge[] = [];
    for (const s of stages) {
      for (const [outcome, t] of Object.entries(s.next)) {
        const back = (layer.get(t) ?? 0) <= (layer.get(s.id) ?? 0);
        edges.push({
          id: `${s.id}-${outcome}`,
          source: s.id,
          target: t,
          label: outcome,
          type: back ? "default" : "smoothstep",
          animated: back,
          style: { stroke: muted, strokeWidth: 1.2 },
          labelStyle: { fontSize: 10, fill: "hsl(var(--muted-foreground))" },
          labelBgStyle: labelBg,
          labelBgPadding: [3, 1],
          markerEnd: { type: MarkerType.ArrowClosed, color: muted, width: 14, height: 14 },
        });
      }
      if (s.resume_to) {
        edges.push({
          id: `${s.id}-resume`,
          source: s.id,
          target: s.resume_to,
          label: `resume (${s.wait_for})`,
          animated: true,
          style: { stroke: toneStyle("sky").chart, strokeDasharray: "4 3" },
          labelStyle: { fontSize: 10, fill: toneStyle("sky").chart },
          labelBgStyle: labelBg,
          markerEnd: { type: MarkerType.ArrowClosed, color: toneStyle("sky").chart },
        });
      }
    }
    return { nodes, edges };
  }, [cfg]);

  return (
    <div className="h-[calc(100vh-300px)] min-h-[480px] overflow-hidden rounded-lg border bg-card">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.04, minZoom: 0.62 }} // readable labels; pan or use the minimap for the rest
        nodesConnectable={false}
        edgesFocusable={false}
        colorMode={dark ? "dark" : "light"}
        proOptions={{ hideAttribution: true }}
        minZoom={0.2}
      >
        <Background gap={16} size={1} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable ariaLabel="Workflow overview" nodeStrokeWidth={2} className="!rounded-md !border" />
      </ReactFlow>
    </div>
  );
}
