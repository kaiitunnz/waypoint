"use client";

import Link from "next/link";
import { MouseEvent, useEffect, useState } from "react";

import { SessionRecord } from "@/lib/types";
import { transportLabel } from "@/lib/transport";

interface SessionListProps {
  sessions: SessionRecord[];
  onDelete?: (sessionId: string) => void | Promise<void>;
  onDeleteExited?: () => void | Promise<void>;
  onTerminate?: (sessionId: string) => void | Promise<void>;
}

const PAGE_SIZE = 8;

export function SessionList({ sessions, onDelete, onDeleteExited, onTerminate }: SessionListProps) {
  const [expanded, setExpanded] = useState(false);
  const [page, setPage] = useState(1);

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

  function handleDeleteExited() {
    if (!onDeleteExited) {
      return;
    }
    if (!window.confirm("Delete all exited sessions and their transcripts? This cannot be undone.")) {
      return;
    }
    void onDeleteExited();
  }

  const exitedCount = sessions.filter((session) => session.status === "exited").length;
  const activeCount = sessions.length - exitedCount;
  const totalPages = Math.max(1, Math.ceil(sessions.length / PAGE_SIZE));

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
  const visibleSessions = sessions.slice(pageStart, pageStart + PAGE_SIZE);
  const showingFrom = pageStart + 1;
  const showingTo = Math.min(sessions.length, pageStart + PAGE_SIZE);

  return (
    <section className="panel stack session-list-shell">
      <div className="field-row">
        <div className="session-list-summary">
          <h3>Sessions</h3>
          <p className="muted">
            {sessions.length} total · {activeCount} active · {exitedCount} exited
          </p>
        </div>
        <button
          className="secondary"
          type="button"
          onClick={() => setExpanded((current) => !current)}
        >
          {expanded ? "Collapse" : `Show sessions (${sessions.length})`}
        </button>
      </div>
      {!expanded ? (
        <p className="muted">Collapsed by default to keep the home screen compact.</p>
      ) : (
        <>
          <div className="action-row list-actions">
            <span className="meta">
              Showing {showingFrom}-{showingTo} of {sessions.length}
            </span>
            {totalPages > 1 ? (
              <div className="action-row pagination-controls">
                <button
                  className="secondary"
                  type="button"
                  onClick={() => setPage((current) => Math.max(1, current - 1))}
                  disabled={page === 1}
                >
                  Previous
                </button>
                <span className="meta">
                  Page {page} of {totalPages}
                </span>
                <button
                  className="secondary"
                  type="button"
                  onClick={() =>
                    setPage((current) => Math.min(totalPages, current + 1))
                  }
                  disabled={page === totalPages}
                >
                  Next
                </button>
              </div>
            ) : null}
            {onDeleteExited && exitedCount > 0 ? (
              <button className="secondary" type="button" onClick={handleDeleteExited}>
                Delete exited ({exitedCount})
              </button>
            ) : null}
          </div>
          <div className="stack">
            {visibleSessions.map((session) => (
              <Link className="panel session-card" href={`/session/${session.id}`} key={session.id}>
                <div className="session-row">
                  <span className={`badge ${session.backend}`}>
                    {session.backend === "codex" ? "Codex" : "Claude"}
                  </span>
                  <span className={`badge transport ${session.transport}`}>
                    {transportLabel(session.transport)}
                  </span>
                  {session.launch_target_id ? (
                    <span className="badge neutral">{session.launch_target_id}</span>
                  ) : null}
                  <span className={`status ${session.status}`}>
                    {session.status.replace("_", " ")}
                  </span>
                </div>
                <h3 className="session-card-title">{session.title}</h3>
                <p className="muted session-card-path">
                  {session.remote_cwd ?? session.cwd}
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
              </Link>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
