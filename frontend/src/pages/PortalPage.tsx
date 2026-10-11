/** Public vendor portal (/portal/:token): no login, no app shell. The token in the link is the request. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ChevronRight, CircleDashed, FileText, Loader2, Lock, Moon, Sun, Trash2, UploadCloud } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import { useParams } from "react-router-dom";
import { toast } from "sonner";
import { api, ApiError, unwrap, type Portal } from "@/api/client";
import { StatusBadge } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/primitives";
import { useConfig } from "@/lib/config";
import { usePrefs } from "@/lib/prefs";
import { statusDef, toneStyle } from "@/lib/status";
import { cn } from "@/lib/utils";

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.docx"; // browser hint only: the server decides what is usable

function size(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** What the vendor sees for a check: the outcome, never our matching thresholds (those stay on the admin page). */
const CHECK_TEXT: Record<string, [string, string]> = {
  name: ["Name matches our records", "Name does not match our records yet"],
  tax_id: ["Tax ID confirmed", "Tax ID could not be confirmed"],
  account: ["Bank account confirmed", "Bank account could not be confirmed"],
  readable: ["We will check this document by hand", "We will check this document by hand"],
};
function vendorSafe(check: string, status: string): string {
  const t = CHECK_TEXT[check];
  return t ? t[status === "pass" ? 0 : 1] : status === "pass" ? "Checked" : "Our team will review this";
}

