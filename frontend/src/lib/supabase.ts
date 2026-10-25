/**
 * Supabase Auth (Google sign-in). The SDK owns the session: it stores it, refreshes the access token
 * before it expires and, on /auth/callback, exchanges the PKCE `?code=` for a session.
 * Null when VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY are missing or malformed; the login page shows
 * `supabaseConfigError` instead of the whole app failing to start.
 */
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const url = (import.meta.env.VITE_SUPABASE_URL as string | undefined)?.trim();
const anonKey = (import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined)?.trim();

function configError(): string | null {
  if (!url || !anonKey) return "Google sign-in isn't configured in this build (VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY).";
  try {
    const u = new URL(url);
    if (u.protocol !== "https:" && u.hostname !== "localhost" && u.hostname !== "127.0.0.1") throw new Error("not https");
    if (u.hostname.includes(".pooler.")) throw new Error("database host");
  } catch {
    return "VITE_SUPABASE_URL must be the project URL, https://<project-ref>.supabase.co (Supabase > Project Settings > API), not the database host.";
  }
  return null;
}

export const supabaseConfigError = configError();

export const supabase: SupabaseClient | null =
  supabaseConfigError === null && url && anonKey
    ? createClient(url, anonKey, {
        auth: { flowType: "pkce", persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
      })
    : null;

if (supabaseConfigError && (url || anonKey)) console.error(`[auth] ${supabaseConfigError}`);
