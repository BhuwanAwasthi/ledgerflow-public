import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { statusDef, toneStyle, type StatusKind } from "@/lib/status";
import { cn } from "@/lib/utils";

export function StatusBadge({ kind, value, className, pulse }: { kind: StatusKind; value: string | null | undefined; className?: string; pulse?: boolean }) {
  const d = statusDef(kind, value);
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset", d.style.badge, className)}>
      <span className={cn("size-1.5 rounded-full", d.style.dot, pulse && "animate-pulse")} aria-hidden />
      {d.label}
    </span>
  );
}

export function StatusDot({ kind, value, className }: { kind: StatusKind; value: string | null | undefined; className?: string }) {
  const d = statusDef(kind, value);
  return <span className={cn("inline-block size-2 rounded-full", d.style.dot, className)} aria-label={d.label} title={d.label} />;
}

export function PageHeader({ title, description, actions, eyebrow }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; eyebrow?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow && <div className="mb-1 text-xs font-medium text-muted-foreground">{eyebrow}</div>}
        <h1 className="truncate text-xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function EmptyState({ icon: Icon, title, children, action, className }: { icon: LucideIcon; title: string; children?: ReactNode; action?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center rounded-lg border border-dashed px-6 py-14 text-center", className)}>
      <div className="mb-3 flex size-10 items-center justify-center rounded-full bg-muted">
        <Icon className="size-5 text-muted-foreground" />
      </div>
      <div className="text-sm font-medium">{title}</div>
      {children && <div className="mt-1 max-w-sm text-sm text-muted-foreground">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  const t = toneStyle("rose");
  return <div className={cn("rounded-md border px-3 py-2 text-sm", t.border, t.soft, t.text)}>{msg}</div>;
}
