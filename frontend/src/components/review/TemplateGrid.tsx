import { Download, Pencil, Undo2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { apiUrl, type CellFlag, type GridOp, type TemplateVersion, type Workspace, type WorkspaceColumn } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Tip } from "@/components/ui/overlays";
import { Badge, Input } from "@/components/ui/primitives";
import { plainMoney } from "@/lib/format";
import { toneStyle } from "@/lib/status";
import { cn } from "@/lib/utils";

/** An edit the reviewer typed but has not applied yet, keyed by the invoice value it changes. */
export type PendingEdits = Record<string, string>;

type Edit = NonNullable<WorkspaceColumn["edit"]>;
type InvoiceLine = Record<string, unknown>;

/** Header fields are one value for every row; line and GL fields belong to a row. */
function editKey(edit: Edit, row: number): string {
  if (edit.op === "set_gl") return `gl:${row}:${edit.field}`;
  return edit.path?.includes("{i}") ? `line:${row}:${edit.field}` : `field:${edit.field}`;
}

function rawValue(ws: Workspace, edit: Edit, row: number, cell: string): string {
  if (edit.op === "set_gl") return cell;
  const inv = ws.invoice as Record<string, unknown>;
  const v = edit.path?.includes("{i}") ? ((inv.lines as InvoiceLine[] | undefined)?.[row] ?? {})[edit.field] : inv[edit.field];
  return v === null || v === undefined ? "" : String(v);
}

function invalid(edit: Edit, value: string): string | null {
  if (value.trim() === "") return null;
  if ((edit.input === "money" || edit.input === "number") && !/^-?\d+(\.\d+)?$/.test(value.replace(/,/g, "").trim())) return "Enter a number, e.g. 1250.00";
  if (edit.input === "date" && !/^\d{4}-\d{2}-\d{2}$/.test(value)) return "Pick a date";
  return null;
}

/** Turn the pending edits into the typed ops the reconciler validates (one set_gl per row). */
export function opsFrom(pending: PendingEdits, ws: Workspace, rows: Record<string, string>[]): GridOp[] {
  const ops: GridOp[] = [];
  const glRows = new Map<number, { gl_code?: string; department?: string }>();
  for (const [key, value] of Object.entries(pending)) {
    const [kind, a, b] = key.split(":");
    if (kind === "field") ops.push({ op: "set_field", path: a, value: value.trim() === "" ? null : value.trim() });
    else if (kind === "line") ops.push({ op: "set_field", path: `lines[${a}].${b}`, value: value.trim() === "" ? null : value.replace(/,/g, "").trim() });
    else glRows.set(Number(a), { ...glRows.get(Number(a)), [b]: value.trim() });
  }
  const glCol = ws.columns.find((c) => c.edit?.op === "set_gl" && c.edit.field === "gl_code");
  const deptCol = ws.columns.find((c) => c.edit?.op === "set_gl" && c.edit.field === "department");
  for (const [row, v] of glRows) {
    const gl = v.gl_code ?? (glCol ? rows[row]?.[glCol.header] : "") ?? "";
    const dept = v.department ?? (deptCol ? rows[row]?.[deptCol.header] : "") ?? "";
    ops.push({ op: "set_gl", line_index: row, gl_code: gl, department: dept || null });
  }
  return ops;
}

function CellEditor({ edit, initial, onDone }: { edit: Edit; initial: string; onDone: (v: string | null) => void }) {
  const [value, setValue] = useState(initial);
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);
  const error = invalid(edit, value);
  const commit = () => onDone(error ? null : value);
  return (
    <div className="relative">
      <Input
        ref={ref}
        type={edit.input === "date" ? "date" : "text"}
        inputMode={edit.input === "money" || edit.input === "number" ? "decimal" : undefined}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") onDone(null);
          e.stopPropagation();
        }}
        className={cn("h-7 min-w-[7rem] rounded px-1.5 text-[13px] tabular", error && "border-destructive focus-visible:ring-destructive/50")}
        aria-invalid={!!error}
        aria-label={`Edit ${edit.field}`}
      />
      {error && <div className="absolute left-0 top-full z-20 mt-1 whitespace-nowrap rounded bg-foreground px-1.5 py-0.5 text-[11px] text-background">{error}</div>}
    </div>
  );
}

