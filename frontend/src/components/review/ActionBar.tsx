import { Check, Copy, ExternalLink, Link2, Mail, MessageSquarePlus, Send, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import type { GridOp } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle, Tip } from "@/components/ui/overlays";
import { Badge, Input, Kbd, Label, Skeleton, Textarea } from "@/components/ui/primitives";
import { statusDef, toneStyle } from "@/lib/status";
import { cn } from "@/lib/utils";

export type ReviewActionName = "approve" | "remark" | "reject" | "request_info";
type DialogName = "approve" | "remark" | "reject" | "ask" | null;

export interface Draft {
  kind: string;
  to: string | null;
  subject: string;
  body: string;
  mailto: string;
  portal_link: string | null;
  needs_text?: string[];
}

function typingInField(e: KeyboardEvent): boolean {
  const t = e.target as HTMLElement | null;
  return !!t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
}

function describeOp(op: GridOp): string {
  if (op.op === "set_gl") return `Line ${op.line_index + 1}: GL ${op.gl_code}${op.department ? `, department ${op.department}` : ""}`;
  return `${op.path} → ${op.value === null ? "empty" : String(op.value)}`;
}

async function copy(text: string, what: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(`${what} copied`);
  } catch {
    toast.error("Couldn't copy", { description: "Select the text and copy it manually." });
  }
}

export function DraftView({ draft }: { draft: Draft }) {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-[64px_1fr] gap-y-1.5 text-[13px]">
        <span className="text-muted-foreground">To</span>
        <span className="font-medium">{draft.to ?? <span className="text-muted-foreground">no address on file</span>}</span>
        <span className="text-muted-foreground">Subject</span>
        <span className="font-medium">{draft.subject}</span>
      </div>
      {(draft.needs_text?.length ?? 0) > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {draft.needs_text!.map((n) => (
            <Badge key={n} className="max-w-full whitespace-normal text-left">
              {n}
            </Badge>
          ))}
        </div>
      )}
      {draft.portal_link && (
        <div className="space-y-1">
          <div className="text-xs font-medium text-muted-foreground">Upload link (no login)</div>
          <div className="flex items-center gap-1.5">
            <Input readOnly value={draft.portal_link} className="h-8 font-mono text-xs" onFocus={(e) => e.currentTarget.select()} aria-label="Upload link" />
            <Button size="sm" variant="outline" onClick={() => copy(draft.portal_link!, "Upload link")}>
              <Link2 /> Copy upload link
            </Button>
          </div>
        </div>
      )}
      <pre className="max-h-52 overflow-auto whitespace-pre-wrap rounded-lg border bg-muted/40 px-3.5 py-3 font-sans text-[13px] leading-relaxed">{draft.body}</pre>
      <div className="flex flex-nowrap gap-1">
        <Button size="sm" variant="ghost" onClick={() => copy(`Subject: ${draft.subject}

${draft.body}`, "Message")}>
          <Copy /> Copy message
        </Button>
        {draft.portal_link && (
          <Button size="sm" variant="ghost" asChild>
            <a href={draft.portal_link} target="_blank" rel="noreferrer">
              <ExternalLink /> Open portal
            </a>
          </Button>
        )}
      </div>
      <p className="text-[11px] text-muted-foreground">Nothing is sent automatically: copy the message or open it in your mail app.</p>
    </div>
  );
}

