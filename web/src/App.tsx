import { useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { LocalBanner, LocalRunView, NewReviewDialog, RunPicker } from "@/components/LocalReviews";
import { OpenReportDialog } from "@/components/OpenReportDialog";
import { PrPicker, prLabel } from "@/components/PrPicker";
import { ErrorState, LoadingState, OpenedNotice, UnknownPrState } from "@/components/ReportStates";
import { ReportViews, useViewTab } from "@/components/ReportViews";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { LocalApi, LocalRepo } from "@/lib/local-api";
import type { OpenedReport } from "@/lib/open-report";
import { useReportSource, type ReportSource } from "@/lib/report-source";
import { useLocalMode, useLocalRuns } from "@/lib/use-local-runs";
import { useTheme } from "@/lib/use-theme";

type ThemeControl = ReturnType<typeof useTheme>;

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

const Main = ({ children }: { children: React.ReactNode }) => (
  <main className="mx-auto max-w-6xl space-y-6 px-4 py-6 sm:px-6 sm:py-8">{children}</main>
);

/** The static site: published PR runs, or files opened from this computer. */
function StaticApp({ themeControl }: { themeControl: ThemeControl }) {
  const source = useReportSource();
  const [opened, setOpened] = useState<OpenedReport | null>(null);
  const selected = source.status === "ready" ? source.pr : null;
  const picker = !opened && source.entries ? <PrPicker entries={source.entries} selected={selected} onSelect={source.select} /> : null;
  return (
    <>
      <AppHeader themeControl={themeControl} picker={picker}>
        <OpenReportDialog onOpen={setOpened} />
      </AppHeader>
      <Main>
        {opened ? (
          <OpenedReportViews key={`${opened.names.join()}:${opened.report.generated_at}`} opened={opened} onClose={() => setOpened(null)} />
        ) : (
          <PublishedReports source={source} />
        )}
      </Main>
    </>
  );
}

/** `behavior-review ui`: this repository's review history, new reviews, and decisions saved into it. */
function LocalApp({ themeControl, api, repo }: { themeControl: ThemeControl; api: LocalApi; repo: LocalRepo }) {
  const { runs, current, view, select, refresh } = useLocalRuns(api);
  const show = (id: string) => {
    void refresh();
    select(id);
  };
  return (
    <>
      <AppHeader themeControl={themeControl} picker={<RunPicker runs={runs} current={current} onSelect={select} />}>
        <NewReviewDialog api={api} repo={repo} onStarted={show} />
      </AppHeader>
      <Main>
        <LocalBanner repo={repo} />
        <LocalRunView api={api} view={view} onRefresh={() => void refresh()} />
      </Main>
    </>
  );
}

export default function App() {
  const themeControl = useTheme();
  const mode = useLocalMode();
  return (
    <TooltipProvider>
      {mode.status === "checking" ? <Main><LoadingState /></Main> : null}
      {mode.status === "static" ? <StaticApp themeControl={themeControl} /> : null}
      {mode.status === "local" ? <LocalApp themeControl={themeControl} api={mode.api} repo={mode.repo} /> : null}
    </TooltipProvider>
  );
}
