/**
 * The route map (build brief §4). main.tsx builds the router from these tables, and e2e/auth-gate.spec.ts
 * visits every one of them signed out, so a new screen is gated (and tested) by adding it here.
 * Plain data, no imports: Playwright loads this file directly.
 */

/** Reachable without a session: the public shell only. */
export const PUBLIC_ROUTES = {
  landing: "/",
  login: "/login",
  callback: "/auth/callback",
  privacy: "/privacy",
  terms: "/terms",
} as const;

/** Everything else needs a session (deep links included); signed out, they redirect to /login?next=<path>. */
export const PRIVATE_ROUTES = {
  launcher: "/launcher",
  dashboard: "/dashboard",
  submit: "/submit",
  submissions: "/submissions",
  cases: "/cases",
  liveRun: "/cases/:ref",
  review: "/cases/:ref/review",
  queue: "/queue",
  approved: "/approved",
  vendors: "/vendors",
  vendor: "/vendors/:id",
  pos: "/pos",
  po: "/pos/:id",
  studio: "/studio",
  studioTab: "/studio/:tab",
  whatif: "/whatif",
  guide: "/guide",
  settings: "/settings",
  portal: "/portal/:token",
  owner: "/owner",
  notFound: "*",
} as const;

/** Old paths from before the route map: each redirects to its new home (params carried over). */
export const REDIRECTS = {
  "/scenarios": "/launcher",
  "/library": "/approved",
  "/cases/:ref/live": "/cases/:ref",
} as const;

export type PrivateRoute = keyof typeof PRIVATE_ROUTES;

/** Fill `:param` segments, e.g. fillPath("/cases/:ref", { ref: "INV-2026-0003" }). */
export function fillPath(pattern: string, params: Record<string, string>): string {
  return pattern.replace(/:([A-Za-z_]+)/g, (_, k: string) => encodeURIComponent(params[k] ?? k));
}
