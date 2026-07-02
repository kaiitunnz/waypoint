"use client";

import { useEffect, useMemo, useState } from "react";

import {
  TransportPicker,
  useTransportForAgent,
} from "@/components/AgentTransportPicker";
import type { BackendCatalog } from "@/lib/backends";
import { defaultTransportFor, humaniseBackend } from "@/lib/backends";
import { matchesQuery, parseQuery } from "@/lib/search";
import { Backend, SessionTransport } from "@/lib/types";

import { SearchInput } from "./SearchInput";

// Shared shape for the per-row data the panel displays. Codex and
// Claude thread summaries happen to be identical today; declaring it
// here keeps the panel structurally typed instead of branching on the
// summary union.
interface ThreadSummary {
  id: string;
  title: string;
  cwd: string;
  repo_name?: string | null;
  branch?: string | null;
  preview?: string | null;
  created_at: string;
  updated_at: string;
}

interface ResumeThreadPanelProps {
  threadsByBackend: Record<Backend, ThreadSummary[]>;
  loadingByBackend: Record<Backend, boolean>;
  targetLabel: string | null;
  supportedBackends: Backend[];
  preferredBackend: Backend;
  onImportThread: (
    backend: Backend,
    threadId: string,
    cwd: string,
    transport: SessionTransport | null,
    importHistory: boolean,
  ) => Promise<void>;
  onDeleteThread?: (
    backend: Backend,
    threadId: string,
    launchTargetId?: string,
  ) => Promise<void>;
  catalog: BackendCatalog;
}

// "all" merges every agent's threads into one list; a concrete backend narrows
// it to that agent and unlocks the transport picker for the import.
type Filter = "all" | Backend;

interface UnifiedThread extends ThreadSummary {
  backend: Backend;
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
  const label = catalog?.byId(id)?.label ?? humaniseBackend(id);
  return label.slice(0, 3).toLowerCase();
}

