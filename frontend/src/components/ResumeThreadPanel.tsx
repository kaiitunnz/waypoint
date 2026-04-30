"use client";

import { useEffect, useMemo, useState } from "react";

import {
  Backend,
  ClaudeThreadSummary,
  CodexThreadSummary,
} from "@/lib/types";

interface ResumeThreadPanelProps {
  codexThreads: CodexThreadSummary[];
  codexLoading: boolean;
  claudeThreads: ClaudeThreadSummary[];
  claudeLoading: boolean;
  targetLabel: string | null;
  supportedBackends: Backend[];
  preferredBackend: Backend;
  onImportCodexThread: (threadId: string) => Promise<void>;
  onImportClaudeThread: (threadId: string) => Promise<void>;
}

type Filter = "all" | Backend;

interface UnifiedThread {
  id: string;
  backend: Backend;
  title: string;
  cwd: string;
  repo_name?: string | null;
  branch?: string | null;
  preview?: string | null;
  updated_at: string;
}

const COLLAPSED_VISIBLE = 2;
const PAGE_SIZE = 12;

const BACKEND_MARK: Record<Backend, string> = {
  codex: "cdx",
  claude_code: "cld",
};

const BACKEND_LABEL: Record<Backend, string> = {
  codex: "Codex",
  claude_code: "Claude",
};

export function ResumeThreadPanel({
  codexThreads,
  codexLoading,
  claudeThreads,
  claudeLoading,
  targetLabel,
  supportedBackends,
  preferredBackend,
  onImportCodexThread,
  onImportClaudeThread,
}: ResumeThreadPanelProps) {
  const showCodex = supportedBackends.includes("codex");
  const showClaude = supportedBackends.includes("claude_code");
  const dualBackend = showCodex && showClaude;

  const allThreads: UnifiedThread[] = useMemo(() => {
    const merged: UnifiedThread[] = [];
    if (showCodex) {
      for (const t of codexThreads) {
        merged.push({ ...t, backend: "codex" });
      }
    }
    if (showClaude) {
      for (const t of claudeThreads) {
        merged.push({ ...t, backend: "claude_code" });
      }
    }
    merged.sort((a, b) =>
      a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0,
    );
    return merged;
  }, [codexThreads, claudeThreads, showCodex, showClaude]);

  const counts = useMemo(
    () => ({
      all: allThreads.length,
      codex: showCodex ? codexThreads.length : 0,
      claude_code: showClaude ? claudeThreads.length : 0,
    }),
    [allThreads.length, codexThreads.length, claudeThreads.length, showCodex, showClaude],
  );

  const initialFilter: Filter = dualBackend
    ? supportedBackends.includes(preferredBackend)
      ? preferredBackend
      : "all"
    : showCodex
      ? "codex"
      : "claude_code";

  const [filter, setFilter] = useState<Filter>(initialFilter);
  const [filterTouched, setFilterTouched] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [page, setPage] = useState(1);
  const [importingId, setImportingId] = useState<string | null>(null);

  // Auto-follow the launch form's backend selection until the user
  // explicitly picks a chip; that lock stays for the session so the
  // filter doesn't keep snapping out from under them.
  useEffect(() => {
    if (!dualBackend || filterTouched) return;
    if (supportedBackends.includes(preferredBackend)) {
      setFilter(preferredBackend);
    }
  }, [dualBackend, filterTouched, preferredBackend, supportedBackends]);

  const filteredThreads = useMemo(() => {
    if (filter === "all") return allThreads;
    return allThreads.filter((t) => t.backend === filter);
  }, [allThreads, filter]);

  // Page reset whenever the filter or thread set changes underneath us.
  useEffect(() => {
    setPage(1);
  }, [filter, filteredThreads.length]);

  const totalPages = Math.max(1, Math.ceil(filteredThreads.length / PAGE_SIZE));
  const pageStart = (page - 1) * PAGE_SIZE;
  const visibleThreads = expanded
    ? filteredThreads.slice(pageStart, pageStart + PAGE_SIZE)
    : filteredThreads.slice(0, COLLAPSED_VISIBLE);

  const loading = codexLoading || claudeLoading;
  const totalForLabel = counts.all;
  const subhead = subheadFor(totalForLabel, dualBackend, targetLabel);

  async function handleImport(thread: UnifiedThread) {
    setImportingId(thread.id);
    try {
      if (thread.backend === "codex") {
        await onImportCodexThread(thread.id);
      } else {
        await onImportClaudeThread(thread.id);
      }
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
          <p className="meta resume-panel-subhead">{subhead}</p>
        </div>
        {dualBackend ? (
          <div
            className="resume-panel-filter"
            role="radiogroup"
            aria-label="Filter by backend"
          >
            <FilterChip
              label="All"
              count={counts.all}
              active={filter === "all"}
              onClick={() => chooseFilter("all")}
            />
            <FilterChip
              label="Codex"
              count={counts.codex}
              active={filter === "codex"}
              disabled={counts.codex === 0}
              onClick={() => chooseFilter("codex")}
            />
            <FilterChip
              label="Claude"
              count={counts.claude_code}
              active={filter === "claude_code"}
              disabled={counts.claude_code === 0}
              onClick={() => chooseFilter("claude_code")}
            />
          </div>
        ) : null}
      </div>

      {loading ? (
        <p className="muted resume-panel-loading">Loading stored threads…</p>
      ) : null}

      {!loading && filteredThreads.length === 0 ? (
        <p className="muted resume-panel-empty">
          {emptyHintFor(filter, dualBackend, targetLabel)}
        </p>
      ) : null}

      {filteredThreads.length > 0 ? (
        <div className="import-thread-list resume-thread-list">
          {visibleThreads.map((thread) => {
            const isImporting = importingId === thread.id;
            return (
              <article
                className={`import-thread-row resume-thread-row is-${thread.backend}`}
                key={`${thread.backend}:${thread.id}`}
              >
                <span
                  className={`import-thread-index resume-thread-mark is-${thread.backend}`}
                  aria-label={BACKEND_LABEL[thread.backend]}
                  title={BACKEND_LABEL[thread.backend]}
                >
                  {BACKEND_MARK[thread.backend]}
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
    </section>
  );
}

function FilterChip({
  label,
  count,
  active,
  disabled,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      disabled={disabled}
      className={`resume-filter-chip${active ? " is-active" : ""}`}
      onClick={onClick}
    >
      <span className="resume-filter-label">{label}</span>
      <span className="resume-filter-count">{count}</span>
    </button>
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
  return `No ${BACKEND_LABEL[filter]} sessions${where}.`;
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
