"use client";

import { useMemo, useState } from "react";

import { deleteThread, importBackendThread } from "@waypoint/web-core/api";
import { agentTransports, type BackendCatalog } from "@waypoint/web-core/backends";
import { useImportModels } from "@waypoint/web-core/hooks/useImportModels";
import { useResumableThreads } from "@waypoint/web-core/hooks/useResumableThreads";
import { clampTransport } from "@waypoint/web-core/launch/form";
import { entriesToRecord, recordToEntries, type EnvEntry } from "@waypoint/web-core/launch/env";
import { threadDiscoveryBackends } from "@waypoint/web-core/launch/launchDefaults";
import {
  canDeleteThread,
  clampThreadFilter,
  filterThreads,
  initialThreadFilter,
  mergeThreads,
  showsImportModel,
  threadCounts,
  threadFormAgent,
  threadImportChoices,
  threadImportRequest,
  threadProfileScope,
  type AgentThread,
  type ThreadFilter,
} from "@waypoint/web-core/launch/threads";
import type { Backend, SessionRecord, SessionTransport } from "@waypoint/web-core/types";

import { handleAuthFailure } from "@/lib/auth";

export const THREAD_PAGE = 8;

interface ResumeOptions {
  host: string;
  token: string;
  enabled: boolean;
  catalog: BackendCatalog;
  launchable: Backend[];
  launchTargetId: string;
  // The launch form's agent and account profile scope that agent's thread store.
  formBackend: Backend;
  formAccountProfileId: string;
  defaultLaunchEnvByBackend: Record<Backend, Record<string, string>>;
  // Changes refetch, such as a launch target connecting.
  revision: unknown;
  onError: (message: string) => void;
}

export interface ResumeState {
  backends: Backend[];
  counts: Record<string, number>;
  loading: boolean;
  filter: ThreadFilter;
  setFilter: (filter: ThreadFilter) => void;
  query: string;
  setQuery: (query: string) => void;
  importHistory: boolean;
  setImportHistory: (value: boolean) => void;
  // The single agent whose interface, model and environment apply, or null for All.
  agent: Backend | null;
  transports: SessionTransport[];
  transport: SessionTransport;
  setTransport: (transport: SessionTransport) => void;
  showModel: boolean;
  models: ReturnType<typeof useImportModels>;
  envEntries: EnvEntry[];
  setEnvEntries: (entries: EnvEntry[]) => void;
  threads: AgentThread[];
  shown: AgentThread[];
  more: number;
  showMore: () => void;
  selected: AgentThread | null;
  select: (thread: AgentThread) => void;
  canDelete: (thread: AgentThread) => boolean;
  remove: (thread: AgentThread) => Promise<void>;
  resume: () => Promise<SessionRecord | null>;
}

export const threadKey = (thread: AgentThread) => `${thread.backend}:${thread.id}`;

export function useResume({
  host,
  token,
  enabled,
  catalog,
  launchable,
  launchTargetId,
  formBackend,
  formAccountProfileId,
  defaultLaunchEnvByBackend,
  revision,
  onError,
}: ResumeOptions): ResumeState {
  const backends = useMemo(() => threadDiscoveryBackends(launchable, catalog), [launchable, catalog]);
  const scopeProfile = formAccountProfileId || null;
  const { threadsByBackend, loadingByBackend, removeThread } = useResumableThreads({
    host,
    token,
    backends,
    launchTargetId,
    scopeBackend: formBackend,
    scopeAccountProfileId: scopeProfile,
    enabled,
    revision,
    onAuthFailure: handleAuthFailure,
    onError,
  });

  const [chosenFilter, setFilterState] = useState<ThreadFilter>(() => initialThreadFilter(backends));
  const filter = clampThreadFilter(chosenFilter, backends);
  const [query, setQuery] = useState("");
  const [importHistory, setImportHistory] = useState(true);
  const [limit, setLimit] = useState(THREAD_PAGE);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const setFilter = (next: ThreadFilter) => {
    setFilterState(next);
    setLimit(THREAD_PAGE);
    setSelectedKey(null);
  };

  const agent = filter === "all" ? null : filter;
  const formAgent = threadFormAgent(filter, backends, formBackend);
  const transports = agentTransports(formAgent, catalog);
  const [chosenTransport, setTransport] = useState<SessionTransport>("");
  const transport = clampTransport(chosenTransport, formAgent, catalog);
  // The environment starts from the agent's defaults, again whenever the agent changes.
  const envKey = `${formAgent}:${JSON.stringify(defaultLaunchEnvByBackend[formAgent] ?? {})}`;
  const [env, setEnv] = useState(() => ({ key: envKey, entries: recordToEntries(defaultLaunchEnvByBackend[formAgent]) }));
  if (env.key !== envKey) {
    setEnv({ key: envKey, entries: recordToEntries(defaultLaunchEnvByBackend[formAgent]) });
  }
  const envEntries = env.entries;
  const setEnvEntries = (entries: EnvEntry[]) => setEnv((current) => ({ ...current, entries }));

  const showModel = showsImportModel(filter, formAgent, catalog);
  const models = useImportModels(host, token, formAgent, launchTargetId || null, scopeProfile, enabled && showModel);

  const all = useMemo(() => mergeThreads(threadsByBackend, backends), [threadsByBackend, backends]);
  const counts = useMemo(() => threadCounts(threadsByBackend, backends), [threadsByBackend, backends]);
  const threads = useMemo(() => filterThreads(all, filter, query), [all, filter, query]);
  const selected = threads.find((thread) => threadKey(thread) === selectedKey) ?? null;
  const loading = filter === "all" ? backends.some((id) => loadingByBackend[id]) : Boolean(loadingByBackend[filter]);

  async function remove(thread: AgentThread) {
    await deleteThread(
      host,
      token,
      thread.backend,
      thread.id,
      launchTargetId || null,
      threadProfileScope(thread.backend, formBackend, scopeProfile),
    );
    removeThread(thread.backend, thread.id);
  }

  async function resume(): Promise<SessionRecord | null> {
    if (!selected) return null;
    const choices = threadImportChoices(
      selected,
      filter,
      { transport, launchEnv: entriesToRecord(envEntries), model: models.selected, showModel },
      defaultLaunchEnvByBackend,
      catalog,
    );
    const session = await importBackendThread(
      host,
      token,
      selected.backend,
      threadImportRequest({
        thread: selected,
        launchTargetId,
        accountProfileId: threadProfileScope(selected.backend, formBackend, scopeProfile),
        choices,
        importHistory,
      }),
    );
    removeThread(selected.backend, selected.id);
    return session;
  }

  return {
    backends,
    counts,
    loading,
    filter,
    setFilter,
    query,
    setQuery: (next) => {
      setQuery(next);
      setLimit(THREAD_PAGE);
    },
    importHistory,
    setImportHistory,
    agent,
    transports,
    transport,
    setTransport,
    showModel,
    models,
    envEntries,
    setEnvEntries,
    threads,
    shown: threads.slice(0, limit),
    more: Math.max(0, threads.length - limit),
    showMore: () => setLimit((current) => current + THREAD_PAGE),
    selected,
    select: (thread) => setSelectedKey(threadKey(thread)),
    canDelete: (thread) => canDeleteThread(thread.backend, catalog),
    remove,
    resume,
  };
}
