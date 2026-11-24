"use client";

import Link from "next/link";
import { MouseEvent, ReactNode, useEffect, useMemo, useState } from "react";

import {
  defaultTransportFor,
  displayAgentFor,
  humaniseBackend,
  transportLabel,
  type BackendCatalog,
} from "@waypoint/web-core/backends";
import { matchesQuery, parseQuery } from "@waypoint/web-core/search";
import { formatClock } from "@waypoint/web-core/scheduleTime";
import {
  byRecency,
  SESSION_SEARCH_FIELDS,
  useMergedSessionPage,
  useSessionHistory,
} from "@waypoint/web-core/sessionHistory";
import { SessionRecord } from "@waypoint/web-core/types";
import { PANEL_SHOW_EXITED_KEY, useShowExitedSessions } from "@waypoint/web-core/hooks/useShowExitedSessions";

import { SearchInput } from "./SearchInput";
import { Pager } from "@/components/Pager";

interface SessionListProps {
  host: string;
  token: string;
  /** Live and pinned sessions; exited history is paged from the server. */
  sessions: SessionRecord[];
  /** `history_revision` from the latest session list frame. */
  historyRevision: string;
  /** Sources hidden from this list (live rows are filtered by the caller). */
  hiddenSources: readonly string[];
  catalog?: BackendCatalog;
  onAuthFailure?: () => void;
  onDelete?: (sessionId: string) => void | Promise<void>;
  onDeleteExited?: () => void | Promise<void>;
  onTerminate?: (sessionId: string) => void | Promise<void>;
  onSetPinned?: (sessionId: string, pinned: boolean) => void | Promise<void>;
  onSetTitle?: (sessionId: string, title: string) => void | Promise<void>;
  onOpenSettings?: (session: SessionRecord) => void;
}

const PAGE_SIZE = 10;

