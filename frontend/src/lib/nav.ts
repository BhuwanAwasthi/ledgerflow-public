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

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  roles: Role[];
  group: "Submit" | "Review" | "Master data" | "Build";
  keywords?: string;
}

export const NAV: NavItem[] = [
  { to: "/submit", label: "Submit invoices", icon: Upload, roles: ["submitter", "admin"], group: "Submit", keywords: "upload" },
  { to: "/submissions", label: "My submissions", icon: ClipboardList, roles: ["submitter", "admin"], group: "Submit", keywords: "journey track" },
  { to: "/", label: "Dashboard", icon: LayoutDashboard, roles: ["reviewer", "admin"], group: "Review", keywords: "kpi metrics" },
  { to: "/queue", label: "Work queue", icon: Inbox, roles: ["reviewer", "admin"], group: "Review", keywords: "review hold" },
  { to: "/cases", label: "All cases", icon: FileStack, roles: ["reviewer", "admin"], group: "Review", keywords: "invoices" },
  { to: "/library", label: "Approved library", icon: Library, roles: ["reviewer", "admin"], group: "Review", keywords: "export zip batch" },
  { to: "/vendors", label: "Vendors", icon: Building2, roles: ["reviewer", "admin"], group: "Master data" },
  { to: "/pos", label: "Purchase orders", icon: Boxes, roles: ["reviewer", "admin"], group: "Master data", keywords: "po" },
  { to: "/scenarios", label: "Scenario launcher", icon: PlayCircle, roles: ["submitter", "reviewer", "admin"], group: "Build", keywords: "demo fixtures run" },
  { to: "/studio", label: "Agent Studio", icon: Settings2, roles: ["admin"], group: "Build", keywords: "config yaml rules workflow" },
];

export const GROUPS: NavItem["group"][] = ["Submit", "Review", "Master data", "Build"];

export function navFor(role: Role): NavItem[] {
  return NAV.filter((n) => n.roles.includes(role));
}

export function homeFor(role: Role): string {
  return role === "submitter" ? "/submissions" : "/";
}

export const RULES_ICON = ListChecks;
