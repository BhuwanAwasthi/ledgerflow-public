/** Frame of the public pages (landing, login, callback, privacy, terms): no app data, no API calls. */
import { Moon, Sun } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { usePrefs } from "@/lib/prefs";
import { PUBLIC_ROUTES } from "@/lib/routes";
import { cn } from "@/lib/utils";

export function Mark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={cn("size-7 shrink-0", className)} aria-hidden>
      <rect width="32" height="32" rx="8" className="fill-primary" />
      <path d="M9 9v14h14" stroke="white" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M14 18l4-4 3 3" stroke="#c7d2fe" strokeWidth="2.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function PublicShell({ children, wide }: { children: ReactNode; wide?: boolean }) {
  const { dark, toggleDark } = usePrefs();
  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="flex h-14 items-center justify-between border-b px-4 lg:px-8">
        <Link to={PUBLIC_ROUTES.landing} className="flex items-center gap-2.5">
          <Mark />
          <span className="text-[15px] font-semibold tracking-tight">LedgerFlow</span>
        </Link>
        <Button variant="ghost" size="icon-sm" onClick={toggleDark} aria-label="Toggle dark mode">
          {dark ? <Sun /> : <Moon />}
        </Button>
      </header>
      <main className={cn("mx-auto w-full flex-1 px-4 py-10", wide ? "max-w-5xl" : "max-w-md")}>{children}</main>
      <footer className="flex flex-wrap items-center justify-center gap-4 border-t px-4 py-4 text-xs text-muted-foreground">
        <Link to={PUBLIC_ROUTES.privacy} className="hover:text-foreground hover:underline">
          Privacy
        </Link>
        <Link to={PUBLIC_ROUTES.terms} className="hover:text-foreground hover:underline">
          Terms
        </Link>
        <span>An AP invoice-to-decision engine · portfolio project</span>
      </footer>
    </div>
  );
}
