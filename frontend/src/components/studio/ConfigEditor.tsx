/**
 * One config file in CodeMirror: the running content (Studio version or file), unsaved draft, validation of the
 * whole config while typing (inline markers + list), Save, Discard, Reset to file and version history.
 */
import { markdown } from "@codemirror/lang-markdown";
import { yaml as yamlLang } from "@codemirror/lang-yaml";
import { linter, lintGutter, type Diagnostic } from "@codemirror/lint";
import CodeMirror, { EditorView } from "@uiw/react-codemirror";
import { CheckCircle2, CircleAlert, History, Loader2, RotateCcw, Save, Undo2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import type { ConfigVersion } from "@/api/client";
import { ErrorNote, StatusBadge } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle, Popover, PopoverContent, PopoverTrigger, Tip } from "@/components/ui/overlays";
import { Kbd, Skeleton } from "@/components/ui/primitives";
import { dateTime, relative } from "@/lib/format";
import { usePrefs } from "@/lib/prefs";
import { statusDef } from "@/lib/status";
import { errorsOf, locateErrors, useConfigFile, useDrafts, useResetConfig, useSaveConfig, validateEdits, type LineError } from "@/lib/studio";
import { cn } from "@/lib/utils";

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function Versions({ versions, onLoad }: { versions: ConfigVersion[]; onLoad: (v: ConfigVersion) => void }) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button size="sm" variant="ghost" disabled={!versions.length}>
          <History /> Versions{versions.length ? ` (${versions.length})` : ""}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 p-1">
        <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground">Studio versions, newest first. Loading one puts it in the editor as an unsaved change.</div>
        <ul className="max-h-72 overflow-y-auto">
          {versions.map((v) => (
            <li key={v.version}>
              <button className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] hover:bg-muted" onClick={() => onLoad(v)}>
                <span className="font-medium tabular">v{v.version}</span>
                {v.active && <StatusBadge kind="config" value="override" />}
                <span className="ml-auto text-xs text-muted-foreground">
                  {v.author ?? "—"} · <span title={dateTime(v.created_at)}>{relative(v.created_at)}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  );
}

