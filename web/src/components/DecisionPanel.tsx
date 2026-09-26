import { useId, useState, type FormEvent } from "react";
import { CircleAlert, Download, Info, Save } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { TONE_CLASSES } from "@/components/StatusBadge";
import { MIN_RATIONALE, type DecisionInput } from "@/lib/decision-file";
import { useDecisionSaver, type SavedFile } from "@/lib/decision-saver";
import { formatValue } from "@/lib/evidence";
import type { Comparison, Decision, Intent, ReviewReport } from "@/lib/review-report";

const INTENTS: { value: Intent; label: string; hint: string }[] = [
  { value: "unintended", label: "Unintended", hint: "A regression: fix the code, then rerun the same probe." },
  { value: "intended", label: "Intended", hint: "A deliberate change: explain why in the rationale." },
  { value: "unresolved", label: "Unresolved", hint: "Not sure yet: it stays open for review." },
];

interface Draft {
  intent: Intent | "";
  rationale: string;
}

const EMPTY_DRAFT: Draft = { intent: "", rationale: "" };

/** The disposition to record, or null while the form is incomplete (validate_and_save's rationale rule). */
function readyIntent(draft: Draft): Intent | null {
  if (!draft.intent) return null;
  return draft.intent === "intended" && draft.rationale.trim().length < MIN_RATIONALE ? null : draft.intent;
}

type SaveState = { status: "saved"; files: SavedFile[] } | { status: "error"; message: string } | null;

function useDecisionSave(report: ReviewReport) {
  const saver = useDecisionSaver();
  const [save, setSave] = useState<SaveState>(null);
  const run = async (items: DecisionInput[]) => {
    try {
      setSave({ status: "saved", files: await saver.save(report, items) });
    } catch (error: unknown) {
      setSave({ status: "error", message: error instanceof Error ? error.message : "The decision could not be saved." });
    }
  };
  return { verb: saver.verb, save, run, clear: () => setSave(null) };
}

function SaveStatus({ state }: { state: SaveState }) {
  if (!state) return null;
  if (state.status === "error") {
    return (
      <p role="alert" className="flex items-center gap-2 text-sm text-danger">
        <CircleAlert aria-hidden="true" className="size-4 shrink-0" />{state.message}
      </p>
    );
  }
  const [only] = state.files;
  const many = state.files.length > 1;
  const written = state.files.every((file) => file.path);
  const names = state.files.map((file, i) => <span key={file.id}>{i ? ", " : ""}<code>{file.path ?? `${file.id}.json`}</code></span>);
  return (
    <p role="status" className="max-w-prose text-sm text-muted-foreground">
      {written ? "Saved" : "Downloaded"} {names} as {many ? "proposed decisions" : "a proposed decision"}
      {!many && only.supersedes ? <> that supersedes <code>{only.supersedes}</code></> : null}.{" "}
      {written ? (
        <>Commit {many ? "them" : "it"}: <code>git add {state.files.map((file) => file.path).join(" ")}</code>;</>
      ) : (
        <>Commit {many ? "them" : "it"} to <code>.behavior-review/decisions/</code> (or <code>behavior_decisions/</code>) on this PR's branch;</>
      )}{" "}
      {many ? "they count" : "it counts"} as approved once the PR is merged.
    </p>
  );
}

function IntentChoice({ id, intent, onChange }: { id: string; intent: Intent | ""; onChange: (intent: Intent) => void }) {
  return (
    <RadioGroup value={intent} onValueChange={(value) => onChange(value as Intent)} aria-label={`Disposition for probe ${id}`}>
      {INTENTS.map((option) => (
        <div key={option.value} className="flex items-start gap-3">
          <RadioGroupItem value={option.value} id={`${id}-${option.value}`} className="mt-0.5" />
          <Label htmlFor={`${id}-${option.value}`} className="flex-col items-start gap-0.5">
            <span>{option.label}</span>
            <span className="text-xs font-normal text-muted-foreground">{option.hint}</span>
          </Label>
        </div>
      ))}
    </RadioGroup>
  );
}

interface DraftState {
  value: Draft;
  set: (next: Draft) => void;
}

function DeltaDecision({ report, comparison, draft }: { report: ReviewReport; comparison: Comparison; draft: DraftState }) {
  const { verb, save, run, clear } = useDecisionSave(report);
  const id = comparison.probe.id;
  const { intent, rationale } = draft.value;
  const ready = readyIntent(draft.value);
  const update = (next: Partial<Draft>) => {
    draft.set({ ...draft.value, ...next });
    clear();
  };
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (ready) void run([{ comparison, intent: ready, rationale }]);
  };
  return (
    <form className="space-y-3" onSubmit={submit}>
      <p className="text-sm">
        <code className="font-semibold">{comparison.probe.target.symbol}</code> via probe <code>{id}</code>:{" "}
        <code>{formatValue(comparison.base.output)}</code> → <code>{formatValue(comparison.head.output)}</code>
      </p>
      <IntentChoice id={id} intent={intent} onChange={(next) => update({ intent: next })} />
      <div className="space-y-1">
        <Label htmlFor={`${id}-rationale`}>
          Rationale {intent === "intended" ? `(required, at least ${MIN_RATIONALE} characters)` : "(optional)"}
        </Label>
        <Textarea
          id={`${id}-rationale`}
          value={rationale}
          onChange={(event) => update({ rationale: event.target.value })}
          aria-invalid={intent === "intended" && !ready}
          placeholder="Why is this behavior change correct?"
        />
      </div>
      <Button type="submit" disabled={!ready || report.fixture}>
        {verb === "Save" ? <Save aria-hidden="true" /> : <Download aria-hidden="true" />}{verb} decision
      </Button>
      <SaveStatus state={save} />
    </form>
  );
}

