"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import {
  approveSession,
  connectSessionSocket,
  fetchEvents,
  fetchSession,
  fetchTerminalSnapshot,
  isAuthError,
  postAction,
  sendInput,
} from "@/lib/api";
import { clearToken } from "@/lib/store";
import {
  fidelityFor,
  supportsResume,
  supportsStructuredApproval,
  transportLabel,
} from "@/lib/transport";
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

  async function submitApproval(decision: string) {
    try {
      await approveSession(host, token, sessionId, decision);
    } catch (approvalError) {
      if (isAuthError(approvalError)) {
        handleAuthFailure();
        return;
      }
      setError(approvalError instanceof Error ? approvalError.message : "failed to send approval");
    }
  }

  function handleAuthFailure() {
    clearToken();
    onAuthFailure?.();
    router.replace("/");
  }

  const pendingApproval =
    session && supportsStructuredApproval(session.transport) && session.status === "waiting_input"
      ? findPendingApproval(events)
      : null;

  return (
    <section className="stack">
      {session ? (
        <header className="panel">
          <div className="session-row">
            <span className={`badge ${session.backend}`}>{session.backend === "codex" ? "Codex" : "Claude"}</span>
            <span className={`badge transport ${session.transport}`}>{transportLabel(session.transport)}</span>
            <span className={`badge fidelity ${fidelityFor(session.transport)}`}>{fidelityFor(session.transport)}</span>
            <span className={`status ${session.status}`}>{session.status.replace("_", " ")}</span>
          </div>
          <h2>{session.title}</h2>
          <p className="muted">{session.cwd}</p>
          <p className="meta">
            {session.source === "managed" ? "Managed" : "Attached"}
            {session.thread_id ? ` · thread ${session.thread_id}` : null}
          </p>
        </header>
      ) : null}
      {error ? <p className="error">{error}</p> : null}
      {pendingApproval ? (
        <ApprovalCard event={pendingApproval} onDecide={submitApproval} />
      ) : null}
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
          {session && supportsResume(session.transport) ? (
            <button className="secondary" onClick={() => void runAction("resume")} type="button">
              Resume
            </button>
          ) : null}
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

function findPendingApproval(events: EventRecord[]): EventRecord | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.kind === "approval_request") {
      return event;
    }
    if (event.kind === "system_note" && /Approval response sent/i.test(event.text)) {
      return null;
    }
  }
  return null;
}

interface ApprovalCardProps {
  event: EventRecord;
  onDecide: (decision: string) => void | Promise<void>;
}

function ApprovalCard({ event, onDecide }: ApprovalCardProps) {
  const method = typeof event.metadata.method === "string" ? event.metadata.method : null;
  return (
    <section className="panel approval">
      <div className="session-row">
        <span className="badge fidelity structured">approval</span>
        {method ? <span className="muted">{method}</span> : null}
      </div>
      <pre>{event.text}</pre>
      <div className="action-row">
        <button className="primary" onClick={() => void onDecide("accept")} type="button">
          Approve
        </button>
        <button className="secondary" onClick={() => void onDecide("acceptForSession")} type="button">
          Approve for session
        </button>
        <button className="secondary" onClick={() => void onDecide("decline")} type="button">
          Decline
        </button>
        <button className="secondary" onClick={() => void onDecide("cancel")} type="button">
          Cancel
        </button>
      </div>
    </section>
  );
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
