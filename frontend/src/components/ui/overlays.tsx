import * as D from "@radix-ui/react-dialog";
import * as M from "@radix-ui/react-dropdown-menu";
import * as P from "@radix-ui/react-popover";
import * as T from "@radix-ui/react-tooltip";
import { X } from "lucide-react";
import type { ComponentPropsWithoutRef, ReactNode } from "react";
import { cn } from "@/lib/utils";

/* ---------------- dialog ---------------- */
export const Dialog = D.Root;
export const DialogTitle = D.Title;
export const DialogDescription = D.Description;

export function DialogContent({
  className,
  children,
  hideClose,
  ...p
}: ComponentPropsWithoutRef<typeof D.Content> & { hideClose?: boolean; children: ReactNode }) {
  return (
    <D.Portal>
      <D.Overlay className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px] data-[state=open]:animate-in data-[state=open]:fade-in-0" />
      <D.Content
        className={cn(
          "fixed left-1/2 top-[14vh] z-50 w-[calc(100vw-2rem)] max-w-lg -translate-x-1/2 overflow-hidden rounded-xl border bg-popover text-popover-foreground shadow-2xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
          className,
        )}
        {...p}
      >
        {children}
        {!hideClose && (
          <D.Close className="absolute right-3 top-3 rounded-md p-1 text-muted-foreground hover:bg-muted" aria-label="Close">
            <X className="size-4" />
          </D.Close>
        )}
      </D.Content>
    </D.Portal>
  );
}

/* ---------------- dropdown menu ---------------- */
export const DropdownMenu = M.Root;
export const DropdownMenuTrigger = M.Trigger;
export const DropdownMenuRadioGroup = M.RadioGroup;

export function DropdownMenuContent({ className, sideOffset = 6, ...p }: ComponentPropsWithoutRef<typeof M.Content>) {
  return (
    <M.Portal>
      <M.Content
        sideOffset={sideOffset}
        className={cn(
          "z-50 min-w-[12rem] rounded-lg border bg-popover p-1 text-popover-foreground shadow-lg data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
          className,
        )}
        {...p}
      />
    </M.Portal>
  );
}
export function DropdownMenuLabel({ className, ...p }: ComponentPropsWithoutRef<typeof M.Label>) {
  return <M.Label className={cn("px-2 py-1.5 text-xs font-medium text-muted-foreground", className)} {...p} />;
}
export function DropdownMenuItem({ className, ...p }: ComponentPropsWithoutRef<typeof M.Item>) {
  return (
    <M.Item
      className={cn("flex cursor-default select-none items-center gap-2 rounded-md px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-muted", className)}
      {...p}
    />
  );
}
export function DropdownMenuSeparator({ className, ...p }: ComponentPropsWithoutRef<typeof M.Separator>) {
  return <M.Separator className={cn("-mx-1 my-1 h-px bg-border", className)} {...p} />;
}
export function DropdownMenuRadioItem({ className, children, ...p }: ComponentPropsWithoutRef<typeof M.RadioItem>) {
  return (
    <M.RadioItem
      className={cn(
        "relative flex cursor-default select-none items-start gap-2 rounded-md py-1.5 pl-7 pr-2 text-sm outline-none data-[highlighted]:bg-muted",
        className,
      )}
      {...p}
    >
      <span className="absolute left-2.5 top-[13px] flex size-2 items-center justify-center">
        <M.ItemIndicator>
          <span className="block size-2 rounded-full bg-primary" />
        </M.ItemIndicator>
      </span>
      {children}
    </M.RadioItem>
  );
}

/* ---------------- popover ---------------- */
export const Popover = P.Root;
export const PopoverTrigger = P.Trigger;

export function PopoverContent({ className, align = "end", sideOffset = 8, ...p }: ComponentPropsWithoutRef<typeof P.Content>) {
  return (
    <P.Portal>
      <P.Content
        align={align}
        sideOffset={sideOffset}
        className={cn(
          "z-50 w-80 rounded-lg border bg-popover text-popover-foreground shadow-lg outline-none data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
          className,
        )}
        {...p}
      />
    </P.Portal>
  );
}

/* ---------------- tooltip ---------------- */
export const TooltipProvider = T.Provider;

export function Tip({ content, children, side = "top" }: { content: ReactNode; children: ReactNode; side?: "top" | "bottom" | "left" | "right" }) {
  return (
    <T.Root delayDuration={250}>
      <T.Trigger asChild>{children}</T.Trigger>
      <T.Portal>
        <T.Content side={side} sideOffset={6} className="z-50 max-w-xs rounded-md bg-foreground px-2 py-1 text-xs text-background shadow-md">
          {content}
        </T.Content>
      </T.Portal>
    </T.Root>
  );
}
