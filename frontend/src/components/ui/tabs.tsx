import * as T from "@radix-ui/react-tabs";
import type { ComponentPropsWithoutRef } from "react";
import { cn } from "@/lib/utils";

export const Tabs = T.Root;

export function TabsList({ className, ...p }: ComponentPropsWithoutRef<typeof T.List>) {
  return <T.List className={cn("flex items-center gap-1 border-b px-3", className)} {...p} />;
}

export function TabsTrigger({ className, ...p }: ComponentPropsWithoutRef<typeof T.Trigger>) {
  return (
    <T.Trigger
      className={cn(
        "relative -mb-px inline-flex h-10 items-center gap-1.5 border-b-2 border-transparent px-2.5 text-[13px] font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring data-[state=active]:border-primary data-[state=active]:text-foreground [&_svg]:size-3.5",
        className,
      )}
      {...p}
    />
  );
}

export function TabsContent({ className, ...p }: ComponentPropsWithoutRef<typeof T.Content>) {
  return <T.Content className={cn("focus-visible:outline-none", className)} {...p} />;
}
