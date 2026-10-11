import { forwardRef, type HTMLAttributes, type InputHTMLAttributes, type LabelHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export function Badge({ className, ...p }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground ring-1 ring-inset ring-border",
        className,
      )}
      {...p}
    />
  );
}

export function Skeleton({ className, ...p }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("skeleton h-4 w-full", className)} aria-hidden {...p} />;
}

const field =
  "w-full rounded-md border border-input bg-surface px-3 text-sm shadow-sm placeholder:text-muted-foreground/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:opacity-50";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(({ className, ...p }, ref) => (
  <input ref={ref} className={cn(field, "h-9", className)} {...p} />
));
Input.displayName = "Input";

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(({ className, ...p }, ref) => (
  <textarea ref={ref} className={cn(field, "min-h-[84px] py-2", className)} {...p} />
));
Textarea.displayName = "Textarea";

export function Label({ className, ...p }: LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("text-[13px] font-medium text-foreground", className)} {...p} />;
}

export function Kbd({ className, ...p }: HTMLAttributes<HTMLElement>) {
  return (
    <kbd
      className={cn("inline-flex h-5 min-w-5 items-center justify-center rounded border bg-muted px-1 font-sans text-[11px] font-medium text-muted-foreground", className)}
      {...p}
    />
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-md border bg-muted/60 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          onClick={() => onChange(o.value)}
          className={cn(
            "h-7 rounded-[5px] px-3 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            o.value === value && "bg-surface text-foreground shadow-sm",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
