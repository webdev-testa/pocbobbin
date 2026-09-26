import { useCallback, useEffect, useState } from "react";
import { detectLocal, type LocalApi, type LocalRepo, type RunMeta } from "@/lib/local-api";
import type { RepoMap } from "@/lib/repo-map";
import type { ReviewReport } from "@/lib/review-report";

export type LocalMode =
  | { status: "checking" }
  | { status: "static" }
  | { status: "local"; api: LocalApi; repo: LocalRepo };

/** Local mode when the page was opened by `behavior-review ui` (it carries a token); else the static site. */
export function useLocalMode(): LocalMode {
  const [mode, setMode] = useState<LocalMode>({ status: "checking" });
  useEffect(() => {
    let current = true;
    void detectLocal().then((local) => current && setMode(local ? { status: "local", ...local } : { status: "static" }));
    return () => {
      current = false;
    };
  }, []);
  return mode;
}

export type LocalView =
  | { status: "loading" }
  | { status: "empty" }
  | { status: "error"; message: string }
  | { status: "running" | "failed"; meta: RunMeta }
  | { status: "ready"; meta: RunMeta; report: ReviewReport; map: RepoMap | null };

const runFromUrl = () => new URLSearchParams(window.location.search).get("run");
const message = (error: unknown) => (error instanceof Error ? error.message : "The local server did not answer.");

/** The review history and the selected run (`?run=<id>`, default: the newest finished one). */
export function useLocalRuns(api: LocalApi) {
  const [runs, setRuns] = useState<RunMeta[] | null>(null);
  const [selected, setSelected] = useState<string | null>(runFromUrl);
  const [view, setView] = useState<LocalView>({ status: "loading" });
  const refresh = useCallback(
    () => api.get<RunMeta[]>("/api/runs").then(setRuns).catch((error: unknown) => setView({ status: "error", message: message(error) })),
    [api],
  );
  useEffect(() => void refresh(), [refresh]);

  const current = selected ?? runs?.find((run) => run.status === "done")?.id ?? runs?.[0]?.id ?? null;
  useEffect(() => {
    if (runs === null) return;
    const meta = runs.find((run) => run.id === current);
    if (!meta) return setView(current ? { status: "error", message: `There is no review ${current} in this repository's history.` } : { status: "empty" });
    if (meta.status !== "done") return setView({ status: meta.status, meta });
    let live = true;
    setView({ status: "loading" });
    api.run(meta.id)
      .then((run) => live && setView({ status: "ready", meta, ...run }))
      .catch((error: unknown) => live && setView({ status: "error", message: message(error) }));
    return () => {
      live = false;
    };
  }, [api, runs, current]);

  const select = useCallback((id: string) => {
    const url = new URL(window.location.href);
    url.searchParams.set("run", id);
    window.history.pushState(null, "", url);
    setSelected(id);
  }, []);

  return { runs: runs ?? [], current, view, select, refresh };
}
