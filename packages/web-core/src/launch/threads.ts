import { defaultTransportFor, humaniseBackend, type BackendCatalog } from "../backends";
import { matchesQuery, parseQuery } from "../search";
import type { Backend, SessionTransport } from "../types";

// A resumable native thread as the threads endpoint lists it; every agent's
// summary has this shape.
export interface ThreadSummary {
  id: string;
  title: string;
  cwd: string;
  repo_name?: string | null;
  branch?: string | null;
  preview?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentThread extends ThreadSummary {
  backend: Backend;
}

// "all" lists every agent's threads together; an agent narrows the list and
// unlocks that agent's interface, model and environment for the import.
export type ThreadFilter = "all" | Backend;

const SEARCH_FIELDS = ["title", "cwd", "repo_name", "branch", "preview", "backend"];

// Every agent's threads, most recently updated first.
export function mergeThreads(byBackend: Record<Backend, ThreadSummary[]>, backends: Backend[]): AgentThread[] {
  const merged: AgentThread[] = [];
  for (const backend of backends) {
    for (const thread of byBackend[backend] ?? []) {
      merged.push({ ...thread, backend });
    }
  }
  return merged.sort((a, b) => (a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0));
}

export function threadCounts(byBackend: Record<Backend, ThreadSummary[]>, backends: Backend[]): Record<string, number> {
  const counts: Record<string, number> = { all: 0 };
  for (const backend of backends) {
    counts[backend] = (byBackend[backend] ?? []).length;
    counts.all += counts[backend];
  }
  return counts;
}

// "All" leads when more than one agent can resume; a lone agent has no "All".
export function threadFilterOptions(backends: Backend[]): ThreadFilter[] {
  return backends.length >= 2 ? ["all", ...backends] : backends;
}

export function initialThreadFilter(backends: Backend[]): ThreadFilter {
  return backends.length >= 2 ? "all" : (backends[0] ?? "all");
}

// Keeps a filter valid when the agent drops out of the resumable set.
export function clampThreadFilter(filter: ThreadFilter, backends: Backend[]): ThreadFilter {
  if (filter === "all" || backends.length === 0 || backends.includes(filter)) return filter;
  return initialThreadFilter(backends);
}

// Threads for the filter, matched against the search query syntax.
export function filterThreads(threads: AgentThread[], filter: ThreadFilter, query: string): AgentThread[] {
  const list = filter === "all" ? threads : threads.filter((thread) => thread.backend === filter);
  const trimmed = query.trim();
  if (!trimmed) return list;
  const terms = parseQuery(trimmed);
  return list.filter((thread) => matchesQuery(thread, terms, SEARCH_FIELDS));
}

// The agent whose interface and environment a single-agent view edits: the
// filtered one, or the preferred agent while "All" is shown.
export function threadFormAgent(filter: ThreadFilter, backends: Backend[], preferred: Backend): Backend {
  if (filter !== "all") return filter;
  return backends.includes(preferred) ? preferred : (backends[0] ?? preferred);
}

// The account profile a thread's store is scoped to: the form's profile, for the
// form's own agent only. Import and delete need it, or a profile-scoped thread is
// not found.
export function threadProfileScope(
  backend: Backend,
  scopeBackend: Backend | null,
  scopeAccountProfileId: string | null,
): string | null {
  return backend === scopeBackend ? scopeAccountProfileId || null : null;
}

// What a single-agent view chose for the import.
export interface ThreadImportChoices {
  transport: SessionTransport;
  launchEnv: Record<string, string>;
  model: string | null;
}

export interface ThreadImportScope {
  launchTargetId: string | null;
  // The form's agent and account profile, which scope that agent's thread store.
  backend: Backend | null;
  accountProfileId: string | null;
}

// The body of `POST /api/backends/{id}/sessions/import`. A single-agent view
// resumes with the chosen interface, environment and model; "All" mixes agents,
// so each thread resumes with its own agent's defaults.
export function threadImportBody({
  thread,
  filter,
  choices,
  scope,
  importHistory,
  defaultLaunchEnvByBackend,
  catalog,
}: {
  thread: AgentThread;
  filter: ThreadFilter;
  choices: ThreadImportChoices;
  scope: ThreadImportScope;
  importHistory: boolean;
  defaultLaunchEnvByBackend: Record<Backend, Record<string, string>>;
  catalog: BackendCatalog;
}): Record<string, unknown> {
  const own = filter === thread.backend;
  return {
    thread_id: thread.id,
    launch_target_id: scope.launchTargetId || null,
    account_profile_id: threadProfileScope(thread.backend, scope.backend, scope.accountProfileId),
    cwd: thread.cwd,
    // An explicit transport supersedes launch_mode, so the mode stays "auto".
    launch_mode: "auto",
    transport: own ? choices.transport || null : defaultTransportFor(thread.backend, catalog),
    launch_env: own ? choices.launchEnv : { ...(defaultLaunchEnvByBackend[thread.backend] ?? {}) },
    import_history: importHistory,
    // Null lets the agent resolve its configured default.
    model: showsImportModel(filter, thread.backend, catalog) ? choices.model : null,
  };
}

export function canDeleteThread(backend: Backend, catalog: BackendCatalog): boolean {
  return catalog.agentCaps(backend)?.supports_thread_delete ?? false;
}

// An agent offers a model at import only when it keeps that choice.
export function showsImportModel(filter: ThreadFilter, agent: Backend, catalog: BackendCatalog): boolean {
  return filter !== "all" && (catalog.agentCaps(agent)?.supports_thread_import_model ?? false);
}

export function threadEmptyHint(filter: ThreadFilter, catalog: BackendCatalog, targetLabel: string | null): string {
  const where = targetLabel ? ` on ${targetLabel}` : "";
  if (filter === "all") return `No importable threads found${where}.`;
  return `No ${humaniseBackend(filter, catalog)} threads to resume${where}.`;
}

export function deleteThreadMessage(thread: ThreadSummary): string {
  return (
    `Delete "${thread.title}"?\n\nThis removes the on-disk transcript permanently, so the agent ` +
    "can no longer resume it. This cannot be undone."
  );
}

// "just now" under a minute, else a relative day, hour or minute count.
export function formatThreadTime(value: string, now: number = Date.now()): string {
  const deltaSeconds = Math.round((new Date(value).getTime() - now) / 1000);
  if (Math.abs(deltaSeconds) < 60) return "just now";
  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["day", 60 * 60 * 24],
    ["hour", 60 * 60],
    ["minute", 60],
  ];
  for (const [unit, seconds] of units) {
    if (Math.abs(deltaSeconds) >= seconds) {
      return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(
        Math.round(deltaSeconds / seconds),
        unit,
      );
    }
  }
  return "just now";
}

// The agents whose resumable threads to list: launchable ones that discover
// threads. An agent missing from the catalog is tried, so a first load before the
// catalog arrives still fetches.
export function threadDiscoveryBackends(launchable: Backend[], catalog: BackendCatalog): Backend[] {
  return launchable.filter((id) => catalog.agentCaps(id)?.supports_thread_discovery ?? true);
}