export function ResumeThreadPanel({
  threadsByBackend,
  loadingByBackend,
  targetLabel,
  supportedBackends,
  preferredBackend,
  onImportThread,
  onDeleteThread,
  catalog,
}: ResumeThreadPanelProps) {
  const multiAgent = supportedBackends.length >= 2;

  const allThreads: UnifiedThread[] = useMemo(() => {
    const merged: UnifiedThread[] = [];
    for (const id of supportedBackends) {
      for (const t of threadsByBackend[id] ?? []) {
        merged.push({ ...t, backend: id });
      }
    }
    merged.sort((a, b) =>
      a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0,
    );
    return merged;
  }, [threadsByBackend, supportedBackends]);

  const counts = useMemo(() => {
    const byBackend: Record<string, number> = { all: allThreads.length };
    for (const id of supportedBackends) {
      byBackend[id] = (threadsByBackend[id] ?? []).length;
    }
    return byBackend;
  }, [allThreads.length, threadsByBackend, supportedBackends]);

  // Default to the cross-agent list when more than one agent can resume; a
  // lone agent skips the "All" chip entirely.
  const initialFilter: Filter = multiAgent
    ? "all"
    : (supportedBackends[0] ?? "all");
  const [filter, setFilter] = useState<Filter>(initialFilter);
  const [expanded, setExpanded] = useState(false);
  const [page, setPage] = useState(1);
  const [importingId, setImportingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  // Replay the prior conversation into the new session's transcript on import.
  // On by default: an imported thread should look continuous, not empty.
  const [importHistory, setImportHistory] = useState(true);

  // The transport picker only applies to a single-agent view. Feed the hook a
  // concrete agent (the filtered one, or the preferred fallback while "All" is
  // active) so its state stays valid; the value is only read when a concrete
  // agent is selected.
  const transportAgent: Backend =
    filter === "all"
      ? supportedBackends.includes(preferredBackend)
        ? preferredBackend
        : (supportedBackends[0] ?? preferredBackend)
      : filter;
  const [transport, setTransport, transports] = useTransportForAgent(
    transportAgent,
    catalog,
  );

  const isExpanded = expanded || query.trim().length > 0;

  // Re-clamp if the selected agent drops out of the supported set.
  useEffect(() => {
    if (
      filter !== "all" &&
      supportedBackends.length > 0 &&
      !supportedBackends.includes(filter)
    ) {
      setFilter(multiAgent ? "all" : supportedBackends[0]);
    }
  }, [supportedBackends, filter, multiAgent]);

  const filteredThreads = useMemo(() => {
    let list =
      filter === "all"
        ? allThreads
        : allThreads.filter((t) => t.backend === filter);
    if (query.trim() !== "") {
      const terms = parseQuery(query.trim());
      const defaultFields = [
        "title",
        "cwd",
        "repo_name",
        "branch",
        "preview",
        "backend",
      ];
      list = list.filter((t) => matchesQuery(t, terms, defaultFields));
    }
    return list;
  }, [allThreads, filter, query]);

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

  async function handleImport(thread: UnifiedThread) {
    // Single-agent view resumes over the chosen transport; the merged "All"
    // view can't offer one control across mixed agents, so each row imports
    // over its own agent's default transport.
    const chosen =
      filter === thread.backend
        ? transport || null
        : defaultTransportFor(thread.backend, catalog);
    setImportingId(thread.id);
    try {
      await onImportThread(
        thread.backend,
        thread.id,
        thread.cwd,
        chosen,
        importHistory,
      );
    } finally {
      setImportingId(null);
    }
  }

  async function handleDelete(thread: UnifiedThread) {
    if (!onDeleteThread) {
      return;
    }
    const confirmed = window.confirm(
      `Delete "${thread.title}"?\n\nThis removes the on-disk transcript permanently. ` +
        "`claude --resume`/codex resume will no longer see it. This cannot be undone.",
    );
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

  function chooseFilter(next: Filter) {
    setFilter(next);
    setExpanded(false);
  }

  const filterOptions: Filter[] = multiAgent
    ? ["all", ...supportedBackends]
    : supportedBackends;

  return (
    <div className="launch-body resume-body">
      {filterOptions.length > 1 ? (
        <label className="field">
          <span>Agent</span>
          <select
            value={filter}
            onChange={(event) => chooseFilter(event.target.value as Filter)}
          >
            {filterOptions.map((option) => {
              if (option === "all") {
                return (
                  <option key="all" value="all">
                    All ({counts.all})
                  </option>
                );
              }
              const label = catalog.byId(option)?.label ?? humaniseBackend(option);
              return (
                <option key={option} value={option}>
                  {label} ({counts[option] ?? 0})
                </option>
              );
            })}
          </select>
        </label>
      ) : null}

      {filter !== "all" ? (
        <TransportPicker
          transports={transports}
          value={transport}
          onChange={setTransport}
          catalog={catalog}
        />
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
            : emptyHintFor(filter, catalog, targetLabel)}
        </p>
      ) : null}

      {filteredThreads.length > 0 ? (
        <div className="import-thread-list resume-thread-list">
          {visibleThreads.map((thread) => {
            const isImporting = importingId === thread.id;
            const isDeleting = deletingId === thread.id;
            const canDelete =
              !!onDeleteThread &&
              !!catalog.byId(thread.backend)?.agent_capabilities
                ?.supports_thread_delete;
            const busy = importingId !== null || deletingId !== null;
            const backendLabel =
              catalog.byId(thread.backend)?.label ??
              humaniseBackend(thread.backend);
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
                      {formatRelativeTime(thread.updated_at)}
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

function emptyHintFor(
  filter: Filter,
  catalog: BackendCatalog,
  targetLabel: string | null,
): string {
  const where = targetLabel ? ` on ${targetLabel}` : "";
  if (filter === "all") {
    return `No importable threads found${where}.`;
  }
  const label = catalog.byId(filter)?.label ?? humaniseBackend(filter);
  return `No ${label} threads to resume${where}.`;
}

function formatRelativeTime(value: string): string {
  const then = new Date(value).getTime();
  const now = Date.now();
  const deltaSeconds = Math.round((then - now) / 1000);
  // ``Intl.RelativeTimeFormat`` returns "this minute" for ``format(0,
  // "minute")``, which reads as boilerplate for a row that just updated
  // a few seconds ago. Short-circuit to a plain phrase below 60 s.
  if (Math.abs(deltaSeconds) < 60) {
    return "just now";
  }
  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["day", 60 * 60 * 24],
    ["hour", 60 * 60],
    ["minute", 60],
  ];
  for (const [unit, seconds] of units) {
    if (Math.abs(deltaSeconds) >= seconds) {
      const amount = Math.round(deltaSeconds / seconds);
      return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(
        amount,
        unit,
      );
    }
  }
  return "just now";
}
