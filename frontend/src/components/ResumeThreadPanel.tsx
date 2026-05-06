"use client";

import { useEffect, useMemo, useState } from "react";

import type { BackendCatalog } from "@/lib/backends";
import { humaniseBackend } from "@/lib/backends";
import { matchesQuery, parseQuery } from "@/lib/search";
import { Backend } from "@/lib/types";

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
  onImportThread: (backend: Backend, threadId: string, cwd: string) => Promise<void>;
  catalog?: BackendCatalog;
}

type Filter = "all" | Backend;

interface UnifiedThread extends ThreadSummary {
  backend: Backend;
}

const COLLAPSED_VISIBLE = 2;
// Desktop and mobile values are kept separate even when equal today —
// future tuning may diverge them again, and the matchMedia hook below
// already wires them.
const PAGE_SIZE_DESKTOP = 5;
const PAGE_SIZE_MOBILE = 5;
const MOBILE_BREAKPOINT = "(max-width: 720px)";

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
  catalog,
}: ResumeThreadPanelProps) {
  const dualBackend = supportedBackends.length >= 2;

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

  const filterOptions = useMemo(
    () => [
      {
        value: "all" as Filter,
        label: `All (${counts.all})`,
        disabled: false,
      },
      ...supportedBackends.map((id) => ({
        value: id as Filter,
        label: `${catalog?.byId(id)?.label ?? humaniseBackend(id)} (${counts[id] ?? 0})`,
        disabled: (counts[id] ?? 0) === 0,
      })),
    ],
    [catalog, counts, supportedBackends],
  );

  const initialFilter: Filter = dualBackend
    ? supportedBackends.includes(preferredBackend)
      ? preferredBackend
      : "all"
    : (supportedBackends[0] ?? "all");

  const [filter, setFilter] = useState<Filter>(initialFilter);
  const [filterTouched, setFilterTouched] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<number>(PAGE_SIZE_DESKTOP);
  const [importingId, setImportingId] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const isExpanded = expanded || query.trim().length > 0;

  // Tighter pagination on phones — 5 rows fits without forcing a long
  // scroll inside an already-cramped viewport.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const mql = window.matchMedia(MOBILE_BREAKPOINT);
    const sync = () => setPageSize(mql.matches ? PAGE_SIZE_MOBILE : PAGE_SIZE_DESKTOP);
    sync();
    mql.addEventListener("change", sync);
    return () => mql.removeEventListener("change", sync);
  }, []);

  // Auto-follow the launch form's backend selection until the user
  // explicitly picks an option; that lock stays for the session so the
  // filter doesn't keep snapping out from under them.
  useEffect(() => {
    if (!dualBackend || filterTouched) return;
    if (supportedBackends.includes(preferredBackend)) {
      setFilter(preferredBackend);
    }
  }, [dualBackend, filterTouched, preferredBackend, supportedBackends]);

  const filteredThreads = useMemo(() => {
    let list = filter === "all" ? allThreads : allThreads.filter((t) => t.backend === filter);

    if (query.trim() !== "") {
      const terms = parseQuery(query.trim());
      const defaultFields = ["title", "cwd", "repo_name", "branch", "preview", "backend"];
      list = list.filter((t) => matchesQuery(t, terms, defaultFields));
    }

    return list;
  }, [allThreads, filter, query]);

  // Page reset whenever the filter, thread set, or page size changes
  // underneath us (e.g. rotating phone landscape ⇄ portrait).
  useEffect(() => {
    setPage(1);
  }, [filter, filteredThreads.length, pageSize, query]);

  const totalPages = Math.max(1, Math.ceil(filteredThreads.length / pageSize));
  const pageStart = (page - 1) * pageSize;
  const visibleThreads = isExpanded
    ? filteredThreads.slice(pageStart, pageStart + pageSize)
    : filteredThreads.slice(0, COLLAPSED_VISIBLE);

  const loading = supportedBackends.some((id) => loadingByBackend[id]);
  const totalForLabel = counts.all;
  const subhead = subheadFor(totalForLabel, dualBackend, targetLabel);

  async function handleImport(thread: UnifiedThread) {
    setImportingId(thread.id);
    try {
      await onImportThread(thread.backend, thread.id, thread.cwd);
    } finally {
      setImportingId(null);
    }
  }

  function chooseFilter(next: Filter) {
    setFilter(next);
    setFilterTouched(true);
    setExpanded(false);
  }

  return (
    <section className="panel stack import-thread-panel resume-panel">
      <div className="resume-panel-header">
        <div className="resume-panel-titles">
          <h3>Resume thread</h3>
          <p className="muted">{subhead}</p>
        </div>
        {dualBackend ? (
          <label className="field resume-panel-filter">
            <span>Backend</span>
            <select
              aria-label="Filter by backend"
              value={filter}
              onChange={(event) => chooseFilter(event.target.value as Filter)}
            >
              {filterOptions.map((option) => (
                <option
                  key={option.value}
                  value={option.value}
                  disabled={option.disabled}
                >
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>

      <SearchInput
        className="thread-panel-search"
        value={query}
        onChange={setQuery}
        placeholder='Filter threads... (e.g. "title:bug AND branch:main")'
        showStatusExample={false}
      />

      {loading ? (
        <p className="muted resume-panel-loading">Loading stored threads…</p>
      ) : null}

      {!loading && filteredThreads.length === 0 ? (
        <p className="muted resume-panel-empty">
          {query.trim().length > 0
            ? "No threads match your search."
            : emptyHintFor(filter, dualBackend, targetLabel)}
        </p>
      ) : null}

      {filteredThreads.length > 0 ? (
        <div className="import-thread-list resume-thread-list">
          {visibleThreads.map((thread) => {
            const isImporting = importingId === thread.id;
            const backendLabel =
              catalog?.byId(thread.backend)?.label ?? humaniseBackend(thread.backend);
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
                      <button
                        className="secondary"
                        disabled={importingId !== null}
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
                {pageStart + 1}–{Math.min(filteredThreads.length, pageStart + pageSize)} of {filteredThreads.length}
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
    </section>
  );
}

function subheadFor(
  total: number,
  dualBackend: boolean,
  targetLabel: string | null,
): string {
  if (total === 0) {
    return targetLabel
      ? `No stored sessions on ${targetLabel}`
      : "No stored sessions yet";
  }
  const noun = total === 1 ? "session" : "sessions";
  const span = dualBackend ? " across two backends" : "";
  const where = targetLabel ? ` on ${targetLabel}` : "";
  return `${total} ${noun}${span}${where}`;
}

function emptyHintFor(
  filter: Filter,
  dualBackend: boolean,
  targetLabel: string | null,
): string {
  const where = targetLabel ? ` on ${targetLabel}` : "";
  if (filter === "all" || !dualBackend) {
    return `No importable threads found${where}.`;
  }
  return `No ${humaniseBackend(filter)} sessions${where}.`;
}

function formatRelativeTime(value: string): string {
  const then = new Date(value).getTime();
  const now = Date.now();
  const deltaSeconds = Math.round((then - now) / 1000);
  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["day", 60 * 60 * 24],
    ["hour", 60 * 60],
    ["minute", 60],
  ];
  for (const [unit, seconds] of units) {
    if (Math.abs(deltaSeconds) >= seconds || unit === "minute") {
      const amount = Math.round(deltaSeconds / seconds);
      return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(
        amount,
        unit,
      );
    }
  }
  return "just now";
}
