import {
  Boxes,
  Building2,
  ClipboardList,
  FileStack,
  Inbox,
  LayoutDashboard,
  Library,
  ListChecks,
  PlayCircle,
  Settings2,
  Upload,
  type LucideIcon,
} from "lucide-react";
import type { Role } from "./prefs";
import { PRIVATE_ROUTES } from "./routes";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** the view toggle shows each screen to these views; it never grants or removes access */
  roles: Role[];
  group: "Submit" | "Review" | "Master data" | "Build";
  keywords?: string;
}

const R = PRIVATE_ROUTES;

export const NAV: NavItem[] = [
  { to: R.submit, label: "Submit invoices", icon: Upload, roles: ["submitter", "admin"], group: "Submit", keywords: "upload" },
  { to: R.submissions, label: "My submissions", icon: ClipboardList, roles: ["submitter", "admin"], group: "Submit", keywords: "journey track" },
  { to: R.dashboard, label: "Dashboard", icon: LayoutDashboard, roles: ["reviewer", "admin"], group: "Review", keywords: "kpi metrics" },
  { to: R.queue, label: "Work queue", icon: Inbox, roles: ["reviewer", "admin"], group: "Review", keywords: "review hold" },
  { to: R.cases, label: "All cases", icon: FileStack, roles: ["reviewer", "admin"], group: "Review", keywords: "invoices" },
  { to: R.approved, label: "Approved library", icon: Library, roles: ["reviewer", "admin"], group: "Review", keywords: "export zip batch" },
  { to: R.vendors, label: "Vendors", icon: Building2, roles: ["reviewer", "admin"], group: "Master data" },
  { to: R.pos, label: "Purchase orders", icon: Boxes, roles: ["reviewer", "admin"], group: "Master data", keywords: "po" },
  { to: R.launcher, label: "Scenario launcher", icon: PlayCircle, roles: ["submitter", "reviewer", "admin"], group: "Build", keywords: "demo fixtures run" },
  { to: R.studio, label: "Agent Studio", icon: Settings2, roles: ["admin"], group: "Build", keywords: "config yaml rules workflow" },
];

export const GROUPS: NavItem["group"][] = ["Submit", "Review", "Master data", "Build"];

export function navFor(role: Role): NavItem[] {
  return NAV.filter((n) => n.roles.includes(role));
}

/** Where switching the view lands. */
export function homeFor(role: Role): string {
  return role === "submitter" ? R.submissions : R.dashboard;
}

/** `/cases/INV-2026-0003` (or `/review`): the ref when the case has one, else its id. */
export function caseLink(c: { id: number; ref?: string | null }, view?: "review"): string {
  const base = `/cases/${encodeURIComponent(c.ref || String(c.id))}`;
  return view ? `${base}/${view}` : base;
}

export const RULES_ICON = ListChecks;
