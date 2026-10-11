import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

/** API origin: VITE_API_URL in prod (Vercel -> Render), localhost:8000 in dev. */
export const API_URL: string = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") ??
  (import.meta.env.DEV ? "http://localhost:8000" : "");

export const api = createClient<paths>({ baseUrl: API_URL });

type S = components["schemas"];
export type Case = S["CaseOut"];
export type CaseDetail = S["CaseDetailOut"];
export type CaseList = S["CaseListOut"];
export type StageEvent = S["StageEventOut"];
export type Dashboard = S["DashboardOut"];
export type Journey = S["JourneyOut"];
export type JourneyStep = S["JourneyStepOut"];
export type Fixture = S["FixtureOut"];
export type Notification = S["NotificationOut"];
export type Health = S["HealthOut"];
export type DocumentInfo = S["DocumentOut"];
export type RuleResult = S["RuleResultOut"];
export type TemplateVersion = S["TemplateVersionOut"];
export type ReviewActionRow = S["ReviewActionOut"];
export type Workspace = S["WorkspaceOut"];
export type WorkspaceColumn = S["WorkspaceColumnOut"];
export type CellFlag = S["CellFlagOut"];
export type POBalance = S["POBalanceOut"];
export type GridOp = S["SetFieldOp"] | S["SetGLOp"];
export type ReviewBody = S["ReviewIn"];

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

function detailOf(error: unknown): string {
  if (error && typeof error === "object" && "detail" in error) {
    const d = (error as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map((x) => (x && typeof x === "object" && "msg" in x ? String(x.msg) : String(x))).join("; ");
  }
  return "Request failed";
}

/** Unwrap an openapi-fetch result or throw an ApiError with the server's `detail`. */
export function unwrap<T>(r: { data?: T; error?: unknown; response: Response }): T {
  if (r.error !== undefined || r.data === undefined) {
    throw new ApiError(detailOf(r.error), r.response.status);
  }
  return r.data;
}

export function apiUrl(path: string): string {
  return `${API_URL}${path}`;
}
