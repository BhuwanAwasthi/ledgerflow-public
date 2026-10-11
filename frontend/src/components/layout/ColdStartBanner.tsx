/** "Waking the engine…" when /healthz is slow (Render free sleeps after 15 min) or unreachable. */
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, PlugZap } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { apiUrl } from "@/api/client";
import { useConfig } from "@/lib/config";
import { toneStyle } from "@/lib/status";
import { cn } from "@/lib/utils";

type State = "ok" | "waking" | "down";

export function ColdStartBanner() {
  const { data: cfg } = useConfig();
  const threshold = cfg?.app.ui.cold_start_after_ms ?? 2500;
  const [state, setState] = useState<State>("ok");
  const [elapsed, setElapsed] = useState(0);
  const qc = useQueryClient();
  const wasCold = useRef(false);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    const check = async () => {
      const started = Date.now();
      const slow = window.setTimeout(() => !cancelled && setState("waking"), threshold);
      const tick = window.setInterval(() => !cancelled && setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
      let next = 60_000;
      try {
        const r = await fetch(apiUrl("/healthz"), { cache: "no-store" });
        if (!r.ok) throw new Error(String(r.status));
        if (!cancelled) {
          if (wasCold.current) qc.invalidateQueries();
          wasCold.current = false;
          setState("ok");
        }
      } catch {
        if (!cancelled) {
          wasCold.current = true;
          setState("down");
          next = 4_000;
        }
      } finally {
        window.clearTimeout(slow);
        window.clearInterval(tick);
        if (Date.now() - started > threshold) wasCold.current = true;
      }
      if (!cancelled) timer = window.setTimeout(check, next);
    };
    check();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [threshold, qc]);

  if (state === "ok") return null;
  const t = toneStyle(state === "waking" ? "violet" : "amber");
  return (
    <div role="status" className={cn("flex items-center gap-2.5 border-b px-4 py-2 text-[13px] lg:px-8", t.soft, t.border)}>
      {state === "waking" ? <Loader2 className={cn("size-4 animate-spin", t.text)} /> : <PlugZap className={cn("size-4", t.text)} />}
      <span className="font-medium">{state === "waking" ? "Waking the engine…" : "Can't reach the engine yet. Retrying…"}</span>
      <span className="text-muted-foreground">
        The free API server sleeps after 15 minutes idle; the first request takes about a minute.
        {state === "waking" && elapsed > 0 && ` ${elapsed}s so far.`}
      </span>
    </div>
  );
}
