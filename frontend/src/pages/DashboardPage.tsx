import { useQuery } from "@tanstack/react-query";
import { CircleDollarSign, Clock, Cpu, Database, Gauge, Hourglass, Inbox, Layers, PlayCircle, ShieldAlert } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, unwrap, type Dashboard } from "@/api/client";
import { caseCols } from "@/components/caseColumns";
import { DataTable } from "@/components/DataTable";
import { ErrorNote, PageHeader } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge, Segmented, Skeleton } from "@/components/ui/primitives";
import { Tip } from "@/components/ui/overlays";
import { useConfig } from "@/lib/config";
import { compact, count, duration, money, pct, shortDate, usd } from "@/lib/format";
import { readPref, usePrefs, writePref } from "@/lib/prefs";
import { chartColor, statusDef, toneStyle } from "@/lib/status";
import { cn } from "@/lib/utils";

function useChartTheme() {
  const { dark } = usePrefs();
  return dark
    ? { grid: "#232a37", axis: "#8b93a5", line: "#818cf8", fillTop: 0.32, surface: "#151a24" }
    : { grid: "#eceef2", axis: "#6b7280", line: "#4f46e5", fillTop: 0.2, surface: "#ffffff" };
}

function Kpi({ icon: Icon, label, value, sub, hint, loading, tone }: { icon: typeof Inbox; label: string; value: ReactNode; sub?: ReactNode; hint: string; loading: boolean; tone?: Parameters<typeof toneStyle>[0] }) {
  return (
    <Card className="relative px-4 py-3.5">
      <Tip content={hint}>
        <div className="flex cursor-help items-center gap-1.5 whitespace-nowrap text-xs font-medium text-muted-foreground">
          <Icon className={cn("size-3.5 shrink-0 text-muted-foreground/60", tone && toneStyle(tone).text)} aria-hidden />
          <span className="truncate">{label}</span>
        </div>
      </Tip>
      {loading ? <Skeleton className="mt-2.5 h-7 w-20" /> : <div className="mt-1.5 text-2xl font-semibold tracking-tight tabular">{value}</div>}
      <div className="mt-0.5 min-h-4 truncate whitespace-nowrap text-xs text-muted-foreground">{loading ? <Skeleton className="mt-1 h-3 w-24" /> : sub}</div>
    </Card>
  );
}

interface TipPayload {
  name?: string | number;
  value?: number | string;
  color?: string;
  payload?: Record<string, unknown>;
}

function ChartTooltip({ active, payload, label, format }: { active?: boolean; payload?: TipPayload[]; label?: string | number; format?: (v: number, p: TipPayload) => string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border bg-popover px-3 py-2 text-xs shadow-md">
      {label !== undefined && <div className="mb-1 font-medium">{label}</div>}
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2">
          <span className="size-2 rounded-full" style={{ background: p.color }} />
          <span className="text-muted-foreground">{p.name}</span>
          <span className="ml-auto pl-3 font-medium tabular">{format ? format(Number(p.value), p) : count(Number(p.value))}</span>
        </div>
      ))}
    </div>
  );
}

