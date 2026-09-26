import { lazy, Suspense, useCallback, useMemo, useState } from "react";
import { BehaviorDifferences } from "@/components/BehaviorDifferences";
import { DecisionPanel } from "@/components/DecisionPanel";
import { LimitsCard, TestsCard } from "@/components/TestsAndLimits";
import { NeedsAttention } from "@/components/NeedsAttention";
import { NodeDetailsSheet } from "@/components/NodeDetailsSheet";
import { SummaryHeader } from "@/components/SummaryHeader";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { evidenceNodes } from "@/lib/evidence";
import type { RepoMap } from "@/lib/repo-map";
import type { ReviewReport } from "@/lib/review-report";

// The two tabs every data source shows: the published PRs, an opened file, or a local run.

const EvidenceMap = lazy(() => import("@/components/EvidenceMap"));
const RepoMapTab = lazy(() => import("@/components/RepoMapTab"));

function PrReview({ report }: { report: ReviewReport }) {
  const nodes = useMemo(() => evidenceNodes(report), [report]);
  const [selected, setSelected] = useState<string | undefined>();
  const select = useCallback((key: string) => setSelected(key), []);
  return (
    <div className="space-y-8">
      <SummaryHeader report={report} />
      <section aria-labelledby="map-heading">
        <Suspense fallback={<Skeleton className="h-112 w-full" />}>
          <EvidenceMap report={report} onSelect={select} />
        </Suspense>
      </section>
      <BehaviorDifferences report={report} onSelect={select} />
      <NeedsAttention report={report} />
      <DecisionPanel report={report} />
      <TestsCard report={report} />
      <LimitsCard report={report} />
      <NodeDetailsSheet node={selected ? nodes.get(selected) : undefined} onOpenChange={(open) => !open && setSelected(undefined)} />
    </div>
  );
}

/** The chosen tab outlives a PR (or run) switch, so both maps change together without jumping tabs. */
export function useViewTab() {
  const [tab, setTab] = useState("pr");
  const showEvidence = useCallback(() => {
    setTab("pr");
    requestAnimationFrame(() => document.getElementById("map-heading")?.scrollIntoView({ block: "start" }));
  }, []);
  return { tab, setTab, showEvidence };
}

interface ReportViewsProps {
  report: ReviewReport;
  map: RepoMap | null;
  view: ReturnType<typeof useViewTab>;
}

export function ReportViews({ report, map, view }: ReportViewsProps) {
  return (
    <Tabs value={view.tab} onValueChange={view.setTab} className="gap-6">
      <TabsList aria-label="Views">
        <TabsTrigger value="pr">PR review</TabsTrigger>
        <TabsTrigger value="repo">Repo map</TabsTrigger>
      </TabsList>
      {/* Keyed by run, so a node selected in one review never carries over to another. */}
      <TabsContent value="pr"><PrReview key={report.generated_at} report={report} /></TabsContent>
      <TabsContent value="repo">
        <Suspense fallback={<Skeleton className="h-128 w-full" />}>
          <RepoMapTab report={report} map={map} onShowEvidence={view.showEvidence} />
        </Suspense>
      </TabsContent>
    </Tabs>
  );
}
