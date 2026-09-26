// Mirrors `ReviewReport` in app/schemas.py. The viewer reads this shape as-is and never fills in
// missing evidence; a report that doesn't match is rejected with a readable error.

export type Revision = "base" | "head";
export type Outcome = "same_on_tested_cases" | "delta_observed" | "pre_existing_failure" | "inconclusive";
export type RunStatus = "ok" | "exception" | "error" | "timeout";
export type Intent = "unintended" | "intended" | "unresolved";
export type DecisionStatus = "proposed" | "approved" | "stale" | "superseded";
export type Tier = "full" | "static_probe" | "static_cross_file" | "experimental";

export interface SymbolRef {
  path: string;
  symbol: string;
}

export interface ChangedSymbol extends SymbolRef {
  tags: string[];
  base_line: number | null;
  head_line: number | null;
}

export interface Edge {
  caller: SymbolRef;
  callee: SymbolRef;
  line: number;
  revisions: Revision[];
}

export interface Hop extends SymbolRef {
  line: number;
}

export interface ImpactPath {
  hops: Hop[];
  outside_diff: boolean;
  is_test: boolean;
}

export interface Unknown {
  path: string;
  line: number;
  symbol: string;
  expression: string;
  reason: string;
  may_reach: string[];
}

export interface Observation {
  revision: Revision;
  sha: string;
  status: RunStatus;
  output: unknown;
  exception: string | null;
  duration_ms: number | null;
}

export interface Comparison {
  probe: { id: string; target: SymbolRef; input: Record<string, unknown>; hash: string; authored_by: string };
  base: Observation;
  head: Observation;
  outcome: Outcome;
  ran_at: string;
  reruns: string | null;
}

export interface SuiteRun {
  revision: Revision;
  sha: string;
  status: RunStatus;
  passed: number;
  failed: number;
  errors: number;
  suite_hash: string;
}

export interface Decision {
  id: string;
  repo: string;
  target: SymbolRef;
  base_sha: string;
  head_sha: string;
  probe_hash: string;
  before: unknown;
  after: unknown;
  intent: Intent;
  rationale: string | null;
  requirement_ref: string | null;
  status: DecisionStatus;
  supersedes: string | null;
}

export interface LanguageSupport {
  language: string;
  adapter: string;
  tier: Tier;
}

/** What executed the project's tests and probes; only present after a --run. */
export interface Runtime {
  /** Repository-relative, or only the file name; null when no Python command ran. */
  python: string | null;
  version: string | null;
  /** How the interpreter was chosen: flag, config, virtual_env, venv or fallback. */
  source: string | null;
  probe_runner: { path: string; source: "base" | "packaged"; sha256: string } | null;
}

export interface Analysis extends LanguageSupport {
  config_source: string;
  languages: LanguageSupport[];
  runtime?: Runtime | null;
}

export interface ReviewReport {
  schema_version: string;
  fixture: boolean;
  generated_at: string;
  repo: string;
  revisions: { base_ref: string; head_ref: string; base_sha: string; head_sha: string; changed_files: string[] };
  analysis: Analysis | null;
  impact: { changed_symbols: ChangedSymbol[]; edges: Edge[]; paths: ImpactPath[]; unknowns: Unknown[]; max_hops: number };
  tests: SuiteRun[];
  comparisons: Comparison[];
  needs_bob_action: SymbolRef[];
  decisions: Decision[];
  prior_decisions: Decision[];
  limits: string[];
  links: Record<string, string>;
}

export class ReportError extends Error {}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Rejects anything that isn't a ReviewReport, and fills only the fields the schema itself defaults. */
export function parseReport(value: unknown): ReviewReport {
  if (!isRecord(value)) throw new ReportError("The report is not a JSON object.");
  if (typeof value.fixture !== "boolean") throw new ReportError("The report has no boolean `fixture` marker, so its provenance is unknown.");
  if (!isRecord(value.revisions) || typeof value.revisions.base_sha !== "string" || typeof value.revisions.head_sha !== "string") {
    throw new ReportError("The report does not name its base and head commits.");
  }
  if (!isRecord(value.impact) || !Array.isArray(value.impact.paths) || !Array.isArray(value.impact.changed_symbols)) {
    throw new ReportError("The report has no impact analysis.");
  }
  const report = value as unknown as ReviewReport;
  return {
    ...report,
    analysis: report.analysis ?? null,
    impact: { ...report.impact, edges: report.impact.edges ?? [], unknowns: report.impact.unknowns ?? [] },
    tests: report.tests ?? [],
    comparisons: report.comparisons ?? [],
    needs_bob_action: report.needs_bob_action ?? [],
    decisions: report.decisions ?? [],
    prior_decisions: report.prior_decisions ?? [],
    limits: report.limits ?? [],
    links: report.links ?? {},
  };
}

export async function fetchJson(url: string, signal?: AbortSignal): Promise<unknown | null> {
  const response = await fetch(url, { signal, headers: { Accept: "application/json" } });
  // Static hosts often answer a missing file with the SPA's index.html instead of a 404.
  if (response.status === 404 || !(response.headers.get("content-type") ?? "").includes("json")) return null;
  if (!response.ok) throw new ReportError(`Loading ${url} failed with HTTP ${response.status}.`);
  try {
    return await response.json();
  } catch {
    throw new ReportError(`${url} is not valid JSON.`);
  }
}

export const symbolKey = (ref: SymbolRef) => `${ref.path}::${ref.symbol}`;
export const shortSha = (sha: string) => sha.slice(0, 7);