export function ConfigEditor({ path, height = "calc(100vh - 330px)", toolbar }: { path: string; height?: string; toolbar?: React.ReactNode }) {
  const { dark } = usePrefs();
  const q = useConfigFile(path);
  const { drafts, setDraft } = useDrafts();
  const save = useSaveConfig();
  const reset = useResetConfig();
  const [errors, setErrors] = useState<string[]>([]);
  const [checking, setChecking] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);

  const server = q.data?.content ?? "";
  const value = drafts[path] ?? server;
  const dirty = drafts[path] !== undefined && drafts[path] !== server;
  const debounced = useDebounced(value, 450);

  useEffect(() => {
    if (!q.data) return;
    if (debounced === q.data.content) {
      setErrors([]);
      return;
    }
    let live = true;
    setChecking(true);
    validateEdits({ [path]: debounced })
      .then((e) => live && setErrors(e))
      .catch((e: unknown) => live && setErrors(errorsOf(e)))
      .finally(() => live && setChecking(false));
    return () => {
      live = false;
    };
  }, [debounced, path, q.data]);

  const located = useMemo(() => locateErrors(path, value, errors), [path, value, errors]);
  const extensions = useMemo(() => {
    const lang = path.endsWith(".md") ? markdown() : yamlLang();
    const lint = linter(
      (view) => {
        const out: Diagnostic[] = [];
        for (const e of located.mine) {
          if (e.line === null || e.line > view.state.doc.lines) continue;
          const line = view.state.doc.line(e.line);
          out.push({ from: line.from, to: Math.max(line.to, line.from + 1), severity: "error", message: e.message });
        }
        return out;
      },
      { delay: 0 },
    );
    return [lang, lintGutter(), lint, EditorView.lineWrapping];
  }, [path, located]);

  const onSave = () =>
    save.mutate(
      { path, content: value },
      {
        onSuccess: (d) => {
          setDraft(path, undefined);
          setErrors([]);
          toast.success(`${path} saved as v${d.active_version}`, { description: "New cases run on it; cases already started keep their snapshot." });
        },
        onError: (e) => {
          setErrors(errorsOf(e));
          toast.error("Not saved: the config is invalid", { description: errorsOf(e)[0] });
        },
      },
    );
  const onReset = () =>
    reset.mutate(path, {
      onSuccess: () => {
        setDraft(path, undefined);
        setConfirmReset(false);
        setErrors([]);
        toast.success(`${path} reset to the file`);
      },
      onError: (e) => {
        setConfirmReset(false);
        toast.error("Could not reset", { description: errorsOf(e)[0] });
      },
    });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s" && dirty && !errors.length) {
        e.preventDefault();
        onSave();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (q.error) return <ErrorNote error={q.error} />;
  if (!q.data) return <Skeleton className="rounded-lg" style={{ height }} />;
  const invalid = dirty && errors.length > 0;
  const state = dirty ? (checking ? "unsaved" : invalid ? "invalid" : "valid") : q.data.overridden ? "override" : "file";

  return (
    <div className="flex min-w-0 flex-col overflow-hidden rounded-lg border bg-card">
      <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2">
        <span className="font-mono text-xs text-muted-foreground">{path}</span>
        <StatusBadge kind="config" value={state} pulse={checking} />
        {q.data.overridden && !dirty && <span className="text-xs text-muted-foreground">v{q.data.active_version}</span>}
        {toolbar}
        <div className="ml-auto flex items-center gap-1">
          <Versions versions={q.data.versions} onLoad={(v) => setDraft(path, v.content)} />
          {q.data.overridden && q.data.file_content !== null && (
            <Tip content="Deactivate the Studio versions and run the file in config/ again">
              <Button size="sm" variant="ghost" onClick={() => setConfirmReset(true)}>
                <RotateCcw /> Reset to file
              </Button>
            </Tip>
          )}
          {dirty && (
            <Button size="sm" variant="ghost" onClick={() => setDraft(path, undefined)}>
              <Undo2 /> Discard
            </Button>
          )}
          <Tip content={<span>Validate and save <Kbd className="ml-1 border-background/30 bg-transparent text-background">Ctrl S</Kbd></span>}>
            <span>
              <Button size="sm" disabled={!dirty || invalid || checking || save.isPending} onClick={onSave}>
                {save.isPending ? <Loader2 className="animate-spin" /> : <Save />} Save
              </Button>
            </span>
          </Tip>
        </div>
      </div>
      <ErrorPanel mine={located.mine} others={located.others} dirty={dirty} checking={checking} />
      <CodeMirror
        value={value}
        height={height}
        theme={dark ? "dark" : "light"}
        extensions={extensions}
        onChange={(v) => setDraft(path, v === server ? undefined : v)}
        basicSetup={{ foldGutter: true, highlightActiveLine: true }}
        className="text-[12.5px]"
        aria-label={`Edit ${path}`}
      />
      <Dialog open={confirmReset} onOpenChange={setConfirmReset}>
        <DialogContent>
          <div className="px-6 pb-5 pt-6">
            <DialogTitle className="text-base font-semibold">Reset {path} to the file?</DialogTitle>
            <DialogDescription className="mt-1.5 text-[13px] text-muted-foreground">
              The Studio version stops running and new cases use config/{path} again. Its history is kept, so you can load it back from Versions.
            </DialogDescription>
          </div>
          <div className="flex justify-end gap-2 border-t bg-muted/40 px-6 py-3">
            <Button variant="outline" onClick={() => setConfirmReset(false)}>
              Cancel
            </Button>
            <Button autoFocus disabled={reset.isPending} onClick={onReset}>
              {reset.isPending && <Loader2 className="animate-spin" />} Reset to file
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function ErrorPanel({ mine, others, dirty, checking }: { mine: LineError[]; others: string[]; dirty: boolean; checking: boolean }) {
  const all = [...mine.map((e) => ({ line: e.line, message: e.message })), ...others.map((m) => ({ line: null, message: m }))];
  const rose = statusDef("config", "invalid").style;
  const ok = statusDef("config", "valid").style;
  if (!dirty) return null;
  if (checking && !all.length) return <div className="border-b px-3 py-2 text-xs text-muted-foreground">Checking the whole config…</div>;
  if (!all.length)
    return (
      <div className={cn("flex items-center gap-1.5 border-b px-3 py-2 text-xs", ok.text)}>
        <CheckCircle2 className="size-3.5" /> The whole config is valid with this change.
      </div>
    );
  return (
    <div className={cn("max-h-40 overflow-y-auto border-b px-3 py-2", rose.soft)} role="alert">
      <div className={cn("mb-1 flex items-center gap-1.5 text-xs font-medium", rose.text)}>
        <CircleAlert className="size-3.5" /> {all.length} problem{all.length === 1 ? "" : "s"}: this would not be saved
      </div>
      <ul className="space-y-0.5 font-mono text-[11.5px]">
        {all.map((e, i) => (
          <li key={i}>
            {e.line !== null && <span className={cn("mr-2 font-semibold", rose.text)}>L{e.line}</span>}
            {e.message}
          </li>
        ))}
      </ul>
    </div>
  );
}