export function TemplateGrid({
  caseId,
  ws,
  templates,
  pending,
  onPendingChange,
  activeField,
  onHoverField,
}: {
  caseId: number;
  ws: Workspace;
  templates: TemplateVersion[];
  pending: PendingEdits;
  onPendingChange: (p: PendingEdits) => void;
  activeField: string | null;
  onHoverField: (f: string | null) => void;
}) {
  const latest = templates[templates.length - 1];
  const [versionNo, setVersionNo] = useState<number | null>(null);
  const tv = templates.find((t) => t.version === versionNo) ?? latest;
  const isLatest = tv?.version === latest?.version;
  const editable = ws.reviewable && isLatest;
  const [editing, setEditing] = useState<string | null>(null); // `${row}:${header}`

  const flags = useMemo(() => {
    const m = new Map<string, CellFlag>();
    if (isLatest) ws.cells.forEach((f) => m.set(`${f.row}:${f.column}`, f));
    return m;
  }, [ws.cells, isLatest]);
  const changed = useMemo(() => {
    const m = new Map<string, string>();
    const diff = tv?.diff_from_prev as { changed?: { row: number; column: string; old: string }[] } | null | undefined;
    diff?.changed?.forEach((c) => m.set(`${c.row}:${c.column}`, c.old));
    return m;
  }, [tv]);

  const diffList = useMemo(
    () => ((tv?.diff_from_prev as { changed?: { row: number; column: string; old: string; new: string }[] } | null | undefined)?.changed ?? []),
    [tv],
  );
  // ERP layouts carry columns that are empty on every row (e.g. a DONOTIMPORT marker): hidden unless asked for
  const [showEmpty, setShowEmpty] = useState(false);
  const columns = useMemo(() => {
    const rws = (tv?.rows ?? []) as Record<string, string>[];
    const pendingCols = new Set(Object.keys(pending).length ? ws.columns.filter((c) => c.edit).map((c) => c.header) : []);
    return ws.columns.map((c) => ({
      col: c,
      empty:
        rws.every((r) => (r[c.header] ?? "") === "") &&
        !c.required &&
        !ws.cells.some((f) => f.column === c.header) &&
        !diffList.some((d) => d.column === c.header) &&
        !pendingCols.has(c.header),
    }));
  }, [tv, ws.columns, ws.cells, diffList, pending]);
  const visible = columns.filter((x) => showEmpty || !x.empty).map((x) => x.col);
  const hiddenCount = columns.filter((x) => x.empty).length;

  // bring the first changed or flagged cell into view when a version opens
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = scroller.current?.querySelector<HTMLElement>("td[data-hl='diff']") ?? scroller.current?.querySelector<HTMLElement>("td[data-hl='flag']");
    const box = scroller.current;
    if (!el || !box) return;
    // nearest: scroll only as far as needed to show the cell, keeping the identifying columns in view when possible
    const right = el.offsetLeft + el.offsetWidth + 48 - box.clientWidth;
    box.scrollLeft = right > 0 ? right : 0;
  }, [tv?.version, showEmpty, ws.cells]);
  // unmounting while hovered (tab switch) never fires mouseleave: clear the PDF link
  useEffect(() => () => onHoverField(null), [onHoverField]);

  if (!tv) {
    return <div className="px-5 py-10 text-center text-sm text-muted-foreground">No ERP template yet: it is drafted after the decision stage.</div>;
  }
  const rows = tv.rows as Record<string, string>[];
  const amber = toneStyle("amber");
  const indigo = toneStyle("indigo");
  const pendingCount = Object.keys(pending).length;

  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 px-4 py-2.5 text-xs">
        <div className="inline-flex rounded-md border bg-muted/60 p-0.5" role="radiogroup" aria-label="Template version">
          {templates.map((t) => (
            <button
              key={t.version}
              role="radio"
              aria-checked={t.version === tv.version}
              onClick={() => setVersionNo(t.version)}
              className={cn("h-6 rounded-[5px] px-2 text-xs font-medium text-muted-foreground hover:text-foreground", t.version === tv.version && "bg-surface text-foreground shadow-sm")}
            >
              v{t.version}
            </button>
          ))}
        </div>
        <span className="text-muted-foreground">
          {ws.erp_profile} · {rows.length} row{rows.length === 1 ? "" : "s"} · {tv.status === "final" ? "final" : "draft"} · by {tv.created_by}
        </span>
        <div className="ml-auto flex items-center gap-3 text-muted-foreground">
          {flags.size > 0 && <Legend className={cn(amber.soft, amber.ring)} label="Check this" />}
          {changed.size > 0 && <Legend className={cn(indigo.soft, indigo.ring)} label={`Changed since v${tv.version - 1}`} />}
          {editable && (
            <span className="inline-flex items-center gap-1">
              <Pencil className="size-3" /> Click a cell to edit
            </span>
          )}
          {hiddenCount > 0 && (
            <button className="font-medium text-primary hover:underline" onClick={() => setShowEmpty((v) => !v)}>
              {showEmpty ? "Hide" : "Show"} {hiddenCount} empty column{hiddenCount === 1 ? "" : "s"}
            </button>
          )}
          <Button variant="ghost" size="xs" asChild>
            <a href={apiUrl(`/api/cases/${caseId}/template/${tv.version}/download`)}>
              <Download /> v{tv.version}
            </a>
          </Button>
        </div>
      </div>

      {diffList.length > 0 && (
        <div className={cn("mx-4 mb-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md px-3 py-2 text-xs", indigo.soft, indigo.ring)}>
          <span className="font-medium">
            v{tv.version} changed {diffList.length} cell{diffList.length === 1 ? "" : "s"}:
          </span>
          {diffList.map((d) => (
            <span key={`${d.row}:${d.column}`} className="tabular">
              row {d.row + 1} <span className="font-mono text-[11px]">{d.column}</span> <span className="text-muted-foreground line-through">{d.old || "empty"}</span> → <span className={cn("font-semibold", indigo.text)}>{d.new || "empty"}</span>
            </span>
          ))}
        </div>
      )}
      <div className="relative min-h-0">
      <div className="pointer-events-none absolute inset-y-0 right-0 z-20 w-8 bg-gradient-to-l from-card" aria-hidden />
      {/* positioned so it is the cells' offsetParent: offsetLeft is then independent of the scroll position */}
      <div ref={scroller} className="relative min-h-0 overflow-auto border-y">
        <table className="w-max min-w-full border-separate border-spacing-0 text-[13px]">
          <thead className="sticky top-0 z-10 bg-muted/90 backdrop-blur">
            <tr>
              <th className="sticky left-0 z-20 w-8 border-b border-r bg-muted shadow-[4px_0_6px_-4px_rgb(0_0_0/0.15)] px-2 py-2 text-right text-[11px] font-medium text-muted-foreground">#</th>
              {visible.map((c) => (
                <th key={c.header} className="whitespace-nowrap border-b px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {c.header}
                  {c.required && <span className="ml-0.5 text-destructive" aria-label="required">*</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, r) => (
              <tr key={r} className="group">
                <td className="sticky left-0 z-[1] border-b border-r bg-card shadow-[4px_0_6px_-4px_rgb(0_0_0/0.15)] px-2 py-1.5 text-right text-[11px] tabular text-muted-foreground group-hover:bg-muted">{r + 1}</td>
                {visible.map((c) => {
                  const key = `${r}:${c.header}`;
                  const flag = flags.get(key);
                  const was = changed.get(key);
                  const edit = c.edit && (c.edit.op === "set_gl" || !c.edit.path?.includes("{i}") || ws.grain === "line") ? c.edit : null;
                  const pKey = edit ? editKey(edit, r) : null;
                  const pendingValue = pKey ? pending[pKey] : undefined;
                  const value = pendingValue ?? row[c.header] ?? "";
                  const field = c.binds.find((b) => !b.startsWith("lines.") && b !== "po" && b !== "vendor") ?? null;
                  const linked = !!activeField && c.binds.includes(activeField);
                  const missing = c.required && value === "";
                  // money cells read like money; the editor and the export keep the raw import value
                  const show = (v: string) => (c.edit?.input === "money" && /^-?\d+(\.\d+)?$/.test(v) ? plainMoney(v) : v);
                  const tip = [
                    ...(flag?.reasons ?? []),
                    was !== undefined ? `Was "${was || "empty"}" in v${tv.version - 1}` : null,
                    pendingValue !== undefined ? `Edited: was "${row[c.header] ?? ""}" (not applied yet)` : null,
                    missing ? "Required by the ERP import" : null,
                  ].filter(Boolean);
                  const cell = (
                    <td
                      key={c.header}
                      data-hl={was !== undefined ? "diff" : flag ? "flag" : undefined}
                      onMouseEnter={() => onHoverField(field)}
                      onMouseLeave={() => onHoverField(null)}
                      onClick={() => edit && editable && setEditing(key)}
                      className={cn(
                        "relative whitespace-nowrap border-b px-3 py-1.5 transition-colors group-hover:bg-muted/40",
                        c.edit?.input === "money" && "text-right tabular",
                        (flag || missing) && cn(amber.soft, amber.ring),
                        was !== undefined && cn(indigo.soft, indigo.ring),
                        pendingValue !== undefined && "font-semibold text-primary shadow-[inset_0_0_0_1px_hsl(var(--primary)/0.6)]",
                        linked && "shadow-[inset_0_0_0_2px_hsl(var(--ring))]",
                        edit && editable && "cursor-text hover:shadow-[inset_0_0_0_1px_hsl(var(--ring)/0.5)]",
                      )}
                    >
                      {editing === key && edit ? (
                        <CellEditor
                          edit={edit}
                          initial={pendingValue ?? rawValue(ws, edit, r, row[c.header] ?? "")}
                          onDone={(v) => {
                            setEditing(null);
                            if (v === null || !pKey) return;
                            const original = rawValue(ws, edit, r, row[c.header] ?? "");
                            const next = { ...pending };
                            if (v === original) delete next[pKey];
                            else next[pKey] = v;
                            onPendingChange(next);
                          }}
                        />
                      ) : (
                        <span className="tabular">
                          {value ? show(value) : <span className="text-muted-foreground/60">—</span>}
                          {was !== undefined && <span className="ml-1.5 text-[11px] font-normal text-muted-foreground line-through decoration-muted-foreground/60">{was ? show(was) : "empty"}</span>}
                        </span>
                      )}
                    </td>
                  );
                  return tip.length && editing !== key ? (
                    <Tip key={c.header} content={tip.join(" · ")}>
                      {cell}
                    </Tip>
                  ) : (
                    cell
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      </div>
      {!isLatest && (
        <div className="px-4 py-2 text-xs text-muted-foreground">
          Viewing v{tv.version}. <button className="font-medium text-primary hover:underline" onClick={() => setVersionNo(null)}>Back to v{latest.version}</button> to edit.
        </div>
      )}
      {pendingCount > 0 && (
        <div className="flex items-center gap-2 px-4 py-2 text-xs">
          <Badge className="border-primary/30 bg-accent text-accent-foreground ring-primary/30">
            {pendingCount} unapplied edit{pendingCount === 1 ? "" : "s"}
          </Badge>
          <Button variant="ghost" size="xs" onClick={() => onPendingChange({})}>
            <Undo2 /> Discard
          </Button>
        </div>
      )}
    </div>
  );
}

function Legend({ className, label }: { className: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={cn("size-3 rounded-[3px]", className)} aria-hidden />
      {label}
    </span>
  );
}
