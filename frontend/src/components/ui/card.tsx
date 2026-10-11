import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export function Card({ className, ...p }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-lg border bg-card text-card-foreground shadow-[0_1px_2px_rgba(16,24,40,0.04)]", className)} {...p} />;
}
export function CardHeader({ className, ...p }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex items-start justify-between gap-4 px-5 pb-3 pt-4", className)} {...p} />;
}
export function CardTitle({ className, ...p }: HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={cn("text-sm font-semibold leading-none tracking-tight", className)} {...p} />;
}
export function CardDescription({ className, ...p }: HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn("mt-1.5 text-xs text-muted-foreground", className)} {...p} />;
}
export function CardContent({ className, ...p }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-5 pb-5", className)} {...p} />;
}
