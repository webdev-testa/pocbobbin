import { parseRepoMap, type RepoMap } from "@/lib/repo-map";
import { parseReport, ReportError, type Decision, type ReviewReport } from "@/lib/review-report";

// The API `behavior-review ui` serves on 127.0.0.1 (app/server.py). The page is in local mode only
// when it was opened with that server's token; without one (e.g. on Vercel) nothing is requested.

export interface LocalRepo {
  repo: string;
  branch: string;
  default_base: string;
  languages: { language: string; tier: string }[];
  config_error: string | null;
}

export interface Branch {
  name: string;
  sha: string;
  date: string;
}

export interface RunStep {
  step: string;
  detail: string;
  at: string;
}

export interface RunMeta {
  id: string;
  base: string;
  head: string;
  full: boolean;
  status: "running" | "done" | "failed";
  started_at: string;
  finished_at: string | null;
  steps: RunStep[];
  error: string | null;
  triage?: string | null;
}

export interface SavedDecision {
  decision: Decision;
  path: string;
  git: string;
}

const TOKEN_KEY = "behavior-review-token";

/** The token from the URL `behavior-review ui` printed, kept for this tab (a reload still works) and taken out of the address bar. */
function readToken(): string | null {
  const url = new URL(window.location.href);
  const fromUrl = url.searchParams.get("token");
  if (fromUrl) {
    try {
      sessionStorage.setItem(TOKEN_KEY, fromUrl);
    } catch {
      // Storage can be blocked; the token then lasts until the next reload.
    }
    url.searchParams.delete("token");
    window.history.replaceState(null, "", url);
    return fromUrl;
  }
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

async function reply<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ReportError(body?.detail ?? `The local server answered ${response.status}.`);
  return body as T;
}

export class LocalApi {
  constructor(private readonly token: string) {}

  get<T>(path: string): Promise<T> {
    return fetch(path, { headers: { "x-token": this.token } }).then((response) => reply<T>(response));
  }

  post<T>(path: string, body: unknown): Promise<T> {
    return fetch(path, {
      method: "POST",
      headers: { "x-token": this.token, "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then((response) => reply<T>(response));
  }

  async run(id: string): Promise<{ report: ReviewReport; map: RepoMap | null }> {
    const [report, map] = await Promise.all([
      this.get<unknown>(`/api/runs/${id}/report`),
      this.get<unknown>(`/api/runs/${id}/repo_map`).catch(() => null),
    ]);
    return { report: parseReport(report), map: map === null ? null : parseRepoMap(map) };
  }

  /** A run's steps as they happen; `onEnd` gets its final state. Returns a function that stops listening. */
  follow(id: string, onStep: (step: RunStep) => void, onEnd: (meta: RunMeta) => void): () => void {
    // EventSource can't send headers, so the token goes in the query (the server accepts both).
    const events = new EventSource(`/api/runs/${id}/events?token=${encodeURIComponent(this.token)}`);
    events.onmessage = (event) => onStep(JSON.parse(event.data) as RunStep);
    events.addEventListener("end", (event) => {
      events.close();
      onEnd(JSON.parse((event as MessageEvent).data) as RunMeta);
    });
    events.onerror = () => events.close();
    return () => events.close();
  }
}

/** The local server and its repository, or null when the page wasn't opened by `behavior-review ui`. */
export async function detectLocal(): Promise<{ api: LocalApi; repo: LocalRepo } | null> {
  const token = readToken();
  if (!token) return null;
  const api = new LocalApi(token);
  try {
    return { api, repo: await api.get<LocalRepo>("/api/repo") };
  } catch {
    return null;
  }
}
