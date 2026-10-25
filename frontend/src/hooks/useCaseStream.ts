/**
 * Live stage_events for one case over SSE. The server replays stored events first, so a fresh page and a
 * reconnect both converge on the full history. On a drop we reconnect with backoff, passing `after=<last seq>`
 * so nothing is duplicated or lost. Each connection authenticates with a 60-second single-use stream token
 * (`?t=`), never the access token.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, apiUrl, unwrap, type StageEvent } from "@/api/client";

export type Connection = "connecting" | "live" | "reconnecting";

export interface CaseSummary {
  id: number;
  ref: string | null;
  status: string;
  current_stage: string | null;
  decision: string | null;
}

export interface StreamState {
  events: StageEvent[];
  progress: Record<string, string>;
  summary: CaseSummary | null;
  connection: Connection;
  attempts: number;
}

function merge(prev: StageEvent[], ev: StageEvent): StageEvent[] {
  if (prev.some((p) => p.seq === ev.seq)) return prev;
  const next = [...prev, ev];
  next.sort((a, b) => a.seq - b.seq);
  return next;
}

export function useCaseStream(caseId: number | null, enabled = true): StreamState {
  const qc = useQueryClient();
  const [state, setState] = useState<StreamState>({ events: [], progress: {}, summary: null, connection: "connecting", attempts: 0 });
  const lastSeq = useRef(0);

  useEffect(() => {
    if (caseId === null || !enabled) return;
    let es: EventSource | null = null;
    let retry: number | undefined;
    let attempts = 0;
    let closed = false;
    lastSeq.current = 0;
    setState({ events: [], progress: {}, summary: null, connection: "connecting", attempts: 0 });

    const retryLater = () => {
      if (closed) return;
      attempts += 1;
      setState((s) => ({ ...s, connection: "reconnecting", attempts }));
      retry = window.setTimeout(connect, Math.min(1000 * 2 ** (attempts - 1), 10_000));
    };

    const connect = async () => {
      // EventSource can't send the Authorization header: every (re)connect redeems a fresh single-use token
      let path: string;
      try {
        path = unwrap(await api.POST("/api/cases/{case_id}/stream-token", { params: { path: { case_id: caseId } } })).url;
      } catch {
        return retryLater();
      }
      if (closed) return;
      es = new EventSource(apiUrl(`${path}${lastSeq.current ? `&after=${lastSeq.current}` : ""}`));
      es.onopen = () => {
        attempts = 0;
        setState((s) => ({ ...s, connection: "live", attempts: 0 }));
      };
      es.addEventListener("stage_event", (m) => {
        const ev = JSON.parse((m as MessageEvent<string>).data) as StageEvent;
        lastSeq.current = Math.max(lastSeq.current, ev.seq);
        setState((s) => {
          const progress = { ...s.progress };
          if (ev.status !== "started") delete progress[ev.stage_id];
          return { ...s, events: merge(s.events, ev), progress };
        });
      });
      es.addEventListener("progress", (m) => {
        const p = JSON.parse((m as MessageEvent<string>).data) as { stage_id: string; message: string };
        setState((s) => ({ ...s, progress: { ...s.progress, [p.stage_id]: p.message } }));
      });
      es.addEventListener("case_updated", (m) => {
        const summary = JSON.parse((m as MessageEvent<string>).data) as CaseSummary;
        setState((s) => ({ ...s, summary }));
        qc.invalidateQueries({ queryKey: ["case", caseId] });
      });
      es.onerror = () => {
        es?.close();
        retryLater();
      };
    };
    void connect();
    return () => {
      closed = true;
      es?.close();
      window.clearTimeout(retry);
    };
  }, [caseId, enabled, qc]);

  return state;
}
