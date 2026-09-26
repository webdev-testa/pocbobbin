import { useId, useState, type FormEvent } from "react";
import { CircleAlert, Download, Info } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { TONE_CLASSES } from "@/components/StatusBadge";
import { buildDecisions, downloadDecisions, MIN_RATIONALE, type DecisionInput } from "@/lib/decision-file";
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

type SaveState = { status: "saved"; decisions: Decision[] } | { status: "error"; message: string } | null;

function useDecisionDownload(report: ReviewReport) {
  const [save, setSave] = useState<SaveState>(null);
  const download = async (items: DecisionInput[]) => {
    try {
      const decisions = await buildDecisions(report, items);
      await downloadDecisions(decisions);
      setSave({ status: "saved", decisions });
    } catch (error: unknown) {
      setSave({ status: "error", message: error instanceof Error ? error.message : "The decision file could not be built." });
    }
  };
  return { save, download, clear: () => setSave(null) };
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
  const [only] = state.decisions;
  const many = state.decisions.length > 1;
  return (
    <p role="status" className="max-w-prose text-sm text-muted-foreground">
      Downloaded {state.decisions.map((d, i) => <span key={d.id}>{i ? ", " : ""}<code>{d.id}.json</code></span>)} as{" "}
      {many ? "proposed decisions" : "a proposed decision"}
      {!many && only.supersedes ? <> that supersedes <code>{only.supersedes}</code></> : null}. Commit {many ? "them" : "it"} to{" "}
      <code>behavior_decisions/</code> on this PR's branch; {many ? "they count" : "it counts"} as approved once the PR is merged.
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
  const { save, download, clear } = useDecisionDownload(report);
  const id = comparison.probe.id;
  const { intent, rationale } = draft.value;
  const ready = readyIntent(draft.value);
  const update = (next: Partial<Draft>) => {
    draft.set({ ...draft.value, ...next });
    clear();
  };
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (ready) void download([{ comparison, intent: ready, rationale }]);
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
        <Download aria-hidden="true" />Download decision
      </Button>
      <SaveStatus state={save} />
    </form>
  );
}

function DownloadAll({ report, ready, total }: { report: ReviewReport; ready: DecisionInput[]; total: number }) {
  const { save, download } = useDecisionDownload(report);
  return (
    <div className="space-y-2">
      <Button variant="outline" disabled={!ready.length || report.fixture} onClick={() => void download(ready)}>
        <Download aria-hidden="true" />Download all ready decisions ({ready.length} of {total})
      </Button>
      <p className="text-xs text-muted-foreground">One file per decision; your browser may ask to allow several downloads.</p>
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
      {deltas.length > 1 ? <DownloadAll report={report} ready={ready} total={deltas.length} /> : null}
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

export function DecisionPanel({ report }: { report: ReviewReport }) {
  const deltas = report.comparisons.filter((c) => c.outcome === "delta_observed");
  return (
    <Card>
      <CardHeader>
        <CardTitle><h2 className="text-lg font-semibold">Decision</h2></CardTitle>
        <CardDescription>Every behavior difference needs a human disposition.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <Alert>
          <Info aria-hidden="true" />
          <AlertTitle>This page saves nothing and cannot approve anything</AlertTitle>
          <AlertDescription>
            Download writes the ledger record <code>behavior_decisions/&lt;id&gt;.json</code>. Commit it on the PR's branch: it is
            proposed there, and counts as approved once that PR is merged.
          </AlertDescription>
        </Alert>
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
