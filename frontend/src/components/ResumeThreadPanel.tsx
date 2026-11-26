"use client";

import { useEffect, useMemo, useState } from "react";

import {
  TransportPicker,
  useTransportForAgent,
} from "@/components/AgentTransportPicker";
import { LaunchOptionsDetails } from "@/components/LaunchOptions";
import type { BackendCatalog } from "@waypoint/web-core/backends";
import { humaniseBackend } from "@waypoint/web-core/backends";
import { useImportModels } from "@waypoint/web-core/hooks/useImportModels";
import {
  entriesToRecord,
  type EnvEntry,
  recordToEntries,
} from "@waypoint/web-core/launch/env";
import {
  type AgentThread,
  canDeleteThread,
  clampThreadFilter,
  deleteThreadMessage,
  filterThreads,
  formatThreadTime,
  initialThreadFilter,
  mergeThreads,
  showsImportModel,
  threadCounts,
  threadEmptyHint,
  type ThreadFilter,
  threadFilterOptions,
  threadFormAgent,
  threadImportBody,
  type ThreadSummary,
} from "@waypoint/web-core/launch/threads";
import { Backend } from "@waypoint/web-core/types";

import { SearchInput } from "./SearchInput";

interface ResumeThreadPanelProps {
  host: string;
  token: string;
  launchTargetId: string | null;
  accountProfileId: string | null;
  threadsByBackend: Record<Backend, ThreadSummary[]>;
  loadingByBackend: Record<Backend, boolean>;
  targetLabel: string | null;
  supportedBackends: Backend[];
  preferredBackend: Backend;
  // Imports a thread with the `POST /api/backends/{id}/sessions/import` body.
  onImportThread: (thread: AgentThread, body: Record<string, unknown>) => Promise<void>;
  onDeleteThread?: (
    backend: Backend,
    threadId: string,
    launchTargetId?: string,
  ) => Promise<void>;
  catalog: BackendCatalog;
  defaultLaunchEnvByBackend: Record<Backend, Record<string, string>>;
  onAuthFailure: () => void;
}

const COLLAPSED_VISIBLE = 2;
const PAGE_SIZE = 5;

// 3-letter glyph used by the per-row index chip. Pulled from the
// backend's capability descriptor (``badges.glyph``) when the catalog
// is hydrated; a label-derived fallback covers pre-catalog renders
// and unknown backends.
function backendGlyph(id: Backend, catalog?: BackendCatalog): string {
  const badge = catalog?.byId(id)?.badges?.glyph;
  if (badge) return badge.toLowerCase();
  return humaniseBackend(id, catalog).slice(0, 3).toLowerCase();
}

