import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, FileText, FileUp, Loader2, Radio, Route, Trash2, UploadCloud } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { api, unwrap, type Case } from "@/api/client";
import { PageHeader } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Label, Textarea } from "@/components/ui/primitives";
import { useConfig } from "@/lib/config";
import { readPref, writePref } from "@/lib/prefs";
import { toneStyle } from "@/lib/status";
import { cn } from "@/lib/utils";
import { caseLink } from "@/lib/nav";

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.docx";

function size(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function SubmitPage() {
  const { data: cfg } = useConfig();
  const maxMb = cfg?.app.limits.max_upload_mb ?? Infinity; // the server enforces the limit too
  const [files, setFiles] = useState<File[]>([]);
  const [name, setName] = useState(() => readPref("lf-submitter-name", ""));
  const [email, setEmail] = useState(() => readPref("lf-submitter-email", ""));
  const [note, setNote] = useState("");
  const [drag, setDrag] = useState(false);
  const [done, setDone] = useState<Case | null>(null);
  const input = useRef<HTMLInputElement>(null);

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

  const submit = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/cases", {
          body: { files: [] as string[], submitter_name: name || null, submitter_email: email || null, note: note || null },
          bodySerializer: () => {
            const fd = new FormData();
            files.forEach((f) => fd.append("files", f, f.name));
            if (name) fd.append("submitter_name", name);
            if (email) fd.append("submitter_email", email);
            if (note) fd.append("note", note);
            return fd;
          },
        }),
      ),
    onSuccess: (c) => {
      writePref("lf-submitter-name", name);
      writePref("lf-submitter-email", email);
      setDone(c);
      setFiles([]);
      setNote("");
    },
    onError: (e) => toast.error("Upload failed", { description: e instanceof Error ? e.message : undefined }),
  });

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDrag(false);
    add(e.dataTransfer.files);
  };

  if (done) {
    const t = toneStyle("emerald");
    return (
      <div className="mx-auto max-w-xl pt-6">
        <Card className="overflow-hidden">
          <div className={cn("flex flex-col items-center px-8 pb-6 pt-8 text-center", t.soft)}>
            <CheckCircle2 className={cn("size-10", t.text)} />
            <h1 className="mt-3 text-lg font-semibold">Invoice received</h1>
            <p className="mt-1 text-sm text-muted-foreground">Keep this reference. You can follow every step as the agents work through it.</p>
            <div className="mt-4 rounded-lg border bg-card px-5 py-2.5 font-mono text-lg font-semibold tracking-wide tabular">{done.ref}</div>
          </div>
          <div className="flex flex-wrap justify-center gap-2 border-t px-6 py-4">
            <Button asChild>
              <Link to={`/submissions?case=${done.id}`}>
                <Route /> Track the journey
              </Link>
            </Button>
            <Button variant="outline" asChild>
              <Link to={caseLink(done)}>
                <Radio /> Watch it live
              </Link>
            </Button>
            <Button variant="ghost" onClick={() => setDone(null)}>
              Submit another
            </Button>
          </div>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="Submit invoices" description="Upload one or more invoices with any supporting documents. Bundles with several invoices are split automatically." />
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (files.length) submit.mutate();
        }}
        className="space-y-5"
      >
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
          aria-label="Choose files to upload"
          className={cn(
            "flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed bg-card px-6 py-12 text-center transition-colors hover:border-primary/50 hover:bg-accent/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            drag && "border-primary bg-accent/50",
          )}
        >
          <div className="flex size-12 items-center justify-center rounded-full bg-accent">
            <UploadCloud className="size-6 text-accent-foreground" />
          </div>
          <div className="mt-3 text-sm font-medium">Drop PDFs, images or DOCX here, or click to browse</div>
          <div className="mt-1 text-xs text-muted-foreground">
            {cfg ? `Up to ${maxMb} MB per file · ${cfg.app.limits.max_pages_per_document} pages per document` : " "}
          </div>
          <input ref={input} type="file" multiple accept={ACCEPT} className="hidden" onChange={(e) => add(e.target.files)} />
        </div>

        {files.length > 0 && (
          <Card className="divide-y">
            {files.map((f) => (
              <div key={f.name + f.size} className="flex items-center gap-3 px-4 py-2.5">
                <FileText className="size-4 text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate text-sm">{f.name}</span>
                <span className="text-xs tabular text-muted-foreground">{size(f.size)}</span>
                <Button type="button" variant="ghost" size="icon-sm" aria-label={`Remove ${f.name}`} onClick={() => setFiles(files.filter((x) => x !== f))}>
                  <Trash2 />
                </Button>
              </div>
            ))}
          </Card>
        )}

        <Card className="grid gap-4 p-5 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="name">Your name</Label>
            <Input id="name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Jane Doe" autoComplete="name" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="email">Email</Label>
            <Input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="billing@vendor.com" autoComplete="email" />
            <p className="text-xs text-muted-foreground">Requests for missing documents go to this address as drafts.</p>
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="note">Note for the AP team (optional)</Label>
            <Textarea id="note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. second part of the split PO-1001 delivery" />
          </div>
        </Card>

        <div className="flex items-center justify-end gap-3">
          <span className="text-xs text-muted-foreground">{files.length ? `${files.length} file(s) ready` : "Add at least one file"}</span>
          <Button type="submit" size="lg" disabled={!files.length || submit.isPending}>
            {submit.isPending ? <Loader2 className="animate-spin" /> : <FileUp />} Submit
          </Button>
        </div>
      </form>
    </div>
  );
}
