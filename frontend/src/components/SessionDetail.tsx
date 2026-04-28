"use client";

import { useEffect, useState } from "react";

import { connectSessionSocket, fetchEvents, fetchSession, fetchTerminalSnapshot, postAction, sendInput } from "@/lib/api";
import { EventRecord, SessionEnvelope, SessionRecord } from "@/lib/types";

interface SessionDetailProps {
  host: string;
  token: string;
  sessionId: string;
}

type ViewMode = "chat" | "terminal";

export function SessionDetail({ host, token, sessionId }: SessionDetailProps) {
  const [session, setSession] = useState<SessionRecord | null>(null);
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [snapshot, setSnapshot] = useState("");
  const [draft, setDraft] = useState("");
  const [view, setView] = useState<ViewMode>("chat");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const [loadedSession, loadedEvents, loadedSnapshot] = await Promise.all([
          fetchSession(host, token, sessionId),
          fetchEvents(host, token, sessionId),
          fetchTerminalSnapshot(host, token, sessionId),
        ]);
        if (!active) {
          return;
        }
        setSession(loadedSession);
        setEvents(loadedEvents);
        setSnapshot(loadedSnapshot);
      } catch (loadError) {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : "failed to load session");
        }
      }
    }
    load();
    const socket = connectSessionSocket(host, token, sessionId, (message: SessionEnvelope) => {
      if (message.type === "event") {
        const event = message.payload.event as EventRecord;
        setEvents((current) => mergeEvents(current, event));
        setSnapshot((current) => `${current}${current ? "\n\n" : ""}${event.text}`);
      }
      if (message.type === "session_state") {
        setSession(message.payload.session as SessionRecord);
      }
    });
    return () => {
      active = false;
      socket.close();
    };
  }, [host, token, sessionId]);

  async function submitInput() {
    if (!draft.trim()) {
      return;
    }
    await sendInput(host, token, sessionId, draft);
    setDraft("");
  }

  async function runAction(action: "interrupt" | "resume") {
    await postAction(host, token, sessionId, action);
  }

  return (
    <section className="stack">
      {session ? (
        <header className="panel">
          <div className="session-row">
            <span className={`badge ${session.backend}`}>{session.backend === "codex" ? "Codex" : "Claude"}</span>
            <span className={`status ${session.status}`}>{session.status.replace("_", " ")}</span>
          </div>
          <h2>{session.title}</h2>
          <p className="muted">{session.cwd}</p>
          <p className="meta">
            {session.source === "managed" ? "Structured wrapper path" : "Heuristic tmux attachment"}
          </p>
        </header>
      ) : null}
      {error ? <p className="error">{error}</p> : null}
      <div className="view-toggle">
        <button className={view === "chat" ? "primary" : "secondary"} onClick={() => setView("chat")} type="button">
          Chat
        </button>
        <button
          className={view === "terminal" ? "primary" : "secondary"}
          onClick={() => setView("terminal")}
          type="button"
        >
          Terminal
        </button>
      </div>
      {view === "chat" ? (
        <section className="stack">
          {events.map((event) => (
            <article className={`panel transcript ${event.kind}`} key={`${event.sequence}-${event.id ?? "local"}`}>
              <div className="session-row">
                <span className="badge neutral">{event.kind.replaceAll("_", " ")}</span>
                <span className="muted">{new Date(event.ts).toLocaleTimeString()}</span>
              </div>
              <pre>{event.text}</pre>
            </article>
          ))}
        </section>
      ) : (
        <section className="panel terminal">
          <pre>{snapshot || "No terminal output yet."}</pre>
        </section>
      )}
      <section className="panel stack">
        <label className="field">
          <span>Reply</span>
          <textarea rows={4} value={draft} onChange={(event) => setDraft(event.target.value)} />
        </label>
        <div className="action-row">
          <button className="primary" onClick={() => void submitInput()} type="button">
            Send
          </button>
          <button className="secondary" onClick={() => void runAction("interrupt")} type="button">
            Interrupt
          </button>
          <button className="secondary" onClick={() => void runAction("resume")} type="button">
            Resume
          </button>
        </div>
      </section>
    </section>
  );
}

function mergeEvents(current: EventRecord[], incoming: EventRecord): EventRecord[] {
  const exists = current.some((event) => event.id === incoming.id || event.sequence === incoming.sequence);
  if (exists) {
    return current;
  }
  return [...current, incoming];
}
