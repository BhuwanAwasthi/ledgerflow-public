/**
 * The `next` parameter of /login and /auth/callback is attacker-controlled (anyone can craft a sign-in
 * link). Only a same-origin relative path is ever followed; anything else falls back to the home screen.
 * Plain module, no imports: e2e/safe-next.spec.ts unit-tests it.
 */

const AUTH_PATHS = new Set(["/login", "/auth/callback"]);

export function safeNext(next: string | null | undefined, origin: string): string | null {
  if (!next || next.length > 2048) return null;
  // a relative path: one leading slash, never "//host" or "/\host" (browsers treat both as another origin)
  if (!next.startsWith("/") || next.startsWith("//") || next.includes("\\")) return null;
  if (/[\u0000-\u001f\u007f]/.test(next)) return null; // tabs/newlines are stripped by URL parsers
  let url: URL;
  try {
    url = new URL(next, origin);
  } catch {
    return null;
  }
  if (url.origin !== new URL(origin).origin) return null;
  if (AUTH_PATHS.has(url.pathname)) return null; // no sign-in loops
  return `${url.pathname}${url.search}${url.hash}`;
}

/** "/login?next=<current path>" for a location (pathname + search + hash). */
export function loginPath(current: string): string {
  return current && current !== "/" ? `/login?next=${encodeURIComponent(current)}` : "/login";
}
