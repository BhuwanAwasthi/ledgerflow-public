import { Bell, ChevronDown, Eye, LogOut, Moon, Search, Settings, Sun } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/primitives";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
  Popover,
  PopoverContent,
  PopoverTrigger,
  Tip,
} from "@/components/ui/overlays";
import { CommandPalette } from "./CommandPalette";
import { ColdStartBanner } from "./ColdStartBanner";
import { useConfig } from "@/lib/config";
import { relative } from "@/lib/format";
import { useAuth, useMe, homePath } from "@/lib/auth";
import { GROUPS, homeFor, navFor } from "@/lib/nav";
import { ROLES, roleLabel, usePrefs, type Role } from "@/lib/prefs";
import { PRIVATE_ROUTES } from "@/lib/routes";
import { cn } from "@/lib/utils";

function Logo({ name, tagline }: { name: string; tagline?: string }) {
  const me = useMe();
  return (
    <Link to={homePath(me.data)} className="flex items-center gap-2.5 px-2">
      <svg viewBox="0 0 32 32" className="size-7 shrink-0" aria-hidden>
        <rect width="32" height="32" rx="8" className="fill-primary" />
        <path d="M9 9v14h14" stroke="white" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M14 18l4-4 3 3" stroke="#c7d2fe" strokeWidth="2.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <div className="min-w-0 leading-tight">
        <div className="text-[15px] font-semibold tracking-tight">{name}</div>
        {tagline && <div className="truncate text-[11px] text-muted-foreground">{tagline}</div>}
      </div>
    </Link>
  );
}

function Sidebar({ role }: { role: Role }) {
  const { data: cfg } = useConfig();
  const items = navFor(role);
  const queue = useQuery({
    queryKey: ["queue-count"],
    queryFn: async () => unwrap(await api.GET("/api/cases", { params: { query: { status: "needs_review,on_hold", page_size: 1 } } })).total,
    enabled: role !== "submitter",
    refetchInterval: 20_000,
  });
  return (
    <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r bg-surface lg:flex">
      <div className="flex h-14 items-center border-b px-3">
        <Logo name={cfg?.app.app.name ?? "LedgerFlow"} tagline={cfg?.app.app.tagline} />
      </div>
      <nav className="flex-1 overflow-y-auto px-3 py-4" aria-label="Main">
        {GROUPS.map((g) => {
          const group = items.filter((i) => i.group === g);
          if (!group.length) return null;
          return (
            <div key={g} className="mb-5">
              <div className="mb-1.5 px-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{g}</div>
              {group.map((i) => (
                <NavLink
                  key={i.to}
                  to={i.to}
                  className={({ isActive }) =>
                    cn(
                      "group mb-0.5 flex h-8 items-center gap-2.5 rounded-md px-2 text-[13px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
                      isActive && "bg-accent text-accent-foreground hover:bg-accent",
                    )
                  }
                >
                  <i.icon className="size-4 shrink-0" />
                  <span className="flex-1 truncate">{i.label}</span>
                  {i.to === "/queue" && !!queue.data && (
                    <span className="rounded-full bg-primary/10 px-1.5 text-[11px] font-semibold tabular text-primary">{queue.data}</span>
                  )}
                </NavLink>
              ))}
            </div>
          );
        })}
      </nav>
      <div className="border-t p-3 text-[11px] leading-relaxed text-muted-foreground">
        Your private workspace.
        <br />
        {cfg?.app.app.company}
      </div>
    </aside>
  );
}

/** A view toggle, not a permission: you are Submitter, Reviewer and Admin of your own workspace. It only
 * changes which screens the navigation shows. */
