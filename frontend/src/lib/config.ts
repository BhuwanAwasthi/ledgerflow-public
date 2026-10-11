/**
 * Typed view of GET /api/config (the resolved YAML). The endpoint is an open dict in OpenAPI, so this file
 * names the fields the UI reads. Everything the UI shows about agents, stages, chips and branding comes
 * from here, never from constants in TSX.
 */
import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "@/api/client";

export interface AgentConf {
  id: string;
  name: string;
  description: string;
  kind: "llm" | "code" | "hybrid";
  enabled: boolean;
  actions: Record<string, { outcomes: string[]; prompt: string | null }>;
  handler: string;
  model: { deployment: string | null; reasoning_effort: string | null; max_output_tokens: number | null } | null;
}

export interface StageConf {
  id: string;
  agent: string | null;
  action: string | null;
  status_label: string;
  next: Record<string, string>;
  hitl: boolean;
  wait_for: string | null;
  resume_to: string | null;
}

export interface UiConf {
  journey: { label: string; stages: string[]; statuses: string[] }[];
  stage_chips: Record<string, string[]>;
  dashboard: { ranges: string[]; default_range: string; recent_cases: number };
  status_text: Record<string, string>;
  cold_start_after_ms: number;
  review: { low_confidence_below: number; callout_rules: string[] };
}

export interface AppConf {
  app: {
    name: string;
    tagline: string;
    company: string;
    case_ref_prefix: string;
    default_erp_profile: string;
    sla_hours: Record<string, number>;
  };
  features: Record<string, boolean>;
  limits: { max_upload_mb: number; max_pages_per_document: number };
  ui: UiConf;
}

export interface RuleConf {
  id: string;
  name: string;
  enabled: boolean;
  applies_to: string;
  severity: string;
  params: Record<string, unknown>;
  explain: string;
}

export interface ResolvedConf {
  app: AppConf;
  workflow: { start: string; stages: StageConf[] };
  rules: { currency: string; rules: RuleConf[] };
  agents: Record<string, AgentConf>;
  document_types: { min_confidence: number; labels: { id: string; description: string; route: string }[] };
  erp_profiles: Record<string, { id: string; name: string; file_format: string; grain: string }>;
  hash: string;
}

export function useConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: async () => unwrap(await api.GET("/api/config")) as unknown as ResolvedConf,
    staleTime: 5 * 60_000,
  });
}

export function stageOf(cfg: ResolvedConf | undefined, stageId: string | null | undefined): StageConf | undefined {
  return cfg?.workflow.stages.find((s) => s.id === stageId);
}
