"use client";

import { useEffect, useMemo, useState } from "react";

import {
  AgentTransportPicker,
  useTransportForAgent,
} from "@/components/AgentTransportPicker";
import type { BackendCatalog } from "@/lib/backends";
import { humaniseBackend } from "@/lib/backends";
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
  ) => Promise<void>;
  catalog: BackendCatalog;
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
  catalog,
}: ResumeThreadPanelProps) {
  // The chosen agent both lists its stored threads and drives the transport
  // the imported session is resumed over — so a Claude thread can come back as
  // Chat or Emulated.
  const initialAgent: Backend = supportedBackends.includes(preferredBackend)
    ? preferredBackend
    : (supportedBackends[0] ?? preferredBackend);
  const [agent, setAgent] = useState<Backend>(initialAgent);
  const [agentTouched, setAgentTouched] = useState(false);
  const [transport, setTransport] = useTransportForAgent(agent, catalog);
  const [expanded, setExpanded] = useState(false);
  const [page, setPage] = useState(1);
  const [importingId, setImportingId] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const isExpanded = expanded || query.trim().length > 0;

  // Auto-follow the launch form's agent selection until the user explicitly
  // picks an agent here; that lock stays for the session so the list doesn't
  // keep snapping out from under them.
  useEffect(() => {
    if (agentTouched) return;
    if (supportedBackends.includes(preferredBackend)) {
      setAgent(preferredBackend);
    }
  }, [agentTouched, preferredBackend, supportedBackends]);

  // Re-clamp if the selected agent drops out of the supported set.
  useEffect(() => {
    if (supportedBackends.length > 0 && !supportedBackends.includes(agent)) {
      setAgent(supportedBackends[0]);
    }
  }, [supportedBackends, agent]);

  const agentThreads = useMemo(() => {
    const list = [...(threadsByBackend[agent] ?? [])];
    list.sort((a, b) =>
      a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0,
    );
    return list;
  }, [threadsByBackend, agent]);

  const filteredThreads = useMemo(() => {
    if (query.trim() === "") return agentThreads;
    const terms = parseQuery(query.trim());
    const defaultFields = ["title", "cwd", "repo_name", "branch", "preview"];
    return agentThreads.filter((t) => matchesQuery(t, terms, defaultFields));
  }, [agentThreads, query]);

  // Page reset whenever the agent or thread set changes underneath us.
  useEffect(() => {
    setPage(1);
  }, [agent, filteredThreads.length, query]);

  const totalPages = Math.max(1, Math.ceil(filteredThreads.length / PAGE_SIZE));
  const pageStart = (page - 1) * PAGE_SIZE;
  const visibleThreads = isExpanded
    ? filteredThreads.slice(pageStart, pageStart + PAGE_SIZE)
    : filteredThreads.slice(0, COLLAPSED_VISIBLE);

  const loading = loadingByBackend[agent];

  async function handleImport(thread: ThreadSummary) {
    setImportingId(thread.id);
    try {
      await onImportThread(agent, thread.id, thread.cwd, transport || null);
    } finally {
      setImportingId(null);
    }
  }

  function chooseAgent(next: Backend) {
    setAgent(next);
    setAgentTouched(true);
    setExpanded(false);
  }

  const agentLabel = catalog.byId(agent)?.label ?? humaniseBackend(agent);

  return (
    <div className="launch-body resume-body">
      <AgentTransportPicker
        agents={supportedBackends}
        agent={agent}
        onAgentChange={chooseAgent}
        transport={transport}
        onTransportChange={setTransport}
        catalog={catalog}
      />

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
            : emptyHintFor(agentLabel, targetLabel)}
        </p>
      ) : null}

      {filteredThreads.length > 0 ? (
        <div className="import-thread-list resume-thread-list">
          {visibleThreads.map((thread) => {
            const isImporting = importingId === thread.id;
            return (
              <article
                className={`import-thread-row resume-thread-row is-${agent}`}
                key={`${agent}:${thread.id}`}
                data-backend={agent}
              >
                <span
                  className={`import-thread-index resume-thread-mark is-${agent}`}
                  aria-label={agentLabel}
                  title={agentLabel}
                >
                  {backendGlyph(agent, catalog)}
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
    </div>
  );
}

function emptyHintFor(agentLabel: string, targetLabel: string | null): string {
  const where = targetLabel ? ` on ${targetLabel}` : "";
  return `No ${agentLabel} threads to resume${where}.`;
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
