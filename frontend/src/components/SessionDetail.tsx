"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import {
  connectSessionSocket,
  fetchEvents,
  fetchSession,
  fetchTerminalSnapshot,
  isAuthError,
  postAction,
  sendInput,
} from "@/lib/api";
import { clearToken } from "@/lib/store";
import { EventRecord, SessionEnvelope, SessionRecord } from "@/lib/types";

interface SessionDetailProps {
  host: string;
  token: string;
  sessionId: string;
  onAuthFailure?: () => void;
}

type ViewMode = "chat" | "terminal";

export function SessionDetail({ host, token, sessionId, onAuthFailure }: SessionDetailProps) {
  const router = useRouter();
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
        setEvents(loadedEvents.map(sanitizeEvent));
        setSnapshot(stripAnsi(loadedSnapshot));
      } catch (loadError) {
        if (active) {
          if (isAuthError(loadError)) {
            handleAuthFailure();
            return;
          }
          setError(loadError instanceof Error ? loadError.message : "failed to load session");
        }
      }
    }
    load();
    const socket = connectSessionSocket(
      host,
      token,
      sessionId,
      (message: SessionEnvelope) => {
        if (message.type === "event") {
          const event = sanitizeEvent(message.payload.event as EventRecord);
          setEvents((current) => mergeEvents(current, event));
          setSnapshot((current) => `${current}${current ? "\n\n" : ""}${stripAnsi(event.text)}`);
        }
        if (message.type === "session_state") {
          setSession(message.payload.session as SessionRecord);
        }
        if (message.type === "auth_revoked") {
          handleAuthFailure();
        }
      },
      () => {
        if (active) {
          handleAuthFailure();
        }
      },
    );
    return () => {
      active = false;
      socket.close();
    };
  }, [host, token, sessionId]);

  async function submitInput() {
    if (!draft.trim()) {
      return;
    }
    try {
      await sendInput(host, token, sessionId, draft);
      setDraft("");
    } catch (sendError) {
      if (isAuthError(sendError)) {
        handleAuthFailure();
        return;
      }
      setError(sendError instanceof Error ? sendError.message : "failed to send input");
    }
  }

  async function runAction(action: "interrupt" | "resume") {
    try {
      await postAction(host, token, sessionId, action);
    } catch (actionError) {
      if (isAuthError(actionError)) {
        handleAuthFailure();
        return;
      }
      setError(actionError instanceof Error ? actionError.message : `failed to ${action}`);
    }
  }

  function handleAuthFailure() {
    clearToken();
    onAuthFailure?.();
    router.replace("/");
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

function sanitizeEvent(event: EventRecord): EventRecord {
  return {
    ...event,
    text: stripAnsi(event.text),
  };
}

function stripAnsi(text: string): string {
  return text
    .replace(/\u001B\][\s\S]*?(?:\u0007|\u001B\\)/g, "")
    .replace(/\u001B\[[0-?]*[ -/]*[@-~]/g, "")
    .replace(/\u001B[@-_]/g, "");
}
