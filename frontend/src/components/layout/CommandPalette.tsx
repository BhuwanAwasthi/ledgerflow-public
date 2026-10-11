/** Ctrl/Cmd+K: jump to a case by ref, run a scenario, open a screen. */
import { useMutation, useQuery } from "@tanstack/react-query";
import { Command } from "cmdk";
import { FileText, Moon, Play } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { api, unwrap } from "@/api/client";
import { StatusBadge } from "@/components/common";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/overlays";
import { navFor } from "@/lib/nav";
import { usePrefs } from "@/lib/prefs";

const item =
  "flex cursor-default select-none items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] outline-none data-[selected=true]:bg-muted aria-selected:bg-muted";
const group = "px-1 [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:text-[11px] [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-muted-foreground";

export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const navigate = useNavigate();
  const { role, toggleDark } = usePrefs();
  const [search, setSearch] = useState("");

  useEffect(() => {
    if (!open) setSearch("");
  }, [open]);

  const cases = useQuery({
    queryKey: ["palette-cases", search],
    queryFn: async () => unwrap(await api.GET("/api/cases", { params: { query: { q: search || undefined, page_size: 8 } } })).items,
    enabled: open,
    staleTime: 5_000,
  });
  const fixtures = useQuery({
    queryKey: ["fixtures"],
    queryFn: async () => unwrap(await api.GET("/api/fixtures")),
    enabled: open,
    staleTime: 60_000,
  });
  const run = useMutation({
    mutationFn: async (id: string) => unwrap(await api.POST("/api/fixtures/{scenario_id}/run", { params: { path: { scenario_id: id } } })),
    onSuccess: (c) => {
      toast.success(`${c.ref} started`, { description: "Watching it live." });
      navigate(`/cases/${c.id}/live`);
    },
    onError: (e) => toast.error("Could not start the scenario", { description: e instanceof Error ? e.message : undefined }),
  });

  const go = (fn: () => void) => {
    onOpenChange(false);
    fn();
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl" hideClose aria-describedby={undefined}>
        <DialogTitle className="sr-only">Command palette</DialogTitle>
        <Command shouldFilter label="Command palette" className="flex max-h-[60vh] flex-col">
          <Command.Input
            value={search}
            onValueChange={setSearch}
            placeholder="Type a case ref, vendor, screen or scenario…"
            className="h-12 w-full border-b bg-transparent px-4 text-sm outline-none placeholder:text-muted-foreground"
          />
          <Command.List className="overflow-y-auto p-1.5">
            <Command.Empty className="py-8 text-center text-sm text-muted-foreground">No matches.</Command.Empty>
            {(cases.data ?? []).length > 0 && (
              <Command.Group heading="Cases" className={group}>
                {(cases.data ?? []).map((c) => (
                  <Command.Item
                    key={c.id}
                    value={`case ${c.ref} ${c.vendor_name ?? ""} ${c.invoice_number ?? ""} ${c.submitter_name ?? ""}`}
                    onSelect={() => go(() => navigate(`/cases/${c.id}/live`))}
                    className={item}
                  >
                    <FileText className="size-4 text-muted-foreground" />
                    <span className="font-medium tabular">{c.ref}</span>
                    <span className="truncate text-muted-foreground">{c.vendor_name ?? c.submitter_name}</span>
                    <span className="ml-auto">
                      <StatusBadge kind="case" value={c.status} />
                    </span>
                  </Command.Item>
                ))}
              </Command.Group>
            )}
            <Command.Group heading="Screens" className={group}>
              {navFor(role).map((n) => (
                <Command.Item key={n.to} value={`screen ${n.label} ${n.keywords ?? ""}`} onSelect={() => go(() => navigate(n.to))} className={item}>
                  <n.icon className="size-4 text-muted-foreground" />
                  {n.label}
                </Command.Item>
              ))}
              <Command.Item value="toggle dark light theme mode" onSelect={() => go(toggleDark)} className={item}>
                <Moon className="size-4 text-muted-foreground" />
                Toggle dark mode
              </Command.Item>
            </Command.Group>
            {(fixtures.data ?? []).length > 0 && (
              <Command.Group heading="Run a scenario" className={group}>
                {(fixtures.data ?? []).map((f) => (
                  <Command.Item
                    key={f.id}
                    value={`run scenario ${f.id} ${f.title}`}
                    disabled={!f.available || run.isPending}
                    onSelect={() => go(() => run.mutate(f.id))}
                    className={item}
                  >
                    <Play className="size-4 text-muted-foreground" />
                    <span className="truncate">Run: {f.title}</span>
                    <span className="ml-auto font-mono text-[11px] text-muted-foreground">{f.id.split("_")[0]}</span>
                  </Command.Item>
                ))}
              </Command.Group>
            )}
          </Command.List>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