export function SessionList({
  host,
  token,
  sessions,
  historyRevision,
  hiddenSources,
  catalog,
  onAuthFailure,
  onDelete,
  onDeleteExited,
  onTerminate,
  onSetPinned,
  onSetTitle,
  onOpenSettings,
}: SessionListProps) {
  const [page, setPage] = useState(1);
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [query, setQuery] = useState("");
  const [showExited, setShowExited] = useShowExitedSessions(PANEL_SHOW_EXITED_KEY);

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

  function handleOpenSettings(
    event: MouseEvent<HTMLButtonElement>,
    session: SessionRecord,
  ) {
    event.preventDefault();
    event.stopPropagation();
    onOpenSettings?.(session);
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

  const trimmedQuery = query.trim();
  const searching = trimmedQuery !== "";
  const historyCounts = useSessionHistory({
    host,
    token,
    query: "",
    withItems: false,
    revision: historyRevision,
    excludeSources: hiddenSources,
    onAuthError: onAuthFailure,
  });
  const history = useSessionHistory({
    host,
    token,
    query,
    withItems: true,
    enabled: showExited,
    revision: historyRevision,
    excludeSources: hiddenSources,
    pageSize: PAGE_SIZE,
    onAuthError: onAuthFailure,
  });
  const historyTotal = historyCounts.total ?? 0;

  const { pinnedSessions, recentLive, exitedLive, activeCount } = useMemo(() => {
    const exited = sessions.filter((session) => session.status === "exited").length;
    let visible = showExited
      ? sessions
      : sessions.filter((session) => session.status !== "exited");
    if (searching) {
      const terms = parseQuery(trimmedQuery);
      visible = visible.filter((session) =>
        matchesQuery(session, terms, SESSION_SEARCH_FIELDS),
      );
    }
    const pinned = visible
      .filter((session) => session.pinned_at)
      .sort(
        searching
          ? byRecency
          : (a, b) => (b.pinned_at ?? "").localeCompare(a.pinned_at ?? ""),
      );
    return {
      pinnedSessions: pinned,
      recentLive: visible.filter((session) => !session.pinned_at),
      exitedLive: exited,
      activeCount: sessions.length - exited,
    };
  }, [sessions, searching, trimmedQuery, showExited]);

  // Search results are one flat list (pinned matches first); otherwise pinned
  // sessions get their own section and only recent ones are paged.
  const recentPage = useMergedSessionPage(
    recentLive,
    history,
    showExited,
    page,
    PAGE_SIZE,
    searching ? pinnedSessions : undefined,
  );
  const totalPages = recentPage.totalPages;
  const exitedCount = exitedLive + historyTotal;
  const totalCount = sessions.length + historyTotal;

  useEffect(() => {
    setPage(1);
  }, [query]);

  useEffect(() => {
    setPage((current) => Math.min(current, totalPages));
  }, [totalPages]);

  if (!sessions.length && historyCounts.total === 0) {
    return (
      <section className="panel">
        <h3>No sessions yet</h3>
        <p className="muted">Launch or attach a session to start monitoring from your phone.</p>
      </section>
    );
  }

  const pageStart = (page - 1) * PAGE_SIZE;
  const visibleItems = recentPage.items;
  const listTotal = recentPage.total;
  const showingFrom = visibleItems.length === 0 ? 0 : pageStart + 1;
  const showingTo = pageStart + visibleItems.length;
  const historyBusy = showExited && (history.pending || recentPage.filling);
  const historyNote = searching ? "Searching older sessions…" : "Loading older sessions…";
  const historyStatus =
    showExited && history.error ? (
      <div className="session-list-tail" role="status">
        <span>Older sessions failed to load.</span>
        <button className="secondary" type="button" onClick={history.retry}>
          Retry
        </button>
      </div>
    ) : historyBusy ? (
      <p className="session-list-tail muted" role="status">
        {historyNote}
      </p>
    ) : null;

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
      <article className="panel session-card" key={session.id}>
        <Link
          className="session-card-hit-target"
          href={`/session/${session.id}`}
          aria-label={`Open session ${session.title}`}
        >
          <span aria-hidden className="session-card-hit-label" />
        </Link>
        <div className="session-row">
          <div className="session-card-badges">
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
            {session.account_profile_label ? (
              <span
                className="badge account-profile"
                title={`Account profile: ${session.account_profile_label}`}
              >
                {session.account_profile_label}
              </span>
            ) : null}
            {session.focus ? (
              <span className="badge focus" title="Focus is on">
                ◎ focus
              </span>
            ) : null}
            <span className={`status ${session.status}`}>
              {session.status.replace("_", " ")}
            </span>
          </div>
          <span
            className="session-card-when"
            title={new Date(session.last_event_at).toLocaleString()}
          >
            {formatClock(new Date(session.last_event_at))}
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
              <h3 className="session-card-title session-card-selectable">{session.title}</h3>
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
        <p className="muted session-card-path session-card-selectable">
          {session.cwd}
        </p>
        <div className="session-card-meta">
          <p className="meta session-card-selectable">
            {session.repo_name ?? "No repo"}
            {session.branch ? ` · ${session.branch}` : null}
            {session.source === "managed" ? " · managed" : " · attached"}
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
          {onOpenSettings ? (
            <button
              className="link-button settings-link"
              type="button"
              onClick={(event) => handleOpenSettings(event, session)}
            >
              {"⚙︎ Settings"}
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
      </article>
    );
  }

  return (
    <section className="panel stack session-list-shell">
      <div className="field-row session-list-header">
        <div className="session-list-summary">
          <h3>Sessions</h3>
          <p className="muted">
            {totalCount} total · {activeCount} active · {exitedCount} exited
            {pinnedSessions.length ? ` · ${pinnedSessions.length} pinned` : null}
          </p>
        </div>
        {onDeleteExited && exitedCount > 0 ? (
          <button className="secondary" type="button" onClick={handleDeleteExited}>
            Delete exited ({exitedCount})
          </button>
        ) : null}
      </div>

      <div className="session-list-search-row">
        <SearchInput
          className="session-list-search"
          value={query}
          onChange={setQuery}
          placeholder='Search sessions... (e.g. "title:bug OR branch:main")'
        />
        {exitedCount > 0 ? (
          <button
            type="button"
            className="exited-toggle"
            aria-pressed={!showExited}
            onClick={() => setShowExited(!showExited)}
          >
            {showExited ? "hide exited" : "show exited"}
          </button>
        ) : null}
      </div>

      {searching ? (
        <section className="session-section">
          {visibleItems.length > 0 ? (
            <Pager
              page={page}
              totalPages={totalPages}
              total={listTotal}
              pageStart={showingFrom}
              pageEnd={showingTo}
              onPage={setPage}
              label="sessions"
              compact
            />
          ) : null}
          {visibleItems.length > 0 ? (
            <div className="stack">{visibleItems.map(renderCard)}</div>
          ) : historyBusy || history.error ? null : (
            <p className="muted" style={{ textAlign: "center", padding: "20px 0" }}>
              No sessions match your search.
            </p>
          )}
          {historyStatus}
          {visibleItems.length > 0 ? (
            <Pager
              page={page}
              totalPages={totalPages}
              total={listTotal}
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
              {listTotal > 0 ? <span className="meta">{listTotal}</span> : null}
              {visibleItems.length > 0 ? (
                <Pager
                  page={page}
                  totalPages={totalPages}
                  total={listTotal}
                  pageStart={showingFrom}
                  pageEnd={showingTo}
                  onPage={setPage}
                  label="sessions"
                  compact
                />
              ) : null}
            </header>
            {visibleItems.length > 0 ? (
              <div className="stack">{visibleItems.map(renderCard)}</div>
            ) : historyBusy || history.error ? null : pinnedSessions.length > 0 ? (
              <p className="muted">All sessions are pinned.</p>
            ) : (
              <p className="muted">No recent sessions.</p>
            )}
            {historyStatus}
            {visibleItems.length > 0 ? (
              <Pager
                page={page}
                totalPages={totalPages}
                total={listTotal}
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
