import type { ReactNode } from "react";
import { ExternalLink, TriangleAlert } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { StatusBadge, TONE_CLASSES } from "@/components/StatusBadge";
import { TierBadge } from "@/components/TierBadge";
import { tierLimit, verdictCounts, type VerdictCounts } from "@/lib/evidence";
import { shortSha, type ReviewReport, type Runtime } from "@/lib/review-report";

const VERDICT_ORDER: (keyof VerdictCounts)[] = ["behavior_differs", "same", "inconclusive", "needs_probe", "unknown_edge"];

function formatTime(iso: string) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function FixtureBanner() {
  return (
    <Alert variant="destructive">
      <TriangleAlert aria-hidden="true" />
      <AlertTitle>FIXTURE — not real evidence</AlertTitle>
      <AlertDescription>This report is a hand-written example. Nothing on this page came from a real run.</AlertDescription>
    </Alert>
  );
}

function LanguageLine({ report }: { report: ReviewReport }) {
  const languages = report.analysis?.languages.length ? report.analysis.languages : report.analysis ? [report.analysis] : [];
  if (!languages.length) {
    return <Badge variant="outline" className={TONE_CLASSES.neutral}>Language not reported</Badge>;
  }
  const limits = languages.map((support) => tierLimit(report, support.language)).filter((limit): limit is string => !!limit);
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {languages.map((support) => <TierBadge key={support.language} support={support} />)}
      </div>
      {limits.map((limit) => (
        <p key={limit} className="max-w-prose text-sm text-muted-foreground">{limit}</p>
      ))}
    </div>
  );
}

const PYTHON_SOURCE: Record<string, string> = {
  flag: "set with --python",
  config: "set in the config",
  virtual_env: "the active virtual environment",
  venv: "the repository's venv",
  fallback: "behavior-review's own; see limits",
};

/** "Ran with .venv/bin/python 3.12.4 (the repository's venv) · probe runner tools/run_probe.py (packaged)". */
function RuntimeLine({ runtime }: { runtime: Runtime | null | undefined }) {
  const parts: ReactNode[] = [];
  if (runtime?.python) {
    parts.push(
      <span key="python">
        <code className="text-foreground">{runtime.python}</code>{runtime.version ? ` ${runtime.version}` : ""}
        {runtime.source ? ` (${PYTHON_SOURCE[runtime.source] ?? runtime.source})` : ""}
      </span>,
    );
  }
  if (runtime?.probe_runner) {
    parts.push(
      <span key="runner">
        probe runner <code className="text-foreground">{runtime.probe_runner.path}</code> ({runtime.probe_runner.source})
      </span>,
    );
  }
  if (!parts.length) return null;
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted-foreground">
      <span>Ran with</span>
      {parts.map((part, index) => [index ? <span key={`dot-${index}`} aria-hidden="true">·</span> : null, part])}
    </p>
  );
}

export function SummaryHeader({ report }: { report: ReviewReport }) {
  const counts = verdictCounts(report);
  const actionRun = report.links.action_run?.startsWith("https://") ? report.links.action_run : undefined;
  return (
    <section aria-labelledby="summary-heading" className="space-y-4">
      {report.fixture ? <FixtureBanner /> : null}
      <Card>
        <CardHeader className="gap-3">
          <p className="text-sm text-muted-foreground">Behavior review</p>
          <h1 id="summary-heading" className="text-2xl font-semibold tracking-tight break-words">{report.repo}</h1>
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted-foreground">
            <span>base <code className="text-foreground">{shortSha(report.revisions.base_sha)}</code></span>
            <span aria-hidden="true">→</span>
            <span>head <code className="text-foreground">{shortSha(report.revisions.head_sha)}</code></span>
            <span aria-hidden="true">·</span>
            <time dateTime={report.generated_at}>{formatTime(report.generated_at)}</time>
          </p>
          <LanguageLine report={report} />
          <RuntimeLine runtime={report.analysis?.runtime} />
        </CardHeader>
        <Separator />
        <CardContent className="flex flex-col gap-4 pt-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">Verdict</span>
            {VERDICT_ORDER.map((status) => <StatusBadge key={status} status={status} count={counts[status]} />)}
          </div>
          {actionRun ? (
            <Button asChild variant="outline" size="sm">
              <a href={actionRun} target="_blank" rel="noreferrer">
                GitHub Actions run
                <ExternalLink aria-hidden="true" />
              </a>
            </Button>
          ) : (
            <span className="text-sm text-muted-foreground">No Actions run linked</span>
          )}
        </CardContent>
      </Card>
    </section>
  );
}
