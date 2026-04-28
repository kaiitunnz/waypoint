"use client";

import Link from "next/link";
import { MouseEvent } from "react";

import { SessionRecord } from "@/lib/types";
import { fidelityFor, transportLabel } from "@/lib/transport";

interface SessionListProps {
  sessions: SessionRecord[];
  onDelete?: (sessionId: string) => void | Promise<void>;
  onDeleteExited?: () => void | Promise<void>;
  onTerminate?: (sessionId: string) => void | Promise<void>;
}

export function SessionList({ sessions, onDelete, onDeleteExited, onTerminate }: SessionListProps) {
  if (!sessions.length) {
    return (
      <section className="panel">
        <h3>No sessions yet</h3>
        <p className="muted">Launch or attach a session to start monitoring from your phone.</p>
      </section>
    );
  }

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

  return (
    <section className="stack">
      {onDeleteExited && exitedCount > 0 ? (
        <div className="action-row list-actions">
          <button className="secondary" type="button" onClick={handleDeleteExited}>
            Delete exited sessions ({exitedCount})
          </button>
        </div>
      ) : null}
      {sessions.map((session) => (
        <Link className="panel session-card" href={`/session/${session.id}`} key={session.id}>
          <div className="session-row">
            <span className={`badge ${session.backend}`}>{session.backend === "codex" ? "Codex" : "Claude"}</span>
            <span className={`badge transport ${session.transport}`}>{transportLabel(session.transport)}</span>
            <span className={`badge fidelity ${fidelityFor(session.transport)}`}>{fidelityFor(session.transport)}</span>
            <span className={`status ${session.status}`}>{session.status.replace("_", " ")}</span>
          </div>
          <h3>{session.title}</h3>
          <p className="muted">{session.cwd}</p>
          {session.remote_cwd ? <p className="muted">Remote: {session.remote_cwd}</p> : null}
          <p className="meta">
            {session.source === "managed" ? "Managed" : "Attached"} · last activity{" "}
            {new Date(session.last_event_at).toLocaleString()}
          </p>
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
    </section>
  );
}
