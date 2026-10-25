/**
 * The signed-in session. Two sources:
 * - Supabase Auth (Google): the SDK stores the session and refreshes the access token before it expires;
 *   a 401 triggers one more refresh. When refreshing fails, `expired` turns on and RequireAuth shows the
 *   "Session expired" modal over the current screen, so signing in again returns to the same URL.
 * - the AUTH_MODE=dev stub (lib/devLogin.tsx), absent from production builds.
 * Switching users clears the query cache, so one workspace's data never shows in another's session.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { Session } from "@supabase/supabase-js";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, configureAuth, unwrap, type Me } from "@/api/client";
import { devAuth } from "@/lib/devLogin";
import type { DevSession } from "@/lib/devAuthTypes";
import { PRIVATE_ROUTES } from "@/lib/routes";
import { safeNext } from "@/lib/safeNext";
import { supabase } from "@/lib/supabase";

export type AuthStatus = "loading" | "signed_in" | "signed_out";

export interface AuthUser {
  id: string;
  email: string;
  name: string | null;
  avatar_url: string | null;
}

interface Current {
  source: "supabase" | "dev";
  token: string;
  expiresAt: number; // epoch ms
  user: AuthUser;
}

interface AuthContext {
  status: AuthStatus;
  user: AuthUser | null;
  /** the session ended without the user signing out (refresh failed): RequireAuth shows the modal */
  expired: boolean;
  googleAvailable: boolean;
  devAvailable: boolean;
  signInWithGoogle: (next: string | null) => Promise<void>;
  completeDevSignIn: (s: DevSession) => void;
  signOut: () => Promise<void>;
  /** where to go after sign-in when the stored `next` is absent or unsafe */
  takeNext: () => string | null;
}

const NEXT_KEY = "lf-auth-next";
const Ctx = createContext<AuthContext | null>(null);

function fromSupabase(s: Session): Current {
  const meta = (s.user.user_metadata ?? {}) as Record<string, unknown>;
  const str = (v: unknown) => (typeof v === "string" && v ? v : null);
  return {
    source: "supabase",
    token: s.access_token,
    expiresAt: (s.expires_at ?? 0) * 1000,
    user: {
      id: s.user.id,
      email: s.user.email ?? "",
      name: str(meta.full_name) ?? str(meta.name),
      avatar_url: str(meta.avatar_url) ?? str(meta.picture),
    },
  };
}

function fromDev(s: DevSession): Current {
  return { source: "dev", token: s.access_token, expiresAt: s.expires_at, user: { ...s.user, avatar_url: null } };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [current, setCurrent] = useState<Current | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [expired, setExpired] = useState(false);
  const ref = useRef<Current | null>(null);
  const signingOut = useRef(false);
  const refreshing = useRef<Promise<void> | null>(null);
  const lastUser = useRef<string | null>(null);

  const apply = useCallback((next: Current | null) => {
    ref.current = next;
    setCurrent(next);
    setStatus(next ? "signed_in" : "signed_out");
    if (next) setExpired(false);
  }, []);

  const expire = useCallback(() => {
    if (!ref.current || signingOut.current) return;
    if (ref.current.source === "dev") devAuth?.clear();
    apply(null);
    setExpired(true);
  }, [apply]);

  // initial session + Supabase events (sign-in on the callback page, refreshes, sign-out in another tab)
  useEffect(() => {
    const dev = devAuth?.load();
    if (dev) {
      apply(fromDev(dev));
      return;
    }
    if (!supabase) {
      apply(null);
      return;
    }
    let alive = true;
    supabase.auth.getSession().then(({ data }) => alive && apply(data.session ? fromSupabase(data.session) : null));
    const { data: sub } = supabase.auth.onAuthStateChange((event, session) => {
      if (!alive || ref.current?.source === "dev") return;
      if (session) apply(fromSupabase(session));
      else if (event === "SIGNED_OUT") {
        if (ref.current && !signingOut.current) expire();
        else apply(null);
      }
    });
    return () => {
      alive = false;
      sub.subscription.unsubscribe();
    };
  }, [apply, expire]);

  // dev sessions cannot refresh: expire them on time
  useEffect(() => {
    if (current?.source !== "dev") return;
    const t = window.setTimeout(expire, Math.max(current.expiresAt - Date.now(), 0));
    return () => window.clearTimeout(t);
  }, [current, expire]);

  // a different user (or none): drop every cached response of the previous workspace
  useEffect(() => {
    const id = current?.user.id ?? null;
    if (lastUser.current && lastUser.current !== id) qc.clear();
    if (id) lastUser.current = id;
  }, [current, qc]);

  useEffect(() => {
    configureAuth(
      async () => {
        const cur = ref.current;
        if (!cur) return null;
        if (cur.source === "dev" || !supabase) return cur.expiresAt > Date.now() ? cur.token : null;
        const { data } = await supabase.auth.getSession(); // refreshes first when the token has expired
        return data.session?.access_token ?? null;
      },
      () => {
        const cur = ref.current;
        if (!cur || refreshing.current) return;
        if (cur.source === "dev" || !supabase) return expire();
        const client = supabase;
        refreshing.current = client.auth
          .refreshSession()
          .then(({ data, error }) => {
            if (error || !data.session) expire();
          })
          .finally(() => {
            refreshing.current = null;
          });
      },
    );
  }, [expire]);

  const signInWithGoogle = useCallback(async (next: string | null) => {
    if (!supabase) throw new Error("Google sign-in is not configured");
    try {
      sessionStorage.setItem(NEXT_KEY, next ?? "");
    } catch {
      /* private mode: land on the home screen instead */
    }
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${window.location.origin}/auth/callback` },
    });
    if (error) throw error;
  }, []);

  const completeDevSignIn = useCallback((s: DevSession) => apply(fromDev(s)), [apply]);

  const signOut = useCallback(async () => {
    signingOut.current = true;
    try {
      const cur = ref.current;
      devAuth?.clear();
      if (cur?.source === "supabase" && supabase) await supabase.auth.signOut();
    } finally {
      apply(null);
      setExpired(false);
      qc.clear();
      lastUser.current = null;
      signingOut.current = false;
    }
  }, [apply, qc]);

  const takeNext = useCallback(() => {
    try {
      const v = sessionStorage.getItem(NEXT_KEY);
      sessionStorage.removeItem(NEXT_KEY);
      return safeNext(v, window.location.origin);
    } catch {
      return null;
    }
  }, []);

  const value = useMemo<AuthContext>(
    () => ({
      status,
      user: current?.user ?? null,
      expired,
      googleAvailable: !!supabase,
      devAvailable: !!devAuth,
      signInWithGoogle,
      completeDevSignIn,
      signOut,
      takeNext,
    }),
    [status, current, expired, signInWithGoogle, completeDevSignIn, signOut, takeNext],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthContext {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}

/** GET /api/me: profile, mode (unset | demo | byok) and demo-data version. */
export function useMe() {
  const { status } = useAuth();
  return useQuery({
    queryKey: ["me"],
    queryFn: async (): Promise<Me> => unwrap(await api.GET("/api/me")),
    enabled: status === "signed_in",
    staleTime: 60_000,
  });
}

/** Default landing after sign-in: the scenario launcher in demo mode, the dashboard with your own key. */
export function homePath(me: Me | undefined): string {
  return me?.mode === "byok" ? PRIVATE_ROUTES.dashboard : PRIVATE_ROUTES.launcher;
}

/** The signed-in person as recorded on reviews, approvals and config versions. */
export function useActor(): string {
  const { user } = useAuth();
  return user?.name || user?.email || "AP Reviewer";
}
