/**
 * rules.yaml as a table: enabled, severity, applies-to and params per rule, plus the auto-approve gates. Edits go
 * through the YAML document (comments and layout survive) into the shared draft, so "Edit as YAML" shows the same
 * change, and Save validates the whole config like any other edit.
 */
import { CheckCircle2, CircleAlert, Loader2, Save, Undo2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { parseDocument } from "yaml";
import { ErrorNote, StatusBadge } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Tip } from "@/components/ui/overlays";
import { Checkbox, Input, Select, Skeleton } from "@/components/ui/primitives";
import { statusDef } from "@/lib/status";
import { errorsOf, useConfigFile, useDrafts, useSaveConfig, validateEdits } from "@/lib/studio";
import { cn } from "@/lib/utils";

const PATH = "rules.yaml";
const SEVERITIES = ["reject", "hold", "review", "info"];
const APPLIES = ["po", "non_po", "all"];

type Scalar = string | number | boolean | null;

interface RuleRow {
  id: string;
  name: string;
  enabled: boolean;
  applies_to: string;
  severity: string;
  params: Record<string, unknown>;
}

/** Keeps the typed text ("2.") while committing every complete number to the YAML. */
function NumberInput({ name, value, onChange }: { name: string; value: number; onChange: (v: number) => void }) {
  const [text, setText] = useState(String(value));
  useEffect(() => {
    // only outside changes (discard, a loaded version) replace what is being typed
    setText((t) => (Number(t) === value ? t : String(value)));
  }, [value]);
  return (
    <Input
      inputMode="decimal"
      aria-label={name}
      value={text}
      onChange={(e) => {
        setText(e.target.value);
        if (/^-?\d+(\.\d+)?$/.test(e.target.value.trim())) onChange(Number(e.target.value));
      }}
      className="h-7 w-24 px-2 text-right text-xs tabular"
    />
  );
}

function ParamInput({ name, value, onChange }: { name: string; value: unknown; onChange: (v: Scalar) => void }) {
  if (typeof value === "boolean") return <Checkbox label={name} checked={value} onChange={onChange} />;
  if (typeof value === "number") return <NumberInput name={name} value={value} onChange={onChange} />;
  if (typeof value === "string" || value === null)
    return <Input aria-label={name} value={value ?? ""} onChange={(e) => onChange(e.target.value)} className="h-7 w-36 px-2 text-xs" />;
  return (
    <Tip content="Lists and maps are edited in the YAML view">
      <span className="max-w-[180px] truncate rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">{JSON.stringify(value)}</span>
    </Tip>
  );
}

function Params({ params, onChange }: { params: Record<string, unknown>; onChange: (key: string, v: Scalar) => void }) {
  const keys = Object.keys(params);
  if (!keys.length) return <span className="text-xs text-muted-foreground">—</span>;
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1.5">
      {keys.map((k) => (
        <label key={k} className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span className="font-mono">{k}</span>
          <ParamInput name={k} value={params[k]} onChange={(v) => onChange(k, v)} />
        </label>
      ))}
    </div>
  );
}