function SaveAll({ report, ready, total }: { report: ReviewReport; ready: DecisionInput[]; total: number }) {
  const { verb, save, run } = useDecisionSave(report);
  return (
    <div className="space-y-2">
      <Button variant="outline" disabled={!ready.length || report.fixture} onClick={() => void run(ready)}>
        {verb === "Save" ? <Save aria-hidden="true" /> : <Download aria-hidden="true" />}
        {verb} all ready decisions ({ready.length} of {total})
      </Button>
      {verb === "Download" ? (
        <p className="text-xs text-muted-foreground">One file per decision; your browser may ask to allow several downloads.</p>
      ) : null}
      <SaveStatus state={save} />
    </div>
  );
}

function LedgerDecision({ decision }: { decision: Decision }) {
  return (
    <li className="space-y-1 rounded-md border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <code className="text-sm font-semibold">{decision.target.symbol}</code>
        <Badge variant="outline" className={TONE_CLASSES.info}>{decision.intent}</Badge>
        <Badge variant="outline" className={TONE_CLASSES[decision.status === "approved" ? "success" : "neutral"]}>{decision.status}</Badge>
      </div>
      {decision.rationale ? <p className="max-w-prose text-sm">{decision.rationale}</p> : null}
      <p className="text-xs break-all text-muted-foreground">
        {decision.target.path} · decision <code>{decision.id}</code>
        {decision.supersedes ? <> · replaces <code>{decision.supersedes}</code></> : null}
        {decision.requirement_ref ? ` · ${decision.requirement_ref}` : ""}
      </p>
    </li>
  );
}

function LedgerSection({ title, decisions, empty }: { title: string; decisions: Decision[]; empty: string }) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className="space-y-3">
      <h3 id={headingId} className="font-medium">{title}</h3>
      {decisions.length ? (
        <ul className="space-y-2">{decisions.map((d) => <LedgerDecision key={d.id} decision={d} />)}</ul>
      ) : (
        <p className="text-sm text-muted-foreground">{empty}</p>
      )}
    </section>
  );
}

function PendingDecisions({ report, deltas }: { report: ReviewReport; deltas: Comparison[] }) {
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const draftOf = (comparison: Comparison) => drafts[comparison.probe.id] ?? EMPTY_DRAFT;
  const ready = deltas.flatMap((comparison) => {
    const intent = readyIntent(draftOf(comparison));
    return intent ? [{ comparison, intent, rationale: draftOf(comparison).rationale }] : [];
  });
  if (!deltas.length) return <p className="text-sm text-muted-foreground">No behavior difference in this report needs a decision.</p>;
  return (
    <>
      {report.fixture ? <p className="text-sm text-muted-foreground">A fixture report cannot produce ledger records.</p> : null}
      {deltas.length > 1 ? <SaveAll report={report} ready={ready} total={deltas.length} /> : null}
      {deltas.map((comparison) => (
        <div key={comparison.probe.id} className="space-y-6">
          <Separator />
          <DeltaDecision
            report={report}
            comparison={comparison}
            draft={{ value: draftOf(comparison), set: (next) => setDrafts((all) => ({ ...all, [comparison.probe.id]: next })) }}
          />
        </div>
      ))}
    </>
  );
}

function LedgerNote() {
  const { verb } = useDecisionSaver();
  return (
    <Alert>
      <Info aria-hidden="true" />
      <AlertTitle>{verb === "Save" ? "Saving proposes; only merging approves" : "This page saves nothing and cannot approve anything"}</AlertTitle>
      <AlertDescription>
        {verb === "Save" ? "Save writes" : "Download writes"} the ledger record <code>&lt;id&gt;.json</code> for{" "}
        <code>.behavior-review/decisions/</code> (<code>behavior_decisions/</code> in older layouts). Commit it on the PR's
        branch: it is proposed there, and counts as approved once that PR is merged.
      </AlertDescription>
    </Alert>
  );
}

export function DecisionPanel({ report }: { report: ReviewReport }) {
  const deltas = report.comparisons.filter((c) => c.outcome === "delta_observed");
  return (
    <Card>
      <CardHeader>
        <CardTitle><h2 className="text-lg font-semibold">Decision</h2></CardTitle>
        <CardDescription>Every behavior difference needs a human disposition.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <LedgerNote />
        <PendingDecisions report={report} deltas={deltas} />
        <Separator />
        {report.decisions.length ? (
          <LedgerSection title="Committed in this PR (proposed until merged)" decisions={report.decisions} empty="" />
        ) : null}
        <LedgerSection
          title="Prior decisions from the ledger"
          decisions={report.prior_decisions}
          empty="No earlier decision covers these symbols."
        />
      </CardContent>
    </Card>
  );
}
