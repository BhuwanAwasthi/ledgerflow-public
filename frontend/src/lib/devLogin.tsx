/**
 * AUTH_MODE=dev stub login, for local runs and e2e only: any email gets a one-hour session from the
 * backend, no password. Never in a production build: vite.config.ts aliases this module to devLogin.off.ts
 * unless the dev server is running or VITE_DEV_LOGIN=true, and `npm run test:bundle` fails if a built
 * bundle contains it.
 */
import { FlaskConical, Loader2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { apiUrl } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/primitives";
import type { DevAuth, DevSession } from "./devAuthTypes";

const KEY = "lf-dev-session";

function load(): DevSession | null {
  try {
    const raw = localStorage.getItem(KEY);
    const s = raw ? (JSON.parse(raw) as DevSession) : null;
    return s && s.expires_at > Date.now() ? s : null;
  } catch {
    return null;
  }
}

function clear(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* private mode */
  }
}

async function signIn(email: string, name: string | null): Promise<DevSession> {
  const r = await fetch(apiUrl("/api/auth/dev-login"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, name }),
  });
  if (!r.ok) throw new Error(r.status === 404 ? "The API is not running with AUTH_MODE=dev." : `Dev sign-in failed (${r.status})`);
  const body = (await r.json()) as { access_token: string; user_id: string; expires_in: number };
  const session: DevSession = {
    access_token: body.access_token,
    expires_at: Date.now() + body.expires_in * 1000,
    user: { id: body.user_id, email, name },
  };
  try {
    localStorage.setItem(KEY, JSON.stringify(session));
  } catch {
    /* private mode: the session lasts until reload */
  }
  return session;
}

function Form({ onSignedIn }: { onSignedIn: (s: DevSession) => void }) {
  const [email, setEmail] = useState("dev@example.com");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSignedIn(await signIn(email.trim(), null));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="space-y-2 rounded-lg border border-dashed p-3" data-testid="dev-sign-in">
      <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <FlaskConical className="size-3.5" /> Dev sign-in (AUTH_MODE=dev, local only)
      </div>
      <Label htmlFor="dev-email" className="sr-only">
        Email
      </Label>
      <div className="flex gap-2">
        <Input id="dev-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="off" />
        <Button type="submit" variant="outline" disabled={busy}>
          {busy && <Loader2 className="animate-spin" />} Sign in
        </Button>
      </div>
      {error && <p className="text-xs text-destructive">{error}</p>}
    </form>
  );
}

export const devAuth: DevAuth | null = { load, clear, Form };
