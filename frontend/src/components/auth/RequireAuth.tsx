import { Loader2, LogIn, TimerOff } from "lucide-react";
import type { ReactNode } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/overlays";
import { useAuth } from "@/lib/auth";
import { loginPath } from "@/lib/safeNext";

export function FullPageSpinner({ label = "Checking your session…" }: { label?: string }) {
  return (
    <div role="status" className="flex min-h-screen items-center justify-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="size-4 animate-spin" /> {label}
    </div>
  );
}

/** "Session expired, sign in again": over the current screen, so the URL (and the way back) is kept. */
function SessionExpiredModal({ here }: { here: string }) {
  const navigate = useNavigate();
  return (
    <Dialog open>
      <DialogContent hideClose onEscapeKeyDown={(e) => e.preventDefault()} onPointerDownOutside={(e) => e.preventDefault()}>
        <div className="space-y-4 p-6">
          <div className="flex size-10 items-center justify-center rounded-full bg-muted">
            <TimerOff className="size-5 text-muted-foreground" />
          </div>
          <div>
            <DialogTitle className="text-base font-semibold">Session expired</DialogTitle>
            <DialogDescription className="mt-1 text-sm text-muted-foreground">
              Sign in again to carry on. You'll come back to this page; nothing in your workspace was lost.
            </DialogDescription>
          </div>
          <Button onClick={() => navigate(loginPath(here), { replace: true })} className="w-full">
            <LogIn /> Sign in again
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** Every signed-in route: no session -> /login?next=<this path>. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { status, expired } = useAuth();
  const loc = useLocation();
  const here = `${loc.pathname}${loc.search}${loc.hash}`;
  if (status === "loading") return <FullPageSpinner />;
  if (status === "signed_out" && !expired) return <Navigate to={loginPath(here)} replace />;
  return (
    <>
      {children}
      {expired && <SessionExpiredModal here={here} />}
    </>
  );
}