export function RulesEditor() {
  const q = useConfigFile(PATH);
  const { drafts, setDraft } = useDrafts();
  const save = useSaveConfig();
  const [errors, setErrors] = useState<string[]>([]);
  const server = q.data?.content ?? "";
  const content = drafts[PATH] ?? server;
  const dirty = drafts[PATH] !== undefined && drafts[PATH] !== server;

  const parsed = useMemo(() => {
    const doc = parseDocument(content);
    const js = doc.toJS() as { rules?: RuleRow[]; auto_approve_gates?: Record<string, unknown> } | null;
    return { doc, rules: js?.rules ?? [], gates: js?.auto_approve_gates ?? {}, yamlError: doc.errors[0]?.message };
  }, [content]);
  const base = useMemo(() => (parseDocument(server).toJS() as { rules?: RuleRow[] } | null)?.rules ?? [], [server]);
  const changedIds = new Set(parsed.rules.filter((r, i) => JSON.stringify(r) !== JSON.stringify(base[i])).map((r) => r.id));

  useEffect(() => {
    if (!dirty) {
      setErrors([]);
      return;
    }
    let live = true;
    const t = setTimeout(() => {
      validateEdits({ [PATH]: content })
        .then((e) => live && setErrors(e))
        .catch((e: unknown) => live && setErrors(errorsOf(e)));
    }, 400);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [content, dirty]);

  const setIn = (keys: (string | number)[], value: Scalar) => {
    const doc = parseDocument(content);
    doc.setIn(keys, value);
    const next = String(doc);
    setDraft(PATH, next === server ? undefined : next);
  };

  const onSave = () =>
    save.mutate(
      { path: PATH, content },
      {
        onSuccess: (d) => {
          setDraft(PATH, undefined);
          toast.success(`Rules saved as v${d.active_version}`, { description: "New cases are checked with them; cases already started keep their snapshot." });
        },
        onError: (e) => {
          setErrors(errorsOf(e));
          toast.error("Not saved: the config is invalid", { description: errorsOf(e)[0] });
        },
      },
    );

  if (q.error) return <ErrorNote error={q.error} />;
  if (!q.data) return <Skeleton className="h-96 rounded-lg" />;
  if (parsed.yamlError) return <ErrorNote error={`rules.yaml does not parse (${parsed.yamlError}). Fix it in the YAML view.`} />;
  const rose = statusDef("config", "invalid").style;
  const amber = statusDef("config", "unsaved").style;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-card px-4 py-3">
        <div className="text-[13px] font-semibold">Auto-approve gates</div>
        <Params params={parsed.gates} onChange={(k, v) => setIn(["auto_approve_gates", k], v)} />
      </div>

      <div className="overflow-hidden rounded-lg border bg-card">
        <div className="max-h-[calc(100vh-420px)] overflow-auto pb-2">
          <table className="w-full border-separate border-spacing-0 text-[13px]">
            <thead>
              <tr>
                {["On", "Rule", "Applies to", "Severity", "Params"].map((h) => (
                  <th key={h} className="sticky top-0 z-10 border-b bg-muted px-3 py-2 text-left text-xs font-medium text-muted-foreground">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {parsed.rules.map((r, i) => (
                <tr key={r.id} className={cn(changedIds.has(r.id) && amber.soft, !r.enabled && "text-muted-foreground")}>
                  <td className="border-b px-3 py-2 align-top">
                    <Checkbox label={`Enable ${r.id}`} checked={r.enabled !== false} onChange={(on) => setIn(["rules", i, "enabled"], on)} />
                  </td>
                  <td className="border-b px-3 py-2 align-top">
                    <div className="font-medium">{r.name}</div>
                    <div className="font-mono text-[11px] text-muted-foreground">{r.id}</div>
                  </td>
                  <td className="border-b px-3 py-2 align-top">
                    <Select aria-label={`${r.id} applies to`} value={r.applies_to} onChange={(e) => setIn(["rules", i, "applies_to"], e.target.value)} className="h-7 text-xs">
                      {APPLIES.map((a) => (
                        <option key={a} value={a}>
                          {a}
                        </option>
                      ))}
                    </Select>
                  </td>
                  <td className="border-b px-3 py-2 align-top">
                    <div className="flex items-center gap-2">
                      <Select aria-label={`${r.id} severity`} value={r.severity} onChange={(e) => setIn(["rules", i, "severity"], e.target.value)} className="h-7 text-xs">
                        {SEVERITIES.map((s) => (
                          <option key={s} value={s}>
                            {s}
                          </option>
                        ))}
                      </Select>
                      <StatusBadge kind="severity" value={r.severity} />
                    </div>
                  </td>
                  <td className="border-b px-3 py-2 align-top">
                    <Params params={r.params ?? {}} onChange={(k, v) => setIn(["rules", i, "params", k], v)} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div
        className={cn(
          "sticky bottom-0 flex flex-wrap items-center gap-3 rounded-lg border bg-card px-4 py-2.5 shadow-sm transition-opacity",
          dirty ? "opacity-100" : "pointer-events-none opacity-0",
        )}
        aria-hidden={!dirty}
      >
        {errors.length ? (
          <div className={cn("flex min-w-0 flex-1 items-start gap-1.5 text-xs", rose.text)} role="alert">
            <CircleAlert className="mt-px size-3.5 shrink-0" />
            <span className="line-clamp-2">{errors.join(" · ")}</span>
          </div>
        ) : (
          <div className="flex flex-1 items-center gap-1.5 text-xs text-muted-foreground">
            <CheckCircle2 className={cn("size-3.5", statusDef("config", "valid").style.text)} />
            {changedIds.size || "Gate"} change{changedIds.size === 1 ? "" : "s"} not saved · the whole config is valid
          </div>
        )}
        <Button size="sm" variant="ghost" onClick={() => setDraft(PATH, undefined)}>
          <Undo2 /> Discard
        </Button>
        <Button size="sm" disabled={!!errors.length || save.isPending} onClick={onSave}>
          {save.isPending ? <Loader2 className="animate-spin" /> : <Save />} Save rules
        </Button>
      </div>
    </div>
  );
}
