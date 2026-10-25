/**
 * Google -> Supabase -> here with `?code=` (PKCE). The Supabase client exchanges the code on load
 * (detectSessionInUrl); this page waits for the session, then goes to the `next` path saved before the
 * redirect (re-checked by safeNext) or the home screen.
 */
import { AlertTriangle, LogIn } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { FullPageSpinner } from "@/components/auth/RequireAuth";
import { PublicShell } from "@/components/auth/PublicShell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { homePath, useAuth, useMe } from "@/lib/auth";
import { PUBLIC_ROUTES } from "@/lib/routes";

const GIVE_UP_MS = 15_000;

function providerError(): string | null {
  const q = new URLSearchParams(window.location.search);
  const h = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  return q.get("error_description") ?? h.get("error_description") ?? q.get("error") ?? h.get("error");
}

export default function AuthCallbackPage() {
  const { status, takeNext } = useAuth();
  const me = useMe();
  const error = useMemo(providerError, []);
  const [timedOut, setTimedOut] = useState(false);
  const [next, setNext] = useState<string | null | undefined>(undefined);

  useEffect(() => {
    const t = window.setTimeout(() => setTimedOut(true), GIVE_UP_MS);
    return () => window.clearTimeout(t);
  }, []);
  useEffect(() => {
    if (status === "signed_in" && next === undefined) setNext(takeNext());
  }, [status, next, takeNext]);

  if (status === "signed_in" && next !== undefined) {
    if (next) return <Navigate to={next} replace />;
    if (me.isLoading) return <FullPageSpinner label="Opening your workspace…" />;
    return <Navigate to={homePath(me.data)} replace />;
  }
  if (!error && !timedOut) return <FullPageSpinner label="Signing you in…" />;

  return (
    <PublicShell>
      <Card className="space-y-4 p-6 text-center">
        <AlertTriangle className="mx-auto size-6 text-destructive" />
        <h1 className="text-lg font-semibold">Sign-in didn't complete</h1>
        <p className="text-sm text-muted-foreground">
          {error ?? "Google didn't hand a session back in time. This can happen if the sign-in window was closed or cookies are blocked."}
        </p>
        <Button asChild className="w-full">
          <Link to={PUBLIC_ROUTES.login} replace>
            <LogIn /> Try again
          </Link>
        </Button>
      </Card>
    </PublicShell>
  );
}
