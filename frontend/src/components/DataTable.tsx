/** Dense TanStack table (SPEC §10): sticky header, sorting, global + per-column filters, saved views. */
import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type ColumnFiltersState,
  type SortingState,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, Bookmark, Filter, Search, X } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Input, Skeleton } from "@/components/ui/primitives";
import { DropdownMenu, DropdownMenuContent, DropdownMenuLabel, DropdownMenuTrigger } from "@/components/ui/overlays";
import { readPref, writePref } from "@/lib/prefs";
import { cn } from "@/lib/utils";

interface View {
  name: string;
  global: string;
  filters: ColumnFiltersState;
  sorting: SortingState;
}

function loadViews(id: string): View[] {
  try {
    const v: unknown = JSON.parse(readPref(`lf-views-${id}`, "[]"));
    return Array.isArray(v) ? (v as View[]) : [];
  } catch {
    return [];
  }
}

export function DataTable<T>({
  id,
  data,
  columns,
  loading,
  onRowClick,
  empty,
  toolbar,
  maxHeight = "calc(100vh - 260px)",
  initialSorting = [],
  dense = true,
  searchable = true,
}: {
  id: string;
  data: T[] | undefined;
  columns: ColumnDef<T, unknown>[];
  loading?: boolean;
  onRowClick?: (row: T) => void;
  empty?: ReactNode;
  toolbar?: ReactNode;
  maxHeight?: string;
  initialSorting?: SortingState;
  dense?: boolean;
  searchable?: boolean;
}) {
  const [sorting, setSorting] = useState<SortingState>(initialSorting);
  const [filters, setFilters] = useState<ColumnFiltersState>([]);
  const [global, setGlobal] = useState("");
  const [showFilters, setShowFilters] = useState(false);
  const [views, setViews] = useState<View[]>(() => loadViews(id));
  const rows = useMemo(() => data ?? [], [data]);

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting, columnFilters: filters, globalFilter: global },
    onSortingChange: setSorting,
    onColumnFiltersChange: setFilters,
    onGlobalFilterChange: setGlobal,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  const saveView = () => {
    const name = window.prompt("Name this view");
    if (!name) return;
    const next = [...views.filter((v) => v.name !== name), { name, global, filters, sorting }];
    setViews(next);
    writePref(`lf-views-${id}`, JSON.stringify(next));
  };
  const removeView = (name: string) => {
    const next = views.filter((v) => v.name !== name);
    setViews(next);
    writePref(`lf-views-${id}`, JSON.stringify(next));
  };
  const applyView = (v: View) => {
    setGlobal(v.global);
    setFilters(v.filters);
    setSorting(v.sorting);
    if (v.filters.length) setShowFilters(true);
  };
  const active = filters.length > 0 || global !== "";
  const pad = dense ? "py-2" : "py-3";

  return (
    <div className="overflow-hidden rounded-lg border bg-card">
      {searchable && (
        <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2">
          <div className="relative w-full max-w-xs">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input value={global} onChange={(e) => setGlobal(e.target.value)} placeholder="Search…" className="h-8 pl-8 text-[13px]" aria-label="Search rows" />
          </div>
          <Button variant={showFilters ? "secondary" : "ghost"} size="sm" onClick={() => setShowFilters((s) => !s)} aria-pressed={showFilters}>
            <Filter /> Filters{filters.length ? ` (${filters.length})` : ""}
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm">
                <Bookmark /> Views
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-56">
              <DropdownMenuLabel>Saved views</DropdownMenuLabel>
              {views.length === 0 && <div className="px-2 pb-2 text-xs text-muted-foreground">None yet. Filter the table, then save it.</div>}
              {views.map((v) => (
                <div key={v.name} className="flex items-center justify-between rounded-md px-2 py-1 text-sm hover:bg-muted">
                  <button className="flex-1 text-left" onClick={() => applyView(v)}>
                    {v.name}
                  </button>
                  <button className="text-muted-foreground hover:text-foreground" onClick={() => removeView(v.name)} aria-label={`Delete view ${v.name}`}>
                    <X className="size-3.5" />
                  </button>
                </div>
              ))}
              <div className="mt-1 border-t pt-1">
                <button className="w-full rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted disabled:opacity-50" onClick={saveView} disabled={!active && sorting.length === 0}>
                  Save current view…
                </button>
              </div>
            </DropdownMenuContent>
          </DropdownMenu>
          {active && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setGlobal("");
                setFilters([]);
              }}
            >
              <X /> Clear
            </Button>
          )}
          <div className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
            {toolbar}
            <span className="tabular">{table.getFilteredRowModel().rows.length} rows</span>
          </div>
        </div>
      )}
      <div className="overflow-auto" style={{ maxHeight }}>
        <table className="w-full border-separate border-spacing-0 text-[13px]">
          <thead>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => {
                  const meta = h.column.columnDef.meta as { align?: "right" } | undefined;
                  const sorted = h.column.getIsSorted();
                  return (
                    <th
                      key={h.id}
                      className={cn(
                        "sticky top-0 z-10 border-b bg-muted/80 px-3 py-2 text-left text-xs font-medium text-muted-foreground backdrop-blur",
                        meta?.align === "right" && "text-right",
                      )}
                      style={{ width: h.getSize() !== 150 ? h.getSize() : undefined }}
                    >
                      {h.isPlaceholder ? null : (
                        <button
                          className={cn("inline-flex items-center gap-1 hover:text-foreground", !h.column.getCanSort() && "pointer-events-none")}
                          onClick={h.column.getToggleSortingHandler()}
                        >
                          {flexRender(h.column.columnDef.header, h.getContext())}
                          {sorted === "asc" && <ArrowUp className="size-3" />}
                          {sorted === "desc" && <ArrowDown className="size-3" />}
                        </button>
                      )}
                      {showFilters && h.column.getCanFilter() && (
                        <Input
                          value={(h.column.getFilterValue() as string | undefined) ?? ""}
                          onChange={(e) => h.column.setFilterValue(e.target.value || undefined)}
                          placeholder="Filter"
                          className="mt-1.5 h-7 px-2 text-xs font-normal"
                          aria-label={`Filter ${String(h.column.id)}`}
                        />
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {loading &&
              Array.from({ length: 6 }).map((_, i) => (
                <tr key={`sk-${i}`}>
                  {columns.map((_c, j) => (
                    <td key={j} className={cn("border-b px-3", pad)}>
                      <Skeleton className="h-3.5" style={{ width: `${50 + ((i * 7 + j * 13) % 45)}%` }} />
                    </td>
                  ))}
                </tr>
              ))}
            {!loading &&
              table.getRowModel().rows.map((row) => (
                <tr
                  key={row.id}
                  onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                  className={cn("group", onRowClick && "cursor-pointer hover:bg-muted/50")}
                >
                  {row.getVisibleCells().map((cell) => {
                    const meta = cell.column.columnDef.meta as { align?: "right" } | undefined;
                    return (
                      <td key={cell.id} className={cn("border-b px-3 align-middle", pad, meta?.align === "right" && "text-right tabular")}>
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    );
                  })}
                </tr>
              ))}
          </tbody>
        </table>
        {!loading && table.getRowModel().rows.length === 0 && (
          <div className="px-4 py-10 text-center text-sm text-muted-foreground">{empty ?? "Nothing here yet."}</div>
        )}
      </div>
    </div>
  );
}
