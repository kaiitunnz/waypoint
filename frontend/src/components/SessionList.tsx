"use client";

import Link from "next/link";
import { MouseEvent, ReactNode, useEffect, useMemo, useState } from "react";

import {
  defaultTransportFor,
  displayAgentFor,
  humaniseBackend,
  transportLabel,
  type BackendCatalog,
} from "@/lib/backends";
import { matchesQuery, parseQuery } from "@/lib/search";
import { SessionRecord } from "@/lib/types";

import { SearchInput } from "./SearchInput";
import { Pager } from "@/components/Pager";

interface SessionListProps {
  sessions: SessionRecord[];
  catalog?: BackendCatalog;
  onDelete?: (sessionId: string) => void | Promise<void>;
  onDeleteExited?: () => void | Promise<void>;
  onTerminate?: (sessionId: string) => void | Promise<void>;
  onSetPinned?: (sessionId: string, pinned: boolean) => void | Promise<void>;
  onSetTitle?: (sessionId: string, title: string) => void | Promise<void>;
}

const PAGE_SIZE = 10;

export function SessionList({
  sessions,
  catalog,
  onDelete,
  onDeleteExited,
  onTerminate,
  onSetPinned,
  onSetTitle,
}: SessionListProps) {
  const [page, setPage] = useState(1);
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [query, setQuery] = useState("");

  function handleDelete(event: MouseEvent<HTMLButtonElement>, sessionId: string) {
    event.preventDefault();
    event.stopPropagation();
    if (!onDelete) {
      return;
    }
    if (!window.confirm("Delete this session and its transcript? This cannot be undone.")) {
      return;
    }
    void onDelete(sessionId);
  }

  function handleTerminate(event: MouseEvent<HTMLButtonElement>, sessionId: string) {
    event.preventDefault();
    event.stopPropagation();
    if (!onTerminate) {
      return;
    }
    if (!window.confirm("Terminate this session? Any running command will be stopped.")) {
      return;
    }
    void onTerminate(sessionId);
  }

  function handleTogglePin(
    event: MouseEvent<HTMLButtonElement>,
    sessionId: string,
    pinned: boolean,
  ) {
    event.preventDefault();
    event.stopPropagation();
    if (!onSetPinned) {
      return;
    }
    void onSetPinned(sessionId, pinned);
  }

  function handleSetTitle(
    event: MouseEvent<HTMLButtonElement>,
    session: SessionRecord,
  ) {
    event.preventDefault();
    event.stopPropagation();
    setEditingSessionId(session.id);
    setDraftTitle(session.title);
  }

  function handleDraftKeyDown(
    event: React.KeyboardEvent<HTMLInputElement>,
    session: SessionRecord,
  ) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      setEditingSessionId(null);
      setDraftTitle("");
    } else if (event.key === "Enter") {
      event.preventDefault();
      event.stopPropagation();
      commitEditing(session);
    }
  }

  function commitEditing(session: SessionRecord) {
    const newTitle = draftTitle.trim();
    if (newTitle && newTitle !== session.title && onSetTitle) {
      void onSetTitle(session.id, newTitle);
    }
    setEditingSessionId(null);
    setDraftTitle("");
  }

  function handleDeleteExited() {
    if (!onDeleteExited) {
      return;
    }
    if (!window.confirm("Delete all exited sessions and their transcripts? This cannot be undone.")) {
      return;
    }
    void onDeleteExited();
  }

  const {
    pinnedSessions,
    recentSessions,
    flatSearchResults,
    exitedCount,
    activeCount,
  } = useMemo(() => {
    const exited = sessions.filter((session) => session.status === "exited").length;
    const active = sessions.length - exited;

    if (query.trim() === "") {
      const pinned = sessions
        .filter((session) => session.pinned_at)
        .sort((a, b) => (b.pinned_at ?? "").localeCompare(a.pinned_at ?? ""));
      const recent = sessions.filter((session) => !session.pinned_at);
      return { pinnedSessions: pinned, recentSessions: recent, flatSearchResults: null, exitedCount: exited, activeCount: active };
    }

    const terms = parseQuery(query.trim());
    const defaultFields = ["title", "cwd", "repo_name", "branch", "backend", "search_status"];

    const matched = sessions.filter((session) => {
      return matchesQuery(session, terms, defaultFields);
    });

    matched.sort((a, b) => {
      const aPinned = Boolean(a.pinned_at);
      const bPinned = Boolean(b.pinned_at);
      if (aPinned && !bPinned) return -1;
      if (!aPinned && bPinned) return 1;
      return b.last_event_at.localeCompare(a.last_event_at);
    });

    return { pinnedSessions: [], recentSessions: [], flatSearchResults: matched, exitedCount: exited, activeCount: active };
  }, [sessions, query]);

  const listToPaginate = flatSearchResults !== null ? flatSearchResults : recentSessions;
  const totalPages = Math.max(1, Math.ceil(listToPaginate.length / PAGE_SIZE));

  useEffect(() => {
    setPage(1);
  }, [query]);

  useEffect(() => {
    setPage((current) => Math.min(current, totalPages));
  }, [totalPages]);

  if (!sessions.length) {
    return (
      <section className="panel">
        <h3>No sessions yet</h3>
        <p className="muted">Launch or attach a session to start monitoring from your phone.</p>
      </section>
    );
  }

  const pageStart = (page - 1) * PAGE_SIZE;
  const visibleItems = listToPaginate.slice(pageStart, pageStart + PAGE_SIZE);
  const showingFrom = listToPaginate.length === 0 ? 0 : pageStart + 1;
  const showingTo = Math.min(listToPaginate.length, pageStart + PAGE_SIZE);

  function renderCard(session: SessionRecord): ReactNode {
    const pinned = Boolean(session.pinned_at);
    // Normalise legacy transport-as-backend rows (e.g. backend=claude_tty) to
    // their owning agent so old and new sessions render identically. The
    // transport chip is redundant when the session runs over the agent's
    // default transport, so show it only when it differs (Emulated/Terminal).
    const agent = displayAgentFor(session.backend, session.transport, catalog);
    const agentDefault = defaultTransportFor(agent, catalog);
    const showTransport =
      agentDefault !== null && session.transport !== agentDefault;
    return (
      <Link className="panel session-card" href={`/session/${session.id}`} key={session.id}>
        <div className="session-row">
          <span className={`badge ${agent}`}>
            {humaniseBackend(agent, catalog)}
          </span>
          {showTransport ? (
            <span className={`badge transport ${session.transport}`}>
              {transportLabel(session.transport, catalog)}
            </span>
          ) : null}
          {session.launch_target_id ? (
            <span className="badge neutral">{session.launch_target_id}</span>
          ) : null}
          <span className={`status ${session.status}`}>
            {session.status.replace("_", " ")}
          </span>
        </div>
        <div className="session-card-title-row">
          {editingSessionId === session.id ? (
            <input
              className="inline-title-input"
              type="text"
              value={draftTitle}
              onChange={(e) => setDraftTitle(e.target.value)}
              onKeyDown={(e) => handleDraftKeyDown(e, session)}
              onBlur={() => commitEditing(session)}
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
              }}
              autoFocus
            />
          ) : (
            <>
              <h3 className="session-card-title">{session.title}</h3>
              {onSetTitle ? (
                <button
                  className="link-button edit-title-btn"
                  type="button"
                  onClick={(event) => handleSetTitle(event, session)}
                  title="Rename session"
                  aria-label="Rename session"
                >
                  ✎
                </button>
              ) : null}
            </>
          )}
        </div>
        <p className="muted session-card-path">
          {session.cwd}
        </p>
        <div className="session-card-meta">
          <p className="meta">
            {session.repo_name ?? "No repo"}
            {session.branch ? ` · ${session.branch}` : null}
            {session.source === "managed" ? " · managed" : " · attached"}
          </p>
          <p className="meta">
            Last activity {new Date(session.last_event_at).toLocaleString()}
          </p>
        </div>
        <div className="session-card-actions">
          {onSetPinned ? (
            <button
              className={`link-button pin-link ${pinned ? "active" : ""}`}
              type="button"
              onClick={(event) => handleTogglePin(event, session.id, !pinned)}
              aria-pressed={pinned}
            >
              {pinned ? "★ Pinned" : "☆ Pin"}
            </button>
          ) : null}
          {onTerminate && session.status !== "exited" ? (
            <button
              className="link-button danger-link"
              type="button"
              onClick={(event) => handleTerminate(event, session.id)}
            >
              Terminate
            </button>
          ) : null}
          {onDelete && session.status === "exited" ? (
            <button
              className="link-button danger-link"
              type="button"
              onClick={(event) => handleDelete(event, session.id)}
            >
              Delete
            </button>
          ) : null}
        </div>
      </Link>
    );
  }

  return (
    <section className="panel stack session-list-shell">
      <div className="field-row">
        <div className="session-list-summary">
          <h3>Sessions</h3>
          <p className="muted">
            {sessions.length} total · {activeCount} active · {exitedCount} exited
            {pinnedSessions.length ? ` · ${pinnedSessions.length} pinned` : null}
          </p>
        </div>
        {onDeleteExited && exitedCount > 0 ? (
          <button className="secondary" type="button" onClick={handleDeleteExited}>
            Delete exited ({exitedCount})
          </button>
        ) : null}
      </div>

      <SearchInput
        className="session-list-search"
        value={query}
        onChange={setQuery}
        placeholder='Search sessions... (e.g. "title:bug OR branch:main")'
      />

      {flatSearchResults !== null ? (
        <section className="session-section">
          {flatSearchResults.length > 0 ? (
            <div className="stack">{visibleItems.map(renderCard)}</div>
          ) : (
            <p className="muted" style={{ textAlign: "center", padding: "20px 0" }}>
              No sessions match your search.
            </p>
          )}
          {flatSearchResults.length > 0 ? (
            <Pager
              page={page}
              totalPages={totalPages}
              total={flatSearchResults.length}
              pageStart={showingFrom}
              pageEnd={showingTo}
              onPage={setPage}
              label="sessions"
            />
          ) : null}
        </section>
      ) : (
        <>
          {pinnedSessions.length > 0 ? (
            <section className="session-section session-section-pinned">
              <header className="session-section-header">
                <h4>Pinned</h4>
                <span className="meta">{pinnedSessions.length}</span>
              </header>
              <div className="stack">{pinnedSessions.map(renderCard)}</div>
            </section>
          ) : null}

          <section className="session-section">
            <header className="session-section-header">
              <h4>Recent</h4>
              {recentSessions.length > 0 ? (
                <span className="meta">{recentSessions.length}</span>
              ) : null}
            </header>
            {visibleItems.length > 0 ? (
              <div className="stack">{visibleItems.map(renderCard)}</div>
            ) : (
              <p className="muted">All sessions are pinned.</p>
            )}
            {recentSessions.length > 0 ? (
              <Pager
                page={page}
                totalPages={totalPages}
                total={recentSessions.length}
                pageStart={showingFrom}
                pageEnd={showingTo}
                onPage={setPage}
                label="sessions"
              />
            ) : null}
          </section>
        </>
      )}
    </section>
  );
}