export function ResumeThreadPanel({
  host,
  token,
  launchTargetId,
  accountProfileId,
  threadsByBackend,
  loadingByBackend,
  targetLabel,
  supportedBackends,
  preferredBackend,
  onImportThread,
  onDeleteThread,
  catalog,
  defaultLaunchEnvByBackend,
  onAuthFailure,
}: ResumeThreadPanelProps) {
  const allThreads = useMemo(
    () => mergeThreads(threadsByBackend, supportedBackends),
    [threadsByBackend, supportedBackends],
  );

  const counts = useMemo(
    () => threadCounts(threadsByBackend, supportedBackends),
    [threadsByBackend, supportedBackends],
  );

  const [filter, setFilter] = useState<ThreadFilter>(() =>
    initialThreadFilter(supportedBackends),
  );
  const [expanded, setExpanded] = useState(false);
  const [page, setPage] = useState(1);
  const [importingId, setImportingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  // Replay the prior conversation into the new session's transcript on import.
  // On by default: an imported thread should look continuous, not empty.
  const [importHistory, setImportHistory] = useState(true);

  const transportAgent = threadFormAgent(filter, supportedBackends, preferredBackend);
  const [envEntries, setEnvEntries] = useState<EnvEntry[]>(() =>
    recordToEntries(defaultLaunchEnvByBackend[transportAgent]),
  );
  const [transport, setTransport, transports] = useTransportForAgent(
    transportAgent,
    catalog,
  );

  useEffect(() => {
    setEnvEntries(recordToEntries(defaultLaunchEnvByBackend[transportAgent]));
  }, [defaultLaunchEnvByBackend, transportAgent]);

  const showModelPicker = showsImportModel(filter, transportAgent, catalog);
  const {
    options: modelOptions,
    selected: selectedModel,
    setSelected: setSelectedModel,
  } = useImportModels(
    host,
    token,
    transportAgent,
    launchTargetId,
    accountProfileId,
    showModelPicker,
    onAuthFailure,
  );

  const isExpanded = expanded || query.trim().length > 0;

  // Re-clamp if the selected agent drops out of the supported set.
  useEffect(() => {
    const next = clampThreadFilter(filter, supportedBackends);
    if (next !== filter) setFilter(next);
  }, [supportedBackends, filter]);

  const filteredThreads = useMemo(
    () => filterThreads(allThreads, filter, query),
    [allThreads, filter, query],
  );

  // Page reset whenever the filter or thread set changes underneath us.
  useEffect(() => {
    setPage(1);
  }, [filter, filteredThreads.length, query]);

  const totalPages = Math.max(1, Math.ceil(filteredThreads.length / PAGE_SIZE));
  const pageStart = (page - 1) * PAGE_SIZE;
  const visibleThreads = isExpanded
    ? filteredThreads.slice(pageStart, pageStart + PAGE_SIZE)
    : filteredThreads.slice(0, COLLAPSED_VISIBLE);

  const loading =
    filter === "all"
      ? supportedBackends.some((id) => loadingByBackend[id])
      : loadingByBackend[filter];

  async function handleImport(thread: AgentThread) {
    const body = threadImportBody({
      thread,
      filter,
      choices: { transport, launchEnv: entriesToRecord(envEntries), model: selectedModel },
      scope: { launchTargetId, backend: preferredBackend, accountProfileId },
      importHistory,
      defaultLaunchEnvByBackend,
      catalog,
    });
    setImportingId(thread.id);
    try {
      await onImportThread(thread, body);
    } finally {
      setImportingId(null);
    }
  }

  async function handleDelete(thread: AgentThread) {
    if (!onDeleteThread) {
      return;
    }
    const confirmed = window.confirm(deleteThreadMessage(thread));
    if (!confirmed) {
      return;
    }
    setDeletingId(thread.id);
    try {
      await onDeleteThread(thread.backend, thread.id);
    } finally {
      setDeletingId(null);
    }
  }

  function chooseFilter(next: ThreadFilter) {
    setFilter(next);
    setExpanded(false);
  }

  const filterOptions = threadFilterOptions(supportedBackends);

  return (
    <div className="launch-body resume-body">
      {filterOptions.length > 1 ? (
        <label className="field">
          <span>Agent</span>
          <select
            value={filter}
            onChange={(event) => chooseFilter(event.target.value as ThreadFilter)}
          >
            {filterOptions.map((option) => {
              if (option === "all") {
                return (
                  <option key="all" value="all">
                    All ({counts.all})
                  </option>
                );
              }
              return (
                <option key={option} value={option}>
                  {humaniseBackend(option, catalog)} ({counts[option] ?? 0})
                </option>
              );
            })}
          </select>
        </label>
      ) : null}

      {filter !== "all" ? (
        <LaunchOptionsDetails
          mode="resume"
          envEntries={envEntries}
          onEnvEntriesChange={setEnvEntries}
          formBusy={importingId !== null || deletingId !== null}
        />
      ) : null}

      {filter !== "all" ? (
        <TransportPicker
          transports={transports}
          value={transport}
          onChange={setTransport}
          catalog={catalog}
        />
      ) : null}

      {showModelPicker && modelOptions.length > 0 ? (
        <label className="field">
          <span>Model</span>
          <select
            value={selectedModel ?? ""}
            onChange={(event) => setSelectedModel(event.target.value)}
          >
            {modelOptions
              .filter((option) => !option.hidden)
              .map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
          </select>
        </label>
      ) : null}

      <div className="import-history-row">
        <div className="import-history-copy">
          <span className="import-history-label">Import history</span>
          <span className="import-history-hint">
            Replay the prior conversation into the new session
          </span>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={importHistory}
          aria-label="Import prior conversation history"
          className="switch"
          onClick={() => setImportHistory((value) => !value)}
        >
          <span className="switch-thumb" />
        </button>
      </div>

      <div className="resume-filters">
        <div className="resume-filters-search">
          <SearchInput
            className="thread-panel-search"
            value={query}
            onChange={setQuery}
            placeholder='Filter threads... (e.g. "title:bug AND branch:main")'
            showStatusExample={false}
          />
        </div>
      </div>

      {loading ? (
        <p className="muted resume-panel-loading">Loading stored threads…</p>
      ) : null}

      {!loading && filteredThreads.length === 0 ? (
        <p className="muted resume-panel-empty">
          {query.trim().length > 0
            ? "No threads match your search."
            : threadEmptyHint(filter, catalog, targetLabel)}
        </p>
      ) : null}

      {filteredThreads.length > 0 ? (
        <div className="import-thread-list resume-thread-list">
          {visibleThreads.map((thread) => {
            const isImporting = importingId === thread.id;
            const isDeleting = deletingId === thread.id;
            const canDelete =
              !!onDeleteThread && canDeleteThread(thread.backend, catalog);
            const busy = importingId !== null || deletingId !== null;
            const backendLabel = humaniseBackend(thread.backend, catalog);
            return (
              <article
                className={`import-thread-row resume-thread-row is-${thread.backend}`}
                key={`${thread.backend}:${thread.id}`}
                data-backend={thread.backend}
              >
                <span
                  className={`import-thread-index resume-thread-mark is-${thread.backend}`}
                  aria-label={backendLabel}
                  title={backendLabel}
                >
                  {backendGlyph(thread.backend, catalog)}
                </span>
                <div className="import-thread-body">
                  <div className="import-thread-headline">
                    <h4 title={thread.title}>{thread.title}</h4>
                    <time
                      className="import-thread-time"
                      dateTime={thread.updated_at}
                    >
                      {formatThreadTime(thread.updated_at)}
                    </time>
                  </div>
                  {thread.preview ? (
                    <p className="import-thread-preview" title={thread.preview}>
                      {thread.preview}
                    </p>
                  ) : null}
                  <div className="import-thread-bottomline">
                    <div className="import-thread-tags">
                      {thread.branch ? (
                        <span className="import-thread-chip">{thread.branch}</span>
                      ) : null}
                      {thread.repo_name ? (
                        <span className="import-thread-repo">
                          {thread.repo_name}
                        </span>
                      ) : null}
                      <span className="import-thread-cwd" title={thread.cwd}>
                        {thread.cwd}
                      </span>
                    </div>
                    <div className="import-thread-cta">
                      {canDelete ? (
                        <button
                          className="link-button danger-link action-chip"
                          disabled={busy}
                          type="button"
                          onClick={() => void handleDelete(thread)}
                        >
                          {isDeleting ? "Deleting…" : "Delete"}
                        </button>
                      ) : null}
                      <button
                        className="link-button action-chip"
                        disabled={busy}
                        type="button"
                        onClick={() => void handleImport(thread)}
                      >
                        {isImporting ? "Importing…" : "Import →"}
                      </button>
                    </div>
                  </div>
                </div>
              </article>
            );
          })}
          {expanded && totalPages > 1 ? (
            <div className="import-thread-footer resume-thread-footer">
              <span>
                {pageStart + 1}–{Math.min(filteredThreads.length, pageStart + PAGE_SIZE)} of {filteredThreads.length}
              </span>
              <div className="import-thread-pager">
                <button
                  className="secondary"
                  type="button"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page === 1}
                >
                  ← Prev
                </button>
                <span className="import-thread-page-indicator">
                  {page} / {totalPages}
                </span>
                <button
                  className="secondary"
                  type="button"
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page === totalPages}
                >
                  Next →
                </button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      {filteredThreads.length > COLLAPSED_VISIBLE ? (
        <div className="resume-panel-foot">
          <button
            type="button"
            className="resume-panel-foot-link"
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded
              ? "collapse ↑"
              : `view all ${filteredThreads.length} ↓`}
          </button>
        </div>
      ) : null}
    </div>
  );
}
