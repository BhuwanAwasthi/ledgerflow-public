import { useQuery } from "@tanstack/react-query";
import { ClipboardList, Copy, ExternalLink, Radio, Upload } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { api, unwrap, type Case } from "@/api/client";
import { EmptyState, ErrorNote, PageHeader, StatusBadge } from "@/components/common";
import { JourneyTracker } from "@/components/JourneyTracker";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Skeleton } from "@/components/ui/primitives";
import { dateTime, money } from "@/lib/format";
import { readPref, writePref } from "@/lib/prefs";
import { ACTIVE_CASE_STATUSES, statusDef } from "@/lib/status";
import { cn } from "@/lib/utils";

function SubmissionCard({ c, highlight }: { c: Case; highlight: boolean }) {
  const active = ACTIVE_CASE_STATUSES.has(c.status);
  const j = useQuery({
    queryKey: ["journey", c.id, c.status],
    queryFn: async () => unwrap(await api.GET("/api/cases/{case_id}/journey", { params: { path: { case_id: c.id } } })),
    refetchInterval: active ? 3000 : false,
  });
  const tone = statusDef("case", j.data?.status ?? c.status).style;
  return (
    <Card className={cn("overflow-hidden", highlight && "ring-2 ring-primary/40")}>
      <div className="flex flex-wrap items-start gap-x-6 gap-y-2 px-5 pt-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2.5">
            <span className="text-sm font-semibold tabular">{c.ref}</span>
            <StatusBadge kind="case" value={j.data?.status ?? c.status} pulse={active} />
          </div>
          <div className="mt-1 truncate text-[13px] text-muted-foreground">
            {[c.vendor_name, c.invoice_number && `invoice ${c.invoice_number}`, c.total && money(c.total, c.currency ?? "USD"), `sent ${dateTime(c.created_at)}`]
              .filter(Boolean)
              .join(" · ")}
          </div>
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" size="sm" asChild>
            <Link to={`/cases/${c.id}/live`}>
              <Radio /> Live view
            </Link>
          </Button>
        </div>
      </div>
      <div className={cn("mx-5 mt-3 rounded-md px-3 py-2 text-[13px] font-medium", tone.soft)}>
        {j.data ? j.data.headline : <Skeleton className="h-4 w-1/2" />}
      </div>
      <div className="px-5 pb-5 pt-5">{j.data ? <JourneyTracker journey={j.data} /> : <Skeleton className="h-12" />}</div>
      {j.data?.portal_url && (
        <div className="flex flex-wrap items-center gap-3 border-t bg-muted/30 px-5 py-3 text-[13px]">
          <span className="flex-1 text-muted-foreground">Upload the requested documents through the secure link (no login needed).</span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              void navigator.clipboard.writeText(j.data?.portal_url ?? "").then(() => toast.success("Upload link copied"));
            }}
          >
            <Copy /> Copy link
          </Button>
          <Button size="sm" asChild>
            <a href={j.data.portal_url} target="_blank" rel="noreferrer">
              <ExternalLink /> Open upload page
            </a>
          </Button>
        </div>
      )}
    </Card>
  );
}

export default function MySubmissionsPage() {
  const [params] = useSearchParams();
  const focus = Number(params.get("case")) || null;
  const [email, setEmail] = useState(() => readPref("lf-submitter-email", ""));
  const list = useQuery({
    queryKey: ["submissions", email],
    queryFn: async () => unwrap(await api.GET("/api/cases", { params: { query: { submitter_email: email || undefined, page_size: 25 } } })).items,
    refetchInterval: 10_000,
  });
  const focused = useQuery({
    queryKey: ["case-row", focus],
    queryFn: async () => unwrap(await api.GET("/api/cases/{case_id}", { params: { path: { case_id: focus ?? 0 } } })),
    enabled: focus !== null,
  });
  const rows = (list.data ?? []).filter((c) => c.status !== "split" && c.id !== focus);
  const all: Case[] = [...(focused.data ? [focused.data] : []), ...rows];

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        title="My submissions"
        description={email ? `Where each invoice from ${email} is, in plain English.` : "Demo: showing every submitter. Enter your email to see only yours."}
        actions={
          <>
            <Input
              type="email"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
                writePref("lf-submitter-email", e.target.value);
              }}
              placeholder="Filter by your email"
              className="h-8 w-64 text-[13px]"
              aria-label="Your email"
            />
            <Button size="sm" asChild>
              <Link to="/submit">
                <Upload /> Submit invoices
              </Link>
            </Button>
          </>
        }
      />
      {list.error && <ErrorNote error={list.error} />}
      {list.isLoading && (
        <div className="space-y-4">
          {[0, 1].map((i) => (
            <Skeleton key={i} className="h-44 rounded-lg" />
          ))}
        </div>
      )}
      {!list.isLoading && all.length === 0 && (
        <EmptyState
          icon={ClipboardList}
          title={email ? `Nothing submitted from ${email} yet` : "No submissions yet"}
          action={
            <Button size="sm" asChild>
              <Link to="/submit">Submit an invoice</Link>
            </Button>
          }
        >
          Uploads appear here with a live journey tracker.
        </EmptyState>
      )}
      <div className="space-y-4">
        {all.map((c) => (
          <SubmissionCard key={c.id} c={c} highlight={c.id === focus} />
        ))}
      </div>
    </div>
  );
}
