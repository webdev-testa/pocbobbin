import { useCallback, useEffect, useState } from "react";
import { parseRepoMap, type RepoMap } from "@/lib/repo-map";
import { fetchJson, parseReport, ReportError, type ReviewReport } from "@/lib/review-report";

// Where the page's reports come from: a published index of PR runs (public/data/reports/), or,
// without one, the single report in public/data/. Mirrors scripts/build-report-index.mjs.

export interface ReportIndexEntry {
  pr: number;
  title: string;
  head_sha: string;
  base_sha: string;
  generated_at: string;
  action_run: string;
  path: string;
}

export type ReportSource =
  | { status: "loading"; entries: ReportIndexEntry[] | null }
  | { status: "error"; entries: ReportIndexEntry[] | null; message: string }
  | { status: "unknown-pr"; entries: ReportIndexEntry[]; pr: string }
  | { status: "ready"; entries: ReportIndexEntry[] | null; pr: number | null; report: ReviewReport; map: RepoMap | null };

const errorMessage = (error: unknown) => (error instanceof Error ? error.message : "The report could not be loaded.");
const isAbort = (error: unknown) => error instanceof DOMException && error.name === "AbortError";

function parseIndex(value: unknown): ReportIndexEntry[] {
  const reports = (value as { reports?: unknown } | null)?.reports;
  if (!Array.isArray(reports) || !reports.length) throw new ReportError("reports/index.json lists no reports.");
  return reports as ReportIndexEntry[];
}

async function loadRun(folder: string, signal: AbortSignal): Promise<{ report: ReviewReport; map: RepoMap | null }> {
  const [reportJson, mapJson] = await Promise.all([
    fetchJson(`${folder}/report.json`, signal),
    fetchJson(`${folder}/repo_map.json`, signal),
  ]);
  if (reportJson === null) throw new ReportError(`No report was found at ${folder}/report.json.`);
  return { report: parseReport(reportJson), map: mapJson === null ? null : parseRepoMap(mapJson) };
}

const prFromUrl = () => new URLSearchParams(window.location.search).get("pr");

/** `undefined` while loading; `null` when there is no index (single-report layout). */
function useReportIndex(): { entries: ReportIndexEntry[] | null | undefined; error?: string } {
  const [state, setState] = useState<{ entries: ReportIndexEntry[] | null | undefined; error?: string }>({ entries: undefined });
  useEffect(() => {
    const controller = new AbortController();
    fetchJson("/data/reports/index.json", controller.signal)
      .then((json) => setState({ entries: json === null ? null : parseIndex(json) }))
      .catch((error: unknown) => !isAbort(error) && setState({ entries: null, error: errorMessage(error) }));
    return () => controller.abort();
  }, []);
  return state;
}

/** The PR in `?pr=`, kept in sync with the address bar (back/forward included). */
function useSelectedPr(): [string | null, (pr: number | null) => void] {
  const [pr, setPr] = useState(prFromUrl);
  useEffect(() => {
    const onPop = () => setPr(prFromUrl());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const select = useCallback((next: number | null) => {
    const url = new URL(window.location.href);
    if (next === null) url.searchParams.delete("pr");
    else url.searchParams.set("pr", String(next));
    window.history.pushState(null, "", url);
    setPr(next === null ? null : String(next));
  }, []);
  return [pr, select];
}

export function useReportSource(): ReportSource & { select: (pr: number | null) => void } {
  const index = useReportIndex();
  const [requested, select] = useSelectedPr();
  const [source, setSource] = useState<ReportSource>({ status: "loading", entries: null });

  useEffect(() => {
    const { entries } = index;
    if (entries === undefined) return;
    if (index.error) return setSource({ status: "error", entries: null, message: index.error });
    const entry = entries && (requested === null ? entries[0] : entries.find((e) => String(e.pr) === requested));
    if (entries && !entry) return setSource({ status: "unknown-pr", entries, pr: requested ?? "" });
    const controller = new AbortController();
    setSource({ status: "loading", entries });
    loadRun(entry ? `/data/reports/${entry.path}` : "/data", controller.signal)
      .then((run) => setSource({ status: "ready", entries, pr: entry?.pr ?? null, ...run }))
      .catch((error: unknown) => !isAbort(error) && setSource({ status: "error", entries, message: errorMessage(error) }));
    return () => controller.abort();
  }, [index, requested]);

  return { ...source, select };
}