export function ActionBar({
  caseRef,
  totalText,
  versionNo,
  reviewable,
  busy,
  pendingOps,
  onDiscardEdits,
  onSubmit,
  draft,
  drafting,
  onHold,
  closed,
}: {
  caseRef: string;
  totalText: string;
  versionNo: number | null;
  reviewable: boolean;
  busy: boolean;
  pendingOps: GridOp[];
  onDiscardEdits: () => void;
  onSubmit: (action: ReviewActionName, text: string | null, ops?: GridOp[]) => Promise<void>;
  draft: Draft | null;
  drafting: boolean;
  onHold: boolean;
  closed?: React.ReactNode; // a finished case: one status line instead of the buttons
}) {
  const [open, setOpen] = useState<DialogName>(null);
  const hasEditsNow = () => pendingOps.length > 0;
  const [text, setText] = useState("");
  const [asked, setAsked] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);

  const show = (d: DialogName) => {
    if (d === "ask" && (onHold || asked)) return setOpen(d); // view the existing draft
    if (!reviewable && d !== null) return;
    if (d === "approve" && hasEditsNow()) return toast.warning("Apply or discard your grid edits before approving");
    setText("");
    setOpen(d);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (open || e.ctrlKey || e.metaKey || e.altKey || typingInField(e)) return;
      const map: Record<string, DialogName> = { a: "approve", r: "remark", x: "reject", v: "ask" };
      const d = map[e.key.toLowerCase()];
      if (!d) return;
      e.preventDefault();
      show(d);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const submit = async (action: ReviewActionName, body: string | null, ops?: GridOp[]) => {
    try {
      await onSubmit(action, body, ops);
      if (action === "request_info") setAsked(true);
      else setOpen(null);
    } catch {
      /* the page toasts the error; keep the dialog open so the text isn't lost */
    }
  };

  const hasEdits = pendingOps.length > 0;
  const remarkReady = text.trim().length > 0 || hasEdits;
  const amber = toneStyle("amber");
  const approveTone = statusDef("case", "approved").style;
  const rejectTone = statusDef("case", "rejected").style;

  return (
    <>
      {closed ? (
        <div className="flex flex-wrap items-center gap-3 px-4 py-3 text-[13px]">{closed}</div>
      ) : (
        <div className="flex flex-wrap items-center gap-2 px-4 py-3">
          <Tip content={hasEdits ? `Apply or discard your ${pendingOps.length} edit${pendingOps.length === 1 ? "" : "s"} first` : "Approve and finalise (A)"}>
            <span>
              <Button onClick={() => show("approve")} disabled={!reviewable || busy || hasEdits} className={approveTone.solid}>
                <Check /> Approve <Kbd className="border-white/30 bg-white/15 text-current">A</Kbd>
              </Button>
            </span>
          </Tip>
          <Button variant="outline" onClick={() => show("remark")} disabled={!reviewable || busy}>
            <MessageSquarePlus /> Request changes <Kbd>R</Kbd>
            {hasEdits && <Badge className="ml-0.5 bg-accent text-accent-foreground ring-primary/30">{pendingOps.length} edit{pendingOps.length === 1 ? "" : "s"}</Badge>}
          </Button>
          <Button variant="outline" onClick={() => show("reject")} disabled={!reviewable || busy} className={rejectTone.text}>
            <X /> Reject <Kbd>X</Kbd>
          </Button>
          <Button variant="outline" onClick={() => show("ask")} disabled={(!reviewable && !onHold && !asked) || busy}>
            <Send /> {onHold ? "Vendor request" : "Ask vendor"} <Kbd>V</Kbd>
          </Button>
          <div className={cn("ml-auto text-xs", hasEdits ? amber.text : "text-muted-foreground")}>
            {busy && !reviewable ? "Working…" : hasEdits ? "Edits are applied when you request changes (R)." : "Shortcuts work anywhere outside a text field."}
          </div>
        </div>
      )}

      {/* approve */}
      <Dialog open={open === "approve"} onOpenChange={(o) => !o && setOpen(null)}>
        <DialogContent>
          <div className="px-6 pb-5 pt-6">
            <DialogTitle className="text-base font-semibold">Approve {caseRef}?</DialogTitle>
            <DialogDescription className="mt-1.5 text-[13px] text-muted-foreground">
              {totalText} will be finalised{versionNo ? ` as template v${versionNo}` : ""} and its PO reservation becomes consumption. It then waits in the approved library for export.
            </DialogDescription>
            {hasEdits && (
              <div className={cn("mt-4 rounded-md border px-3 py-2 text-[13px]", amber.border, amber.soft)}>
                You have {pendingOps.length} grid edit{pendingOps.length === 1 ? "" : "s"} that are not applied. Request changes to apply them, or discard them before approving.
              </div>
            )}
          </div>
          <div className="flex justify-end gap-2 border-t bg-muted/40 px-6 py-3">
            {hasEdits && (
              <Button variant="ghost" onClick={onDiscardEdits}>
                Discard edits
              </Button>
            )}
            <Button variant="outline" onClick={() => setOpen(null)}>
              Cancel
            </Button>
            <Button autoFocus disabled={busy || hasEdits} onClick={() => submit("approve", null)} className={approveTone.solid}>
              <Check /> Approve
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* remark */}
      <Dialog open={open === "remark"} onOpenChange={(o) => !o && setOpen(null)}>
        <DialogContent className="max-w-xl" onOpenAutoFocus={(e) => (e.preventDefault(), textRef.current?.focus())}>
          <div className="space-y-4 px-6 pb-5 pt-6">
            <div>
              <DialogTitle className="text-base font-semibold">Request changes</DialogTitle>
              <DialogDescription className="mt-1.5 text-[13px] text-muted-foreground">
                Write what should change in plain English. The reconciler turns it into typed changes, code validates them, and the checks run again. Anything it can't apply comes back as a question.
              </DialogDescription>
            </div>
            {hasEdits && (
              <div className="rounded-md border px-3 py-2">
                <div className="mb-1 text-xs font-medium text-muted-foreground">Grid edits (applied exactly as typed, no model involved)</div>
                <ul className="space-y-0.5 text-[13px] tabular">
                  {pendingOps.map((op, i) => (
                    <li key={i}>{describeOp(op)}</li>
                  ))}
                </ul>
              </div>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="remark">{hasEdits ? "Note (optional)" : "Remark"}</Label>
              <Textarea
                id="remark"
                ref={textRef}
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder={hasEdits ? "Why these edits (kept in the audit trail)" : "e.g. PO was amended to $10,500, accept the premium support line and code it to 6410"}
                className="min-h-[110px]"
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && remarkReady) submit("remark", text.trim() || null, hasEdits ? pendingOps : undefined);
                }}
              />
              <div className="text-[11px] text-muted-foreground">
                <Kbd>Ctrl</Kbd> + <Kbd>Enter</Kbd> to send
              </div>
            </div>
          </div>
          <div className="flex justify-end gap-2 border-t bg-muted/40 px-6 py-3">
            <Button variant="outline" onClick={() => setOpen(null)}>
              Cancel
            </Button>
            <Button disabled={!remarkReady || busy} onClick={() => submit("remark", text.trim() || null, hasEdits ? pendingOps : undefined)}>
              <MessageSquarePlus /> Send to reconciler
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* reject */}
      <Dialog open={open === "reject"} onOpenChange={(o) => !o && setOpen(null)}>
        <DialogContent onOpenAutoFocus={(e) => (e.preventDefault(), textRef.current?.focus())}>
          <div className="space-y-4 px-6 pb-5 pt-6">
            <div>
              <DialogTitle className="text-base font-semibold">Reject {caseRef}</DialogTitle>
              <DialogDescription className="mt-1.5 text-[13px] text-muted-foreground">
                The PO reservation is released and a rejection message is drafted for the submitter. A reason is required.
              </DialogDescription>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="reject-reason">Reason</Label>
              <Textarea id="reject-reason" ref={textRef} value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. Duplicate of an invoice already paid" />
            </div>
          </div>
          <div className="flex justify-end gap-2 border-t bg-muted/40 px-6 py-3">
            <Button variant="outline" onClick={() => setOpen(null)}>
              Cancel
            </Button>
            <Button variant="destructive" disabled={!text.trim() || busy} onClick={() => submit("reject", text.trim())}>
              <X /> Reject invoice
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* ask vendor */}
      <Dialog open={open === "ask"} onOpenChange={(o) => !o && setOpen(null)}>
        <DialogContent className="max-w-xl" onOpenAutoFocus={(e) => (e.preventDefault(), textRef.current?.focus())}>
          <div className="space-y-4 px-6 pb-5 pt-6">
            <div>
              <DialogTitle className="text-base font-semibold">{onHold || asked ? "Request to the vendor" : "Ask the vendor"}</DialogTitle>
              <DialogDescription className="mt-1.5 text-[13px] text-muted-foreground">
                {onHold || asked
                  ? "The case is on hold until the vendor uploads what we asked for through the no-login portal link."
                  : "The case goes on hold and the comms agent drafts a message with a no-login upload link. Say what you need."}
              </DialogDescription>
            </div>
            {!(onHold || asked) && (
              <div className="space-y-1.5">
                <Label htmlFor="ask-text">What do you need?</Label>
                <Textarea id="ask-text" ref={textRef} value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. Please send the signed PO amendment for the extra $300" />
              </div>
            )}
            {(onHold || asked) &&
              (draft && !drafting ? (
                <DraftView draft={draft} />
              ) : (
                <div className="space-y-2" aria-busy>
                  <div className="text-xs text-muted-foreground">Drafting the message…</div>
                  <Skeleton className="h-4 w-2/3" />
                  <Skeleton className="h-24 w-full" />
                </div>
              ))}
          </div>
          <div className="flex justify-end gap-2 border-t bg-muted/40 px-6 py-3">
            <Button variant="outline" onClick={() => setOpen(null)}>
              {onHold || asked ? "Close" : "Cancel"}
            </Button>
            {(onHold || asked) && draft && !drafting && (
              <Button asChild>
                <a href={draft.mailto}>
                  <Mail /> Open in mail app
                </a>
              </Button>
            )}
            {!(onHold || asked) && (
              <Button disabled={!text.trim() || busy} onClick={() => submit("request_info", text.trim())}>
                <Send /> Put on hold and draft
              </Button>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
