/**
 * Live-run chips from app.yaml `ui.stage_chips`: templates like "{po.po_number} · remaining {po.remaining|money}".
 * A chip is dropped when any placeholder resolves to nothing, so templates can list optional facts.
 */
import { count, money, pct } from "./format";

type Json = unknown;

function get(obj: Json, path: string): Json {
  let cur: Json = obj;
  for (const key of path.split(".")) {
    if (cur === null || cur === undefined) return undefined;
    if (Array.isArray(cur)) {
      const i = Number(key);
      cur = Number.isInteger(i) ? cur[i] : undefined;
    } else if (typeof cur === "object") {
      cur = (cur as Record<string, Json>)[key];
    } else {
      return undefined;
    }
  }
  return cur;
}

function fmt(value: Json, format: string | undefined, currency: string): string | null {
  if (value === null || value === undefined || value === "") return null;
  switch (format) {
    case "count":
      if (Array.isArray(value)) return String(value.length);
      if (value && typeof value === "object") return String(Object.keys(value).length);
      return count(value as string | number);
    case "money":
      return money(value as string | number, currency);
    case "pct": {
      const n = Number(value);
      return Number.isFinite(n) ? pct(n <= 1 ? n * 100 : n) : null;
    }
    default:
      if (typeof value === "object") return null;
      return String(value).replace(/_/g, " ");
  }
}

export function renderChips(templates: string[] | undefined, output: Json, currency = "USD"): string[] {
  if (!templates || !output) return [];
  const chips: string[] = [];
  for (const t of templates) {
    let ok = true;
    const text = t.replace(/\{([^}|]+)(?:\|(\w+))?\}/g, (_m, path: string, format: string | undefined) => {
      const v = fmt(get(output, path.trim()), format, currency);
      if (v === null) ok = false;
      return v ?? "";
    });
    if (ok && text.trim()) chips.push(text);
  }
  return chips;
}
