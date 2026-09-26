import { createContext, useContext } from "react";
import { buildDecisions, downloadDecisions, type DecisionInput } from "@/lib/decision-file";
import type { LocalApi, SavedDecision } from "@/lib/local-api";
import type { ReviewReport } from "@/lib/review-report";

// Where a decision goes: downloaded as a file (static site) or saved into the repository by the
// local server (`behavior-review ui`). The Decision panel is the same either way.

export interface SavedFile {
  id: string;
  supersedes: string | null;
  /** Repository-relative, when the local server wrote it. */
  path?: string;
}

export interface DecisionSaver {
  verb: "Download" | "Save";
  save: (report: ReviewReport, inputs: DecisionInput[]) => Promise<SavedFile[]>;
}

const downloadSaver: DecisionSaver = {
  verb: "Download",
  async save(report, inputs) {
    const decisions = await buildDecisions(report, inputs);
    await downloadDecisions(decisions);
    return decisions.map((d) => ({ id: d.id, supersedes: d.supersedes }));
  },
};

/** Saved one at a time, so two decisions on one function chain like files saved in order. */
export function localSaver(api: LocalApi, runId: string): DecisionSaver {
  return {
    verb: "Save",
    async save(_report, inputs) {
      const saved: SavedFile[] = [];
      for (const { comparison, intent, rationale } of inputs) {
        const { decision, path } = await api.post<SavedDecision>("/api/decisions", {
          run_id: runId, probe_id: comparison.probe.id, intent, rationale: rationale.trim() || null,
        });
        saved.push({ id: decision.id, supersedes: decision.supersedes, path });
      }
      return saved;
    },
  };
}

export const DecisionSaverContext = createContext<DecisionSaver>(downloadSaver);
export const useDecisionSaver = () => useContext(DecisionSaverContext);
