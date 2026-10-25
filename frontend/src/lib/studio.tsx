/**
 * Agent Studio plumbing: config file queries, save / reset, unsaved drafts shared across tabs (so a test run can
 * use them), and mapping the loader's error messages ("rules.yaml: rules.3.severity: ...") to editor lines.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { parseDocument, type Document } from "yaml";
import { api, ApiError, unwrap, type ConfigFileDetail } from "@/api/client";
import { useActor } from "@/lib/auth";

export function useConfigFiles() {
  return useQuery({ queryKey: ["config-files"], queryFn: async () => unwrap(await api.GET("/api/config/files")) });
}

export function useConfigFile(path: string | null) {
  return useQuery({
    queryKey: ["config-file", path],
    queryFn: async () => unwrap(await api.GET("/api/config/{path}", { params: { path: { path: path ?? "" } } })),
    enabled: !!path,
  });
}

export function useAgentStats() {
  return useQuery({ queryKey: ["agent-stats"], queryFn: async () => unwrap(await api.GET("/api/config/agent-stats")), refetchInterval: 30_000 });
}

function useRefreshConfig() {
  const qc = useQueryClient();
  return (path: string, detail: ConfigFileDetail) => {
    qc.setQueryData(["config-file", path], detail);
    qc.invalidateQueries({ queryKey: ["config-files"] });
    qc.invalidateQueries({ queryKey: ["config"] });
    qc.invalidateQueries({ queryKey: ["health"] });
  };
}

export function useSaveConfig() {
  const actor = useActor();
  const refresh = useRefreshConfig();
  return useMutation({
    mutationFn: async ({ path, content }: { path: string; content: string }) =>
      unwrap(await api.PUT("/api/config/{path}", { params: { path: { path } }, body: { content, author: actor } })),
    onSuccess: (d, v) => refresh(v.path, d),
  });
}

export function useResetConfig() {
  const refresh = useRefreshConfig();
  return useMutation({
    mutationFn: async (path: string) => unwrap(await api.DELETE("/api/config/{path}", { params: { path: { path } } })),
    onSuccess: (d, path) => refresh(path, d),
  });
}

export async function validateEdits(edits: Record<string, string>): Promise<string[]> {
  const r = unwrap(await api.POST("/api/config/validate", { body: { edits } }));
  return r.errors;
}

export function errorsOf(e: unknown): string[] {
  return e instanceof ApiError ? e.errors : [e instanceof Error ? e.message : String(e)];
}

/* ---------------- drafts ---------------- */
interface Drafts {
  drafts: Record<string, string>;
  setDraft: (path: string, content: string | undefined) => void;
}
const DraftCtx = createContext<Drafts | null>(null);

export function DraftsProvider({ children }: { children: ReactNode }) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const setDraft = useCallback((path: string, content: string | undefined) => {
    setDrafts((d) => {
      const next = { ...d };
      if (content === undefined) delete next[path];
      else next[path] = content;
      return next;
    });
  }, []);
  const value = useMemo(() => ({ drafts, setDraft }), [drafts, setDraft]);
  return <DraftCtx.Provider value={value}>{children}</DraftCtx.Provider>;
}

export function useDrafts(): Drafts {
  const ctx = useContext(DraftCtx);
  if (!ctx) throw new Error("useDrafts outside DraftsProvider");
  return ctx;
}

/* ---------------- errors -> lines ---------------- */
export interface LineError {
  line: number | null; // 1-based; null = not located in this file
  message: string;
}

function lineAt(text: string, offset: number): number {
  let line = 1;
  for (let i = 0; i < offset && i < text.length; i++) if (text.charCodeAt(i) === 10) line++;
  return line;
}

/** Offset of the deepest existing node on the key path (a missing key points at its parent). */
function nodeOffset(doc: Document, keys: (string | number)[]): number | null {
  for (let n = keys.length; n > 0; n--) {
    const node: unknown = doc.getIn(keys.slice(0, n), true);
    if (node && typeof node === "object" && "range" in node) {
      const r = (node as { range?: [number, number, number] | null }).range;
      if (r) return r[0];
    }
  }
  return null;
}

/** The loader's messages for `path`, each with the editor line it points at when it can be found. */
export function locateErrors(path: string, content: string, errors: string[]): { mine: LineError[]; others: string[] } {
  const mine: LineError[] = [];
  const others: string[] = [];
  let doc: Document | null = null;
  try {
    doc = parseDocument(content);
  } catch {
    doc = null;
  }
  for (const e of errors) {
    if (!e.startsWith(`${path}:`)) {
      others.push(e);
      continue;
    }
    const rest = e.slice(path.length + 1).trim();
    const yamlLine = /line (\d+)/.exec(rest);
    if (yamlLine) {
      mine.push({ line: Number(yamlLine[1]), message: rest });
      continue;
    }
    const m = /^([A-Za-z0-9_.]+): (.*)$/s.exec(rest);
    let line: number | null = null;
    if (m && doc) {
      const keys = m[1].split(".").map((k) => (/^\d+$/.test(k) ? Number(k) : k));
      const off = nodeOffset(doc, keys);
      if (off !== null) line = lineAt(content, off);
    }
    const stage = /stage '([^']+)'/.exec(rest);
    if (line === null && stage) {
      const at = content.search(new RegExp(`id:\\s*${stage[1]}\\b`));
      if (at >= 0) line = lineAt(content, at);
    }
    mine.push({ line, message: rest });
  }
  return { mine, others };
}
