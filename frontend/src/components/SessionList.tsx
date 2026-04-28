"use client";

import Link from "next/link";

import { SessionRecord } from "@/lib/types";

interface SessionListProps {
  sessions: SessionRecord[];
}

export function SessionList({ sessions }: SessionListProps) {
  if (!sessions.length) {
    return (
      <section className="panel">
        <h3>No sessions yet</h3>
        <p className="muted">Launch or attach a session to start monitoring from your phone.</p>
      </section>
    );
  }

  return (
    <section className="stack">
      {sessions.map((session) => (
        <Link className="panel session-card" href={`/session/${session.id}`} key={session.id}>
          <div className="session-row">
            <span className={`badge ${session.backend}`}>{session.backend === "codex" ? "Codex" : "Claude"}</span>
            <span className={`status ${session.status}`}>{session.status.replace("_", " ")}</span>
          </div>
          <h3>{session.title}</h3>
          <p className="muted">{session.cwd}</p>
          <p className="meta">
            {session.source === "managed" ? "Managed" : "Attached"} · last activity{" "}
            {new Date(session.last_event_at).toLocaleString()}
          </p>
        </Link>
      ))}
    </section>
  );
}
