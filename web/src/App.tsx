import { lazy, Suspense, useCallback, useMemo, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { BehaviorDifferences } from "@/components/BehaviorDifferences";
import { DecisionPanel } from "@/components/DecisionPanel";
import { LimitsCard, TestsCard } from "@/components/TestsAndLimits";
import { NeedsAttention } from "@/components/NeedsAttention";
import { NodeDetailsSheet } from "@/components/NodeDetailsSheet";
import { OpenReportDialog } from "@/components/OpenReportDialog";
import { PrPicker, prLabel } from "@/components/PrPicker";
import { ErrorState, LoadingState, OpenedNotice, UnknownPrState } from "@/components/ReportStates";
import { SummaryHeader } from "@/components/SummaryHeader";
import { Skeleton } from "@/components/ui/skeleton";
import { evidenceNodes } from "@/lib/evidence";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { OpenedReport } from "@/lib/open-report";
import type { RepoMap } from "@/lib/repo-map";
import { useReportSource, type ReportSource } from "@/lib/report-source";
import type { ReviewReport } from "@/lib/review-report";
import { useTheme } from "@/lib/use-theme";

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

/** The chosen tab outlives a PR switch, so both maps change together without jumping tabs. */
function useViewTab() {
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

function ReportViews({ report, map, view }: ReportViewsProps) {
  return (
    <Tabs value={view.tab} onValueChange={view.setTab} className="gap-6">
      <TabsList aria-label="Views">
        <TabsTrigger value="pr">PR review</TabsTrigger>
        <TabsTrigger value="repo">Repo map</TabsTrigger>
      </TabsList>
      {/* Keyed by run, so a node selected in one PR's review never carries over to another. */}
      <TabsContent value="pr"><PrReview key={report.generated_at} report={report} /></TabsContent>
      <TabsContent value="repo">
        <Suspense fallback={<Skeleton className="h-128 w-full" />}>
          <RepoMapTab report={report} map={map} onShowEvidence={view.showEvidence} />
        </Suspense>
      </TabsContent>
    </Tabs>
  );
}

function PublishedReports({ source }: { source: ReportSource & { select: (pr: number | null) => void } }) {
  const view = useViewTab();
  if (source.status === "loading") return <LoadingState />;
  if (source.status === "error") return <ErrorState message={source.message} onRetry={() => window.location.reload()} />;
  if (source.status === "unknown-pr") {
    return <UnknownPrState pr={source.pr} latest={prLabel(source.entries[0])} onShowLatest={() => source.select(null)} />;
  }
  return <ReportViews report={source.report} map={source.map} view={view} />;
}

function OpenedReportViews({ opened, onClose }: { opened: OpenedReport; onClose: () => void }) {
  const view = useViewTab();
  return (
    <>
      <OpenedNotice names={opened.names} onClose={onClose} />
      <ReportViews report={opened.report} map={opened.map} view={view} />
    </>
  );
}

export default function App() {
  const themeControl = useTheme();
  const source = useReportSource();
  const [opened, setOpened] = useState<OpenedReport | null>(null);
  const selected = source.status === "ready" ? source.pr : null;
  const picker = !opened && source.entries ? <PrPicker entries={source.entries} selected={selected} onSelect={source.select} /> : null;

  return (
    <TooltipProvider>
      <AppHeader themeControl={themeControl} picker={picker}>
        <OpenReportDialog onOpen={setOpened} />
      </AppHeader>
      <main className="mx-auto max-w-6xl space-y-6 px-4 py-6 sm:px-6 sm:py-8">
        {opened ? (
          <OpenedReportViews key={`${opened.names.join()}:${opened.report.generated_at}`} opened={opened} onClose={() => setOpened(null)} />
        ) : (
          <PublishedReports source={source} />
        )}
      </main>
    </TooltipProvider>
  );
}
