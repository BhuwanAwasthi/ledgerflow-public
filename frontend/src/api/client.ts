import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

/** API origin: VITE_API_URL in prod (Vercel -> Render), localhost:8000 in dev. */
export const API_URL: string = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") ??
  (import.meta.env.DEV ? "http://localhost:8000" : "");

export const api = createClient<paths>({ baseUrl: API_URL });

/**
 * Every API call carries `Authorization: Bearer <access token>`. The token source and the 401 handler are
 * installed by AuthProvider (lib/auth.tsx): it knows whether the session is Supabase's or the dev stub's,
 * refreshes silently, and shows "Session expired" when a refresh fails.
 */
let tokenSource: () => Promise<string | null> = async () => null;
let onUnauthorized: () => void = () => {};

export function configureAuth(source: () => Promise<string | null>, unauthorized: () => void): void {
  tokenSource = source;
  onUnauthorized = unauthorized;
}

api.use({
  async onRequest({ request }) {
    const token = await tokenSource();
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    return request;
  },
  onResponse({ response }) {
    if (response.status === 401) onUnauthorized();
    return response;
  },
});

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
export type ExportBatch = S["ExportBatchOut"];
export type Vendor = S["VendorOut"];
export type VendorDetail = S["VendorDetailOut"];
export type VendorRequest = S["VendorRequestOut"];
export type PO = S["POOut"];
export type PODetail = S["PODetailOut"];
export type ConfigFile = S["ConfigFileOut"];
export type ConfigFileDetail = S["ConfigFileDetailOut"];
export type ConfigVersion = S["ConfigVersionOut"];
export type AgentStat = S["AgentStatOut"];
export type TestAgentResult = S["TestAgentOut"];
export type Portal = S["PortalOut"];

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public errors: string[] = [message],
  ) {
    super(message);
  }
}

/** Every message a 422 carries: config errors arrive as `detail.errors`, request validation as a list. */
export function errorList(error: unknown): string[] {
  if (error instanceof ApiError) return error.errors;
  return [error instanceof Error ? error.message : String(error)];
}

function errorsOf(error: unknown): string[] {
  if (error && typeof error === "object" && "detail" in error) {
    const d = (error as { detail: unknown }).detail;
    if (d && typeof d === "object" && "errors" in d && Array.isArray((d as { errors: unknown }).errors)) {
      return (d as { errors: unknown[] }).errors.map(String);
    }
  }
  return [];
}

function detailOf(error: unknown): string {
  if (error && typeof error === "object" && "detail" in error) {
    const d = (error as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (d && typeof d === "object" && "message" in d) return String((d as { message: unknown }).message);
    if (Array.isArray(d)) return d.map((x) => (x && typeof x === "object" && "msg" in x ? String(x.msg) : String(x))).join("; ");
  }
  return "Request failed";
}

/** Unwrap an openapi-fetch result or throw an ApiError with the server's `detail`. */
export function unwrap<T>(r: { data?: T; error?: unknown; response: Response }): T {
  if (r.error !== undefined || r.data === undefined) {
    const errors = errorsOf(r.error);
    throw new ApiError(detailOf(r.error), r.response.status, errors.length ? errors : [detailOf(r.error)]);
  }
  return r.data;
}

export function apiUrl(path: string): string {
  return `${API_URL}${path}`;
}

export type Me = S["MeOut"];

/**
 * A 60-second link to one file route (PDF, page PNG, template, ZIP, audit pack, CSV) for <img>, react-pdf
 * and downloads, which cannot send the Authorization header. Never put the access token in a URL.
 */
export async function signedFileUrl(path: string): Promise<string> {
  const r = unwrap(await api.POST("/api/me/file-token", { body: { path } }));
  return apiUrl(r.url);
}

/** Download a file route through a fresh file token (the server sends Content-Disposition). */
export async function downloadFile(path: string): Promise<void> {
  const a = document.createElement("a");
  a.href = await signedFileUrl(path);
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}
