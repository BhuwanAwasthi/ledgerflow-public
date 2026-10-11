/** Courier-style horizontal tracker (SPEC §10.2). Steps and their states come from GET /api/cases/{id}/journey. */
import { AlertTriangle, Check, Hourglass, SkipForward } from "lucide-react";
import type { Journey } from "@/api/client";
import { shortDate } from "@/lib/format";
import { statusDef } from "@/lib/status";
import { cn } from "@/lib/utils";

const ICON = { failed: AlertTriangle, waiting: Hourglass, skipped: SkipForward, done: Check } as const;

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

export function JourneyTracker({ journey, compact }: { journey: Journey; compact?: boolean }) {
  const steps = journey.steps;
  return (
    <ol className="flex w-full items-start" aria-label={`Journey of ${journey.ref}`}>
      {steps.map((s, i) => {
        const d = statusDef("stage", s.state);
        const filled = s.state === "done" || s.state === "current" || s.state === "waiting" || s.state === "failed";
        const Icon = ICON[s.state as keyof typeof ICON];
        // a connector is solid when the step it leads into was reached (done, skipped past, or active)
        const reachedInto = s.state !== "pending";
        return (
          <li key={s.label} className="relative flex min-w-0 flex-1 flex-col items-center text-center">
            {i > 0 && (
              <div
                className={cn("absolute right-1/2 top-[11px] h-0.5 w-full -translate-y-1/2", reachedInto ? statusDef("stage", "done").style.dot : "bg-border")}
                aria-hidden
              />
            )}
            <div
              className={cn(
                "relative z-10 flex size-[22px] items-center justify-center rounded-full ring-4 ring-card",
                filled && cn(d.style.dot, "text-white"),
                s.state === "skipped" && "border-2 border-dashed border-muted-foreground/40 bg-card text-muted-foreground",
                s.state === "pending" && "border-2 border-border bg-card",
                (s.state === "current" || s.state === "waiting") && "animate-pulse-ring",
              )}
              title={d.label}
            >
              {Icon && <Icon className="size-3" strokeWidth={3} />}
              {s.state === "current" && <span className="size-2 rounded-full bg-white" />}
            </div>
            <div className={cn("mt-2 w-full truncate px-0.5 text-[11.5px] font-medium", filled ? "text-foreground" : "text-muted-foreground")}>{s.label}</div>
            {!compact && (
              <div className="h-4 truncate px-0.5 text-[10.5px] tabular text-muted-foreground">
                {s.at ? `${shortDate(s.at)}, ${clock(s.at)}` : s.state === "skipped" ? "Not needed" : s.state === "waiting" ? d.label : ""}
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