function capitalise(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function Shell({ children }: { children: React.ReactNode }) {
  const { data: cfg } = useConfig();
  const { dark, toggleDark } = usePrefs();
  return (
    <div className="min-h-screen bg-background">
      <header className="border-b bg-surface">
        <div className="mx-auto flex h-14 max-w-2xl items-center gap-2.5 px-4">
          <svg viewBox="0 0 32 32" className="size-7 shrink-0" aria-hidden>
            <rect width="32" height="32" rx="8" className="fill-primary" />
            <path d="M9 9v14h14" stroke="white" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M14 18l4-4 3 3" stroke="#c7d2fe" strokeWidth="2.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <div className="min-w-0 leading-tight">
            <div className="text-[15px] font-semibold tracking-tight">{cfg?.app.app.company || cfg?.app.app.name || " "}</div>
            <div className="text-[11px] text-muted-foreground">Accounts payable · secure document upload</div>
          </div>
          <Button variant="ghost" size="icon-sm" className="ml-auto" onClick={toggleDark} aria-label="Toggle theme">
            {dark ? <Sun /> : <Moon />}
          </Button>
        </div>
      </header>
      <main className="mx-auto max-w-2xl px-4 py-8">{children}</main>
      <footer className="mx-auto max-w-2xl px-4 pb-8 text-center text-[11px] text-muted-foreground">
        <Lock className="mr-1 inline size-3" /> This link is unique to your request. Powered by {cfg?.app.app.name ?? "LedgerFlow"}.
      </footer>
    </div>
  );
}

function Result({ r }: { r: Portal }) {
  const emerald = toneStyle("emerald");
  return (
    <Card className="mt-4 overflow-hidden">
      <div className={cn("flex items-start gap-3 px-5 py-4", emerald.soft)}>
        <CheckCircle2 className={cn("mt-0.5 size-5 shrink-0", emerald.text)} />
        <div>
          <div className="text-sm font-semibold">Thank you, we received {r.uploaded.length} document(s).</div>
          <div className="mt-0.5 text-[13px] text-muted-foreground">
            {r.missing.length
              ? `Still needed: ${r.missing.join(", ")}. You can upload them with this same link.`
              : r.resumed
                ? "Your invoice is moving again."
                : "Our AP team will review them shortly."}
          </div>
        </div>
      </div>
      <ul className="divide-y">
        {r.uploaded.map((u) => (
          <li key={u.document_id} className="px-5 py-3">
            <div className="flex items-center gap-2 text-[13px]">
              <FileText className="size-4 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate font-medium">{u.filename}</span>
              <span className="text-xs text-muted-foreground">read as {u.label.replace(/_/g, " ")}</span>
            </div>
            {u.checks.length > 0 && (
              <ul className="mt-2 space-y-1 pl-6">
                {u.checks.map((c, i) => (
                  <li key={i} className="flex items-start gap-2 text-xs">
                    <StatusBadge kind="check" value={String(c.status)} />
                    <span className="text-muted-foreground">{vendorSafe(String(c.check), String(c.status))}</span>
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

export default function PortalPage() {
  const { token = "" } = useParams();
  const qc = useQueryClient();
  const { data: cfg } = useConfig();
  const maxMb = cfg?.app.limits.max_upload_mb ?? Infinity;
  const [files, setFiles] = useState<File[]>([]);
  const [drag, setDrag] = useState(false);
  const [result, setResult] = useState<Portal | null>(null);
  const [more, setMore] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const q = useQuery({
    queryKey: ["portal", token],
    queryFn: async () => unwrap(await api.GET("/portal/{token}", { params: { path: { token } } })),
    retry: false,
  });
  const upload = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/portal/{token}/upload", {
          params: { path: { token } },
          body: { files: [] as string[] },
          bodySerializer: () => {
            const fd = new FormData();
            files.forEach((f) => fd.append("files", f, f.name));
            return fd;
          },
        }),
      ),
    onSuccess: (r) => {
      setResult(r);
      setFiles([]);
      qc.setQueryData(["portal", token], r);
    },
    onError: (e) => toast.error("Upload failed", { description: e instanceof Error ? e.message : undefined }),
  });

  const add = (list: FileList | null) => {
    if (!list) return;
    const next = [...files];
    for (const f of Array.from(list)) {
      if (f.size > maxMb * 1024 * 1024) {
        toast.error(`${f.name} is larger than ${maxMb} MB`);
        continue;
      }
      if (!next.some((x) => x.name === f.name && x.size === f.size)) next.push(f);
    }
    setFiles(next);
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDrag(false);
    add(e.dataTransfer.files);
  };

  if (q.isLoading)
    return (
      <Shell>
        <Skeleton className="h-7 w-72" />
        <Skeleton className="mt-3 h-4 w-96" />
        <Skeleton className="mt-6 h-40 rounded-xl" />
      </Shell>
    );
  if (q.error || !q.data) {
    const notFound = q.error instanceof ApiError && q.error.status === 404;
    return (
      <Shell>
        <Card className="px-6 py-10 text-center">
          <div className="text-base font-semibold">{notFound ? "This link is not valid" : "Something went wrong"}</div>
          <p className="mt-1 text-sm text-muted-foreground">{notFound ? "Check that you copied the whole link from our email, or ask your contact for a new one." : String(q.error)}</p>
        </Card>
      </Shell>
    );
  }
  const p = q.data;
  const message = p.message as { subject?: string; body?: string } | null;
  const done = p.docs_needed.filter((d) => d.received).length;

  return (
    <Shell>
      <div className="mb-1 flex items-center gap-2">
        <StatusBadge kind="request" value={p.status} />
        {p.case_ref && <span className="text-xs text-muted-foreground">Ref {p.case_ref}</span>}
      </div>
      <h1 className="text-xl font-semibold tracking-tight">
        {p.kind === "onboarding" ? `Welcome${p.vendor_name ? `, ${p.vendor_name}` : ""}` : `Documents for invoice ${p.invoice_number ?? p.case_ref ?? ""}`}
      </h1>
      <p className="mt-1 text-sm text-muted-foreground">
        {p.kind === "onboarding"
          ? "Before we can pay your first invoice we need a few documents to set you up as a supplier."
          : "We need the documents below before we can process your invoice."}
      </p>

      {message?.body && (
        <details className="group mt-4 rounded-lg border bg-card px-4 py-3 text-[13px]">
          <summary className="flex cursor-pointer list-none items-center gap-2 font-medium [&::-webkit-details-marker]:hidden">
            <ChevronRight className="size-4 text-muted-foreground transition-transform group-open:rotate-90" />
            {message.subject ?? "Our message"}
          </summary>
          <p className="mt-2 whitespace-pre-line text-muted-foreground">{message.body}</p>
        </details>
      )}

      <Card className="mt-5">
        <div className="flex items-center justify-between border-b px-5 py-3">
          <div className="text-sm font-semibold">What we need</div>
          <span className="text-xs tabular text-muted-foreground">
            {done} of {p.docs_needed.length} received
          </span>
        </div>
        <ul className="divide-y">
          {p.docs_needed.map((d) => (
            <li key={d.id} className="flex items-center gap-3 px-5 py-3 text-[13px]">
              {d.received ? <CheckCircle2 className={cn("size-4", statusDef("check", "pass").style.text)} /> : <CircleDashed className="size-4 text-muted-foreground" />}
              <span className="flex-1">{capitalise(d.label)}</span>
              <span className={cn("text-xs", d.received ? statusDef("check", "pass").style.text : "text-muted-foreground")}>{d.received ? "Received" : "Needed"}</span>
            </li>
          ))}
        </ul>
      </Card>

      {p.open && done === p.docs_needed.length && !more ? (
        <Card className="mt-5 flex flex-wrap items-center gap-3 px-5 py-4 text-[13px] text-muted-foreground">
          <span className="flex-1">Everything we asked for is here. Our AP team will check it and get back to you.</span>
          <Button variant="outline" size="sm" onClick={() => setMore(true)}>
            <UploadCloud /> Send a corrected document
          </Button>
        </Card>
      ) : p.open ? (
        <div className="mt-5 space-y-3">
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={onDrop}
            onClick={() => input.current?.click()}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
            role="button"
            tabIndex={0}
            aria-label="Choose documents to upload"
            className={cn(
              "flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed bg-card px-6 py-10 text-center transition-colors hover:border-primary/50 hover:bg-accent/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              drag && "border-primary bg-accent/50",
            )}
          >
            <div className="flex size-11 items-center justify-center rounded-full bg-accent">
              <UploadCloud className="size-5 text-accent-foreground" />
            </div>
            <div className="mt-3 text-sm font-medium">Drop your documents here, or click to browse</div>
            <div className="mt-1 text-xs text-muted-foreground">PDF, image or Word{Number.isFinite(maxMb) ? ` · up to ${maxMb} MB each` : ""}</div>
            <input ref={input} type="file" multiple accept={ACCEPT} className="hidden" onChange={(e) => add(e.target.files)} />
          </div>
          {files.length > 0 && (
            <Card className="divide-y">
              {files.map((f) => (
                <div key={f.name + f.size} className="flex items-center gap-3 px-4 py-2.5">
                  <FileText className="size-4 text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate text-sm">{f.name}</span>
                  <span className="text-xs tabular text-muted-foreground">{size(f.size)}</span>
                  <Button variant="ghost" size="icon-sm" aria-label={`Remove ${f.name}`} onClick={() => setFiles(files.filter((x) => x !== f))}>
                    <Trash2 />
                  </Button>
                </div>
              ))}
            </Card>
          )}
          <div className="flex justify-end">
            <Button size="lg" disabled={!files.length || upload.isPending} onClick={() => upload.mutate()}>
              {upload.isPending ? <Loader2 className="animate-spin" /> : <UploadCloud />} Upload {files.length ? `${files.length} file(s)` : ""}
            </Button>
          </div>
        </div>
      ) : (
        <Card className="mt-5 px-5 py-4 text-[13px] text-muted-foreground">This request is {statusDef("request", p.status).label.toLowerCase()}. No more uploads are needed.</Card>
      )}
      {result && <Result r={result} />}
    </Shell>
  );
}
