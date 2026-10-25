import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Loader2 } from "lucide-react";
import { useState } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";
import { apiUrl, type Health } from "@/api/client";
import { FullPageSpinner } from "@/components/auth/RequireAuth";
import { Mark, PublicShell } from "@/components/auth/PublicShell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { homePath, useAuth, useMe } from "@/lib/auth";
import { devAuth } from "@/lib/devLogin";
import { PUBLIC_ROUTES } from "@/lib/routes";
import { safeNext } from "@/lib/safeNext";
import { supabaseConfigError } from "@/lib/supabase";

function GoogleIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden className="size-4">
      <path fill="#4285F4" d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.4h6.5a5.6 5.6 0 0 1-2.4 3.6v3h3.9c2.3-2.1 3.5-5.2 3.5-8.7z" />
      <path fill="#34A853" d="M12 24c3.2 0 6-1.1 7.9-2.9l-3.9-3c-1.1.7-2.4 1.2-4 1.2-3.1 0-5.7-2.1-6.6-4.9h-4v3.1A12 12 0 0 0 12 24z" />
      <path fill="#FBBC05" d="M5.4 14.4a7.2 7.2 0 0 1 0-4.7V6.6h-4a12 12 0 0 0 0 10.8l4-3z" />
      <path fill="#EA4335" d="M12 4.8c1.8 0 3.3.6 4.6 1.8l3.4-3.4A12 12 0 0 0 1.4 6.6l4 3.1C6.3 6.9 8.9 4.8 12 4.8z" />
    </svg>
  );
}

/** After sign-in: `next` when it is a safe same-origin path, else the mode's home screen. */
function SignedInRedirect({ next }: { next: string | null }) {
  const me = useMe();
  if (next) return <Navigate to={next} replace />;
  if (me.isLoading) return <FullPageSpinner label="Opening your workspace…" />;
  return <Navigate to={homePath(me.data)} replace />;
}

export default function LoginPage() {
  const { status, googleAvailable, signInWithGoogle, completeDevSignIn } = useAuth();
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"), window.location.origin);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(params.get("error"));
  // the stub form only when this build has it AND the API runs AUTH_MODE=dev
  const health = useQuery({
    queryKey: ["healthz"],
    queryFn: async (): Promise<Health> => (await fetch(apiUrl("/healthz"), { cache: "no-store" })).json() as Promise<Health>,
    enabled: !!devAuth,
    retry: false,
  });
  const DevForm = devAuth && health.data?.auth_mode === "dev" ? devAuth.Form : null;

  if (status === "loading") return <FullPageSpinner />;
  if (status === "signed_in") return <SignedInRedirect next={next} />;

  const google = async () => {
    setBusy(true);
    setError(null);
    try {
      await signInWithGoogle(next); // leaves the page for Google
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <PublicShell>
      <Card className="space-y-6 p-6 sm:p-8">
        <div className="space-y-2 text-center">
          <Mark className="mx-auto size-10" />
          <h1 className="text-xl font-semibold tracking-tight">Sign in to LedgerFlow</h1>
          <p className="text-sm text-muted-foreground">
            Your own private workspace with demo vendors, purchase orders and scenarios. Nothing you do is visible to anyone else.
          </p>
        </div>
        {next && (
          <p className="rounded-md bg-muted px-3 py-2 text-center text-xs text-muted-foreground">
            You'll go back to <span className="font-mono text-foreground">{next}</span> after signing in.
          </p>
        )}
        {error && (
          <div role="alert" className="flex gap-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" /> {error}
          </div>
        )}
        <Button className="w-full" size="lg" variant="outline" onClick={google} disabled={!googleAvailable || busy}>
          {busy ? <Loader2 className="animate-spin" /> : <GoogleIcon />} Continue with Google
        </Button>
        {!googleAvailable && supabaseConfigError && <p className="text-center text-xs text-muted-foreground">{supabaseConfigError}</p>}
        {DevForm && <DevForm onSignedIn={completeDevSignIn} />}
        <p className="text-center text-xs text-muted-foreground">
          We only receive your name, email and avatar from Google. See{" "}
          <Link className="underline" to={PUBLIC_ROUTES.privacy}>
            Privacy
          </Link>
          .
        </p>
      </Card>
    </PublicShell>
  );
}
