import type { Comparison, Decision, Intent, Observation, ReviewReport } from "@/lib/review-report";

// Builds the same ledger record as app/decisions.py `validate_and_save`, so a downloaded file
// can be committed to behavior_decisions/ unchanged and read back by `lookup`.

/** validate_and_save rejects an "intended" decision with a shorter rationale. */
export const MIN_RATIONALE = 10;

export interface DecisionInput {
  comparison: Comparison;
  intent: Intent;
  rationale: string;
}

async function decisionId(parts: string[]): Promise<string> {
  if (!globalThis.crypto?.subtle) throw new Error("Building the decision id needs a secure page (https or localhost).");
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(parts.map((part) => part.trim()).join(":")));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("").slice(0, 12);
}

const observed = (observation: Observation) => observation.exception ?? observation.output;

/**
 * Same rule as `current_decision` in app/decisions.py: the record no other record supersedes.
 * Ids are hashes, so their order says nothing about age; ties fall back to the last by id.
 */
function currentDecision(records: Decision[], path: string, symbol: string, exclude: string): Decision | undefined {
  const same = records.filter((d) => d.target.path === path && d.target.symbol === symbol && d.id !== exclude);
  const replaced = new Set(same.flatMap((d) => (d.supersedes ? [d.supersedes] : [])));
  return same.filter((d) => !replaced.has(d.id)).sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)).pop();
}

async function buildDecision(report: ReviewReport, ledger: Decision[], input: DecisionInput): Promise<Decision> {
  const { repo, revisions: { base_sha, head_sha } } = report;
  const { comparison, intent, rationale } = input;
  const { path, symbol } = comparison.probe.target;
  const id = await decisionId([repo, symbol, base_sha, head_sha, comparison.probe.hash]);
  return {
    id,
    repo,
    target: { path, symbol },
    base_sha,
    head_sha,
    probe_hash: comparison.probe.hash,
    before: observed(comparison.base),
    after: observed(comparison.head),
    intent,
    rationale: rationale.trim() || null,
    requirement_ref: null,
    // Approval comes only from merging the file to main, never from this page.
    status: "proposed",
    supersedes: currentDecision(ledger, path, symbol, id)?.id ?? null,
  };
}

/**
 * The ledger as the repository will hold it: merged records plus this PR's own, which replace a
 * merged record of the same id. Built one at a time, like saving files one by one in Python, so
 * two decisions on one function in a batch chain instead of both replacing the same record.
 */
export async function buildDecisions(report: ReviewReport, inputs: DecisionInput[]): Promise<Decision[]> {
  const ledger = new Map([...report.prior_decisions, ...report.decisions].map((d) => [d.id, d]));
  const built: Decision[] = [];
  for (const input of inputs) {
    const decision = await buildDecision(report, [...ledger.values()], input);
    ledger.set(decision.id, decision);
    built.push(decision);
  }
  return built;
}

function downloadDecision(decision: Decision) {
  const blob = new Blob([`${JSON.stringify(decision, null, 2)}\n`], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${decision.id}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url));
}

/** Browsers drop some of several downloads started in the same tick, so they are spaced out. */
export async function downloadDecisions(decisions: Decision[]) {
  for (const [index, decision] of decisions.entries()) {
    if (index) await new Promise((resolve) => setTimeout(resolve, 250));
    downloadDecision(decision);
  }
}