function CasesOverTime({ data }: { data: Dashboard }) {
  const t = useChartTheme();
  const series = data.series.map((p) => ({ ...p, label: shortDate(String(p.date)) }));
  return (
    <ResponsiveContainer width="100%" height={230}>
      <AreaChart data={series} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
        <defs>
          <linearGradient id="casesFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={t.line} stopOpacity={t.fillTop} />
            <stop offset="100%" stopColor={t.line} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid vertical={false} stroke={t.grid} />
        <XAxis dataKey="label" tickLine={false} axisLine={false} tick={{ fill: t.axis, fontSize: 11 }} minTickGap={24} />
        <YAxis allowDecimals={false} tickLine={false} axisLine={false} tick={{ fill: t.axis, fontSize: 11 }} width={44} />
        <Tooltip content={<ChartTooltip />} cursor={{ stroke: t.axis, strokeDasharray: "3 3" }} />
        <Area type="monotone" dataKey="cases" name="Cases" stroke={t.line} strokeWidth={2} fill="url(#casesFill)" activeDot={{ r: 4, strokeWidth: 2, stroke: t.surface }} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

function ExceptionsByRule({ data }: { data: Dashboard }) {
  const t = useChartTheme();
  const rows = data.exceptions_by_rule.slice(0, 8).map((r) => ({ ...r, amountNum: Number(r.amount) }));
  if (!rows.length) return <div className="flex h-[230px] items-center justify-center text-sm text-muted-foreground">No rule failures in this range.</div>;
  return (
    <div>
      <ResponsiveContainer width="100%" height={Math.max(rows.length * 30 + 10, 120)}>
        <BarChart data={rows} layout="vertical" margin={{ top: 0, right: 28, left: 0, bottom: 0 }} barCategoryGap={6}>
          <XAxis type="number" hide allowDecimals={false} />
          <YAxis type="category" dataKey="name" width={170} tickLine={false} axisLine={false} tick={{ fill: t.axis, fontSize: 11.5 }} />
          <Tooltip
            cursor={{ fill: t.grid, opacity: 0.6 }}
            content={<ChartTooltip format={(v, p) => `${count(v)} case(s) · ${money(String(p.payload?.amountNum ?? 0), data.currency, { compact: true })}`} />}
          />
          <Bar dataKey="cases" name="Cases" radius={[0, 4, 4, 0]} barSize={16} label={{ position: "right", fill: t.axis, fontSize: 11 }}>
            {rows.map((r) => (
              <Cell key={r.rule_id} fill={chartColor("severity", r.severity ?? "info")} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-muted-foreground">
        {Array.from(new Set(rows.map((r) => r.severity ?? "info"))).map((s) => (
          <span key={s} className="inline-flex items-center gap-1.5">
            <span className="size-2 rounded-sm" style={{ background: chartColor("severity", s) }} /> {statusDef("severity", s).label} severity
          </span>
        ))}
      </div>
    </div>
  );
}

function DecisionMix({ data }: { data: Dashboard }) {
  const t = useChartTheme();
  const total = data.decision_mix.reduce((a, d) => a + d.cases, 0);
  const rows = data.decision_mix.filter((d) => d.cases > 0);
  if (!total) return <div className="flex h-[230px] items-center justify-center text-sm text-muted-foreground">No decisions yet.</div>;
  return (
    <div className="flex w-full items-center gap-4">
      <div className="relative size-[132px] shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={rows} dataKey="cases" nameKey="decision" innerRadius={44} outerRadius={64} paddingAngle={rows.length > 1 ? 2 : 0} stroke={t.surface} strokeWidth={2}>
              {rows.map((d) => (
                <Cell key={d.decision} fill={chartColor("decision", d.decision)} />
              ))}
            </Pie>
            <Tooltip content={<ChartTooltip format={(v) => `${count(v)} · ${pct((v / total) * 100)}`} />} />
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <div className="text-xl font-semibold tabular">{count(total)}</div>
          <div className="text-[11px] text-muted-foreground">decided</div>
        </div>
      </div>
      <ul className="min-w-0 flex-1 space-y-2">
        {data.decision_mix.map((d) => (
          <li key={d.decision} className="flex items-center gap-2 text-[13px]">
            <span className="size-2.5 shrink-0 rounded-sm" style={{ background: chartColor("decision", d.decision) }} />
            <span className="truncate">{statusDef("decision", d.decision).label}</span>
            <span className="ml-auto tabular font-medium">{count(d.cases)}</span>
            <span className="w-8 text-right text-xs tabular text-muted-foreground">{pct((d.cases / total) * 100)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function DashboardPage() {
  const { data: cfg } = useConfig();
  const navigate = useNavigate();
  const ranges = cfg?.app.ui.dashboard.ranges ?? [];
  const [range, setRange] = useState(() => readPref("lf-dash-range", ""));
  const effective = ranges.includes(range) ? range : cfg?.app.ui.dashboard.default_range ?? "";
  const q = useQuery({
    queryKey: ["dashboard", effective],
    queryFn: async () => unwrap(await api.GET("/api/dashboard/metrics", { params: { query: { range: effective } } })),
    enabled: !!cfg,
    refetchInterval: 20_000,
    placeholderData: (prev) => prev,
  });
  const d = q.data;
  const k = d?.kpis;
  const loading = !d;
  const currency = d?.currency ?? cfg?.rules.currency ?? "USD";

  return (
    <div>
      <PageHeader
        title="Dashboard"
        description={`Every invoice decided by rules, explained by agents.${effective ? ` Last ${effective.replace("d", " days")}.` : ""}`}
        actions={
          <>
            {!!d?.seeded_cases && (
              <Tip content="Seeded demo history (make seed) so the charts have shape on first open. Live runs are added on top.">
                <Badge className="gap-1.5">
                  <Database className="size-3" /> {d.seeded_cases} seeded demo cases
                </Badge>
              </Tip>
            )}
            <Segmented
              label="Date range"
              value={effective}
              options={ranges.map((r) => ({ value: r, label: r.toUpperCase() }))}
              onChange={(r) => {
                setRange(r);
                writePref("lf-dash-range", r);
              }}
            />
            <Button size="sm" asChild>
              <Link to="/scenarios">
                <PlayCircle /> Run a scenario
              </Link>
            </Button>
          </>
        }
      />
      {q.error && <ErrorNote error={q.error} />}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
        <Kpi icon={Layers} label="Cases" loading={loading} value={count(k?.cases)} sub={k && `${count(k.processing)} processing now`} hint="Invoices received in the range (bundle parents excluded)." />
        <Kpi
          icon={Gauge}
          label="Straight-through"
          loading={loading}
          tone="emerald"
          value={k?.straight_through_pct === null || k?.straight_through_pct === undefined ? "—" : pct(k.straight_through_pct, 0)}
          sub={k && `${count(k.decided)} decided`}
          hint="Auto-approved and closed without a human touching the case."
        />
        <Kpi icon={Inbox} label="In review" loading={loading} tone="amber" value={count(k?.in_review)} sub="awaiting a reviewer" hint="Cases waiting in the work queue right now." />
        <Kpi icon={Hourglass} label="On hold" loading={loading} tone="sky" value={count(k?.on_hold)} sub="awaiting documents" hint="Cases paused for documents or vendor onboarding right now." />
        <Kpi
          icon={Clock}
          label="To decision"
          loading={loading}
          value={k?.median_time_to_decision_s === null || k?.median_time_to_decision_s === undefined ? "—" : duration(k.median_time_to_decision_s * 1000)}
          sub="median from upload"
          hint="Median time from upload to the decision stage completing."
        />
        <Kpi
          icon={ShieldAlert}
          label="Exceptions"
          loading={loading}
          tone="rose"
          value={money(k?.exceptions_caught_amount, currency, { compact: true })}
          sub={k && `${count(k.exceptions_caught_cases)} invoices stopped`}
          hint="Total value of invoices that rules sent to review, hold or reject instead of paying."
        />
        <Kpi
          icon={Cpu}
          label="LLM / invoice"
          loading={loading}
          value={k?.llm_cost_per_invoice_usd === null || k?.llm_cost_per_invoice_usd === undefined || (k.llm_cost_total_usd === 0 && k.llm_tokens > 0) ? "—" : usd(k.llm_cost_per_invoice_usd)}
          sub={k && (k.llm_cost_total_usd === 0 && k.llm_tokens > 0 ? "pricing not set" : `${compact(k.llm_tokens)} tokens`)}
          hint="Model spend divided by cases (pricing from app.yaml)."
        />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <div>
              <CardTitle>Cases over time</CardTitle>
              <CardDescription>Invoices received per day</CardDescription>
            </div>
          </CardHeader>
          <CardContent>{d ? <CasesOverTime data={d} /> : <Skeleton className="h-[230px]" />}</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Decision mix</CardTitle>
              <CardDescription>What the rules decided</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="flex min-h-[230px] items-center">{d ? <DecisionMix data={d} /> : <Skeleton className="h-[150px]" />}</CardContent>
        </Card>
      </div>

      <div className="mt-4 grid items-start gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Exceptions by rule</CardTitle>
              <CardDescription>Cases each rule stopped (latest check per case)</CardDescription>
            </div>
            <CircleDollarSign className="size-4 text-muted-foreground/70" />
          </CardHeader>
          <CardContent>{d ? <ExceptionsByRule data={d} /> : <Skeleton className="h-[230px]" />}</CardContent>
        </Card>
        <div className="lg:col-span-2">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold">Recent cases</h2>
            <Button variant="link" size="xs" asChild className="h-auto px-0">
              <Link to="/cases">View all</Link>
            </Button>
          </div>
          <DataTable
            id="dashboard-recent"
            data={d?.recent}
            loading={loading}
            searchable={false}
            maxHeight="none"
            columns={[caseCols.refCompact, caseCols.vendor, caseCols.total, caseCols.decision, caseCols.status, caseCols.created]}
            onRowClick={(c) => navigate(`/cases/${c.id}/live`)}
            empty="No cases yet. Run a scenario to see one flow through."
          />
        </div>
      </div>
    </div>
  );
}
