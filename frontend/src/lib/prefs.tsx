/** Per-viewer preferences: demo role (no auth, SPEC §10) and light/dark theme. */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type Role = "submitter" | "reviewer" | "admin";

export const ROLES: { value: Role; label: string; hint: string }[] = [
  { value: "submitter", label: "Submitter", hint: "Upload invoices and track them" },
  { value: "reviewer", label: "AP Reviewer", hint: "Review, approve and export" },
  { value: "admin", label: "Admin", hint: "Everything, plus Agent Studio" },
];

export function readPref(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

export function writePref(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode: preference just isn't remembered */
  }
}

interface Prefs {
  role: Role;
  setRole: (r: Role) => void;
  dark: boolean;
  toggleDark: () => void;
}

const Ctx = createContext<Prefs | null>(null);

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [role, setRoleState] = useState<Role>(() => {
    const r = readPref("lf-role", "reviewer");
    return r === "submitter" || r === "admin" ? r : "reviewer";
  });
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
  }, [dark]);

  const setRole = useCallback((r: Role) => {
    setRoleState(r);
    writePref("lf-role", r);
  }, []);
  const toggleDark = useCallback(() => {
    setDark((d) => {
      writePref("lf-theme", d ? "light" : "dark");
      return !d;
    });
  }, []);

  const value = useMemo(() => ({ role, setRole, dark, toggleDark }), [role, setRole, dark, toggleDark]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function usePrefs(): Prefs {
  const v = useContext(Ctx);
  if (!v) throw new Error("usePrefs outside PrefsProvider");
  return v;
}

export function roleLabel(r: Role): string {
  return ROLES.find((x) => x.value === r)?.label ?? r;
}
