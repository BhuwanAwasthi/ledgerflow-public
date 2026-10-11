/** Display helpers. Money arrives as decimal strings (exact); it is only formatted here, never computed on. */

type Num = string | number | null | undefined;

function toNumber(v: Num): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

export function money(v: Num, currency = "USD", opts: { compact?: boolean } = {}): string {
  const n = toNumber(v);
  if (n === null) return "—";
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      notation: opts.compact ? "compact" : "standard",
      maximumFractionDigits: opts.compact ? 1 : 2,
      minimumFractionDigits: opts.compact ? 0 : 2,
    }).format(n);
  } catch {
    return `${n.toFixed(2)} ${currency}`;
  }
}

export function plainMoney(v: Num): string {
  const n = toNumber(v);
  return n === null ? "—" : n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function usd(v: Num, digits = 4): string {
  const n = toNumber(v);
  if (n === null) return "—";
  return `$${n.toFixed(n !== 0 && Math.abs(n) < 0.01 ? digits : 2)}`;
}

export function pct(v: Num, digits = 0): string {
  const n = toNumber(v);
  return n === null ? "—" : `${n.toFixed(digits)}%`;
}

export function count(v: Num): string {
  const n = toNumber(v);
  return n === null ? "—" : n.toLocaleString("en-US");
}

export function compact(v: Num): string {
  const n = toNumber(v);
  return n === null ? "—" : new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

export function duration(ms: Num): string {
  const n = toNumber(ms);
  if (n === null) return "—";
  if (n < 1000) return `${Math.round(n)} ms`;
  const s = n / 1000;
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${Math.round(s % 60)}s`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ${m % 60}m`;
  return `${Math.round(h / 24)}d`;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function timeOfDay(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function relative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  const future = diff < 0;
  const s = Math.abs(diff) / 1000;
  const unit =
    s < 60 ? `${Math.round(s)}s` : s < 3600 ? `${Math.round(s / 60)}m` : s < 86400 ? `${Math.round(s / 3600)}h` : `${Math.round(s / 86400)}d`;
  return future ? `in ${unit}` : `${unit} ago`;
}

export function humanise(v: string | null | undefined): string {
  if (!v) return "—";
  const s = v.replace(/[_.]/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}