function ViewToggle() {
  const { role, setRole } = usePrefs();
  const navigate = useNavigate();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="gap-2" aria-label={`View: ${roleLabel(role)}. Change view`}>
          <Eye className="size-4 text-muted-foreground" />
          <span className="hidden sm:inline">{roleLabel(role)} view</span>
          <ChevronDown className="size-3.5 text-muted-foreground" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-72">
        <DropdownMenuLabel>
          Navigation view
          <div className="mt-0.5 text-xs font-normal text-muted-foreground">You can do everything in your workspace; this only changes the menu.</div>
        </DropdownMenuLabel>
        <DropdownMenuRadioGroup
          value={role}
          onValueChange={(v) => {
            const r = v as Role;
            setRole(r);
            navigate(homeFor(r));
          }}
        >
          {ROLES.map((r) => (
            <DropdownMenuRadioItem key={r.value} value={r.value}>
              <div>
                <div className="font-medium">{r.label}</div>
                <div className="text-xs text-muted-foreground">{r.hint}</div>
              </div>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function UserMenu() {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();
  if (!user) return null;
  const label = user.name || user.email;
  const initials = label.split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("");
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" className="rounded-full" aria-label={`Account: ${label}`}>
          {user.avatar_url ? (
            <img src={user.avatar_url} alt="" referrerPolicy="no-referrer" className="size-7 rounded-full" />
          ) : (
            <span className="flex size-7 items-center justify-center rounded-full bg-primary/10 text-[11px] font-semibold text-primary">{initials}</span>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <DropdownMenuLabel>
          <div className="truncate">{user.name ?? "Signed in"}</div>
          <div className="truncate text-xs font-normal text-muted-foreground">{user.email}</div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => navigate(PRIVATE_ROUTES.settings)}>
          <Settings className="size-4" /> Settings
        </DropdownMenuItem>
        <DropdownMenuItem
          onSelect={() => {
            void signOut().then(() => navigate("/", { replace: true }));
          }}
        >
          <LogOut className="size-4" /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function NotificationBell() {
  const { role } = usePrefs();
  const audience = role === "submitter" ? "submitter" : "reviewer";
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { data } = useQuery({
    queryKey: ["notifications", audience],
    queryFn: async () => unwrap(await api.GET("/api/notifications", { params: { query: { audience } } })),
    refetchInterval: 15_000,
  });
  const markRead = useMutation({
    mutationFn: async (id: number) => unwrap(await api.POST("/api/notifications/{notification_id}/read", { params: { path: { notification_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });
  const unread = (data ?? []).filter((n) => !n.read);
  return (
    <Popover>
      <Tip content="Notifications">
        <PopoverTrigger asChild>
          <Button variant="ghost" size="icon-sm" className="relative" aria-label={`Notifications (${unread.length} unread)`}>
            <Bell />
            {unread.length > 0 && (
              <span className="absolute right-1 top-1 flex size-4 items-center justify-center rounded-full bg-primary text-[10px] font-semibold text-primary-foreground">
                {unread.length > 9 ? "9+" : unread.length}
              </span>
            )}
          </Button>
        </PopoverTrigger>
      </Tip>
      <PopoverContent className="w-96 p-0">
        <div className="flex items-center justify-between border-b px-4 py-2.5">
          <div className="text-sm font-semibold">Notifications</div>
          <div className="text-xs text-muted-foreground">for {audience === "submitter" ? "submitters" : "the AP team"}</div>
        </div>
        <div className="max-h-96 overflow-y-auto">
          {(data ?? []).length === 0 && <div className="px-4 py-8 text-center text-sm text-muted-foreground">You're all caught up.</div>}
          {(data ?? []).slice(0, 30).map((n) => (
            <button
              key={n.id}
              onClick={() => {
                if (!n.read) markRead.mutate(n.id);
                if (n.case_id) navigate(`/cases/${n.case_id}`);
              }}
              className={cn("flex w-full gap-3 border-b px-4 py-3 text-left last:border-0 hover:bg-muted/60", !n.read && "bg-accent/40")}
            >
              <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", n.read ? "bg-transparent" : "bg-primary")} />
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] leading-snug">{n.text}</span>
                <span className="mt-0.5 block text-xs text-muted-foreground">{relative(n.created_at)}</span>
              </span>
            </button>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}

export function AppShell({ children }: { children?: ReactNode }) {
  const { role, dark, toggleDark } = usePrefs();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [location.pathname]);

  return (
    <div className="flex min-h-screen">
      <Sidebar role={role} />
      <div className="flex min-w-0 flex-1 flex-col">
        <ColdStartBanner />
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b bg-background/85 px-4 backdrop-blur lg:px-8">
          <button
            onClick={() => setPaletteOpen(true)}
            className="flex h-8 w-full max-w-sm items-center gap-2 rounded-md border bg-surface px-2.5 text-[13px] text-muted-foreground shadow-sm transition-colors hover:text-foreground"
          >
            <Search className="size-3.5" />
            <span className="flex-1 text-left">Jump to a case, screen or scenario…</span>
            <Kbd>Ctrl</Kbd>
            <Kbd>K</Kbd>
          </button>
          <div className="ml-auto flex items-center gap-1.5">
            <NotificationBell />
            <Tip content={dark ? "Light mode" : "Dark mode"}>
              <Button variant="ghost" size="icon-sm" onClick={toggleDark} aria-label="Toggle dark mode">
                {dark ? <Sun /> : <Moon />}
              </Button>
            </Tip>
            <div className="mx-1 h-5 w-px bg-border" />
            <ViewToggle />
            <UserMenu />
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 lg:px-8 lg:py-8">{children ?? <Outlet />}</main>
      </div>
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  );
}
