import { useEffect, useMemo, useState } from "react";
import { CircleAlert, CircleCheck, Laptop, LoaderCircle, Play } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ReportViews, useViewTab } from "@/components/ReportViews";
import { LoadingState } from "@/components/ReportStates";
import { TONE_CLASSES } from "@/components/StatusBadge";
import { DecisionSaverContext, localSaver } from "@/lib/decision-saver";
import type { Branch, LocalApi, LocalRepo, RunMeta, RunStep } from "@/lib/local-api";
import type { LocalView } from "@/lib/use-local-runs";

// Local mode (`behavior-review ui`): the review history of this repository, a dialog that starts a
// review on this computer and follows its steps, and decisions saved straight into the ledger.

function runLabel(run: RunMeta) {
  const time = new Date(run.started_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  return `${run.base} → ${run.head} · ${time} · ${run.status}`;
}

export function LocalBanner({ repo }: { repo: LocalRepo }) {
  return (
    <Alert className={TONE_CLASSES.info}>
      <Laptop aria-hidden="true" />
      <AlertTitle>Local · {repo.repo} · on {repo.branch}</AlertTitle>
      <AlertDescription>
        Reviews run on this computer and are kept in <code>.behavior-review/runs/</code>; saved decisions go into the ledger for you to commit.
      </AlertDescription>
    </Alert>
  );
}

export function RunPicker({ runs, current, onSelect }: { runs: RunMeta[]; current: string | null; onSelect: (id: string) => void }) {
  if (!runs.length) return null;
  return (
    <Select value={current ?? undefined} onValueChange={onSelect}>
      <SelectTrigger aria-label="Review to show" className="w-full min-w-0 sm:w-auto sm:max-w-96">
        <SelectValue placeholder="Choose a review" />
      </SelectTrigger>
      <SelectContent>
        {runs.map((run) => <SelectItem key={run.id} value={run.id}>{runLabel(run)}</SelectItem>)}
      </SelectContent>
    </Select>
  );
}

function Steps({ steps, status }: { steps: RunStep[]; status: RunMeta["status"] | "starting" }) {
  return (
    <ol aria-label="Review steps" className="space-y-1 text-sm">
      {steps.map((step, index) => (
        <li key={`${step.step}-${index}`} className="flex items-start gap-2">
          <CircleCheck aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-success" />
          <span><span className="font-medium">{step.step}</span> <span className="text-muted-foreground">{step.detail}</span></span>
        </li>
      ))}
      {status === "running" || status === "starting" ? (
        <li className="flex items-center gap-2 text-muted-foreground">
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />Working…
        </li>
      ) : null}
    </ol>
  );
}

/** A run's steps as they happen, starting from what already ran. */
function useFollow(api: LocalApi, run: RunMeta | null) {
  const [steps, setSteps] = useState<RunStep[]>(run?.steps ?? []);
  const [final, setFinal] = useState<RunMeta | null>(run && run.status !== "running" ? run : null);
  useEffect(() => {
    if (!run || run.status !== "running") return;
    setSteps([]);
    return api.follow(run.id, (step) => setSteps((all) => [...all, step]), setFinal);
  }, [api, run]);
  return { steps, final };
}

function BranchSelect({ id, label, value, branches, onChange }: {
  id: string; label: string; value: string; branches: Branch[]; onChange: (value: string) => void;
}) {
  return (
    <div className="space-y-1">
      <Label htmlFor={id}>{label}</Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger id={id} className="w-full"><SelectValue /></SelectTrigger>
        <SelectContent>
          {branches.map((branch) => <SelectItem key={branch.name} value={branch.name}>{branch.name} · {branch.sha}</SelectItem>)}
        </SelectContent>
      </Select>
    </div>
  );
}

function RunProgress({ api, run, onShow }: { api: LocalApi; run: RunMeta; onShow: (id: string) => void }) {
  const { steps, final } = useFollow(api, run);
  return (
    <div className="space-y-3">
      <Steps steps={steps} status={final?.status ?? "running"} />
      {final?.status === "failed" ? (
        <Alert variant="destructive"><CircleAlert aria-hidden="true" /><AlertTitle>The review failed</AlertTitle><AlertDescription>{final.error}</AlertDescription></Alert>
      ) : null}
      {final?.status === "done" ? (
        <DialogClose asChild><Button onClick={() => onShow(run.id)}>Show the review</Button></DialogClose>
      ) : null}
    </div>
  );
}

export function NewReviewDialog({ api, repo, onStarted }: { api: LocalApi; repo: LocalRepo; onStarted: (id: string) => void }) {
  const [branches, setBranches] = useState<Branch[]>([]);
  const [base, setBase] = useState(repo.default_base);
  const [head, setHead] = useState(repo.branch);
  const [run, setRun] = useState<RunMeta | null>(null);
  const [error, setError] = useState<string>();
  const load = () => void api.get<Branch[]>("/api/branches").then(setBranches).catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  const start = async () => {
    try {
      setError(undefined);
      const { id } = await api.post<{ id: string }>("/api/runs", { base, head });
      setRun(await api.get<RunMeta>(`/api/runs/${id}`));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <Dialog onOpenChange={(open) => (open ? load() : setRun(null))}>
      <DialogTrigger asChild><Button size="sm"><Play aria-hidden="true" />New review</Button></DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New review</DialogTitle>
          <DialogDescription>Runs this repository's tests and probes on this computer, on both branches.</DialogDescription>
        </DialogHeader>
        {run ? (
          <RunProgress api={api} run={run} onShow={onStarted} />
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            <BranchSelect id="review-base" label="Base (before)" value={base} branches={branches} onChange={setBase} />
            <BranchSelect id="review-head" label="Head (after)" value={head} branches={branches} onChange={setHead} />
          </div>
        )}
        {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
        {run ? null : (
          <DialogFooter>
            <DialogClose asChild><Button variant="outline">Cancel</Button></DialogClose>
            <Button onClick={() => void start()} disabled={!base || !head}>Start review</Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function LocalRunView({ api, view, onRefresh }: { api: LocalApi; view: LocalView; onRefresh: () => void }) {
  const tab = useViewTab();
  const saver = useMemo(() => (view.status === "ready" ? localSaver(api, view.meta.id) : null), [api, view]);
  if (view.status === "loading") return <LoadingState />;
  if (view.status === "error") {
    return <Alert variant="destructive"><CircleAlert aria-hidden="true" /><AlertTitle>The review could not be shown</AlertTitle><AlertDescription>{view.message}</AlertDescription></Alert>;
  }
  if (view.status === "empty") {
    return (
      <Empty className="border">
        <EmptyHeader>
          <EmptyMedia variant="icon"><Play aria-hidden="true" /></EmptyMedia>
          <EmptyTitle>No reviews yet</EmptyTitle>
          <EmptyDescription>Start one with <strong>New review</strong>: pick the branch you're merging into and the one with your change.</EmptyDescription>
        </EmptyHeader>
      </Empty>
    );
  }
  if (view.status !== "ready") {
    return (
      <div className="space-y-3">
        <h2 className="font-medium">{runLabel(view.meta)}</h2>
        {view.status === "failed" ? <Alert variant="destructive"><CircleAlert aria-hidden="true" /><AlertTitle>The review failed</AlertTitle><AlertDescription>{view.meta.error}</AlertDescription></Alert> : null}
        {view.status === "running" ? <Steps steps={view.meta.steps} status="running" /> : null}
        {view.status === "running" ? <Button variant="outline" size="sm" onClick={onRefresh}>Refresh</Button> : null}
      </div>
    );
  }
  return (
    <DecisionSaverContext.Provider value={saver ?? localSaver(api, view.meta.id)}>
      <ReportViews report={view.report} map={view.map} view={tab} />
    </DecisionSaverContext.Provider>
  );
}
