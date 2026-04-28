"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

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
import { TranscriptCard } from "@/components/TranscriptCard";
import { EventRecord, SessionEnvelope, SessionRecord } from "@/lib/types";

interface SessionDetailProps {
  host: string;
  token: string;
  sessionId: string;
  onAuthFailure?: () => void;
}

type ViewMode = "chat" | "terminal";
type ConnectionState = "connecting" | "open" | "reconnecting";

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

export function SessionDetail({ host, token, sessionId, onAuthFailure }: SessionDetailProps) {
  const router = useRouter();
  const [session, setSession] = useState<SessionRecord | null>(null);
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [snapshot, setSnapshot] = useState("");
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const [draft, setDraft] = useState("");
  const [view, setView] = useState<ViewMode>("chat");
  const [error, setError] = useState("");
  const [connection, setConnection] = useState<ConnectionState>("connecting");

  const refreshSnapshot = useCallback(async () => {
    setSnapshotLoading(true);
    try {
      const text = await fetchTerminalSnapshot(host, token, sessionId);
      setSnapshot(stripAnsi(text));
    } catch (snapshotError) {
      if (isAuthError(snapshotError)) {
        handleAuthFailure();
        return;
      }
      setError(snapshotError instanceof Error ? snapshotError.message : "failed to fetch terminal snapshot");
    } finally {
      setSnapshotLoading(false);
    }
    // handleAuthFailure is stable for our purposes; depending on host/token/sessionId.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [host, token, sessionId]);

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

    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    function connect() {
      setConnection(attempt === 0 ? "connecting" : "reconnecting");
      socket = connectSessionSocket(
        host,
        token,
        sessionId,
        (message: SessionEnvelope) => {
          if (message.type === "event") {
            const event = sanitizeEvent(message.payload.event as EventRecord);
            setEvents((current) => mergeEvents(current, event));
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
        {
          onOpen: () => {
            attempt = 0;
            setConnection("open");
          },
          onClose: () => {
            if (!active) {
              return;
            }
            const delay = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** attempt);
            attempt += 1;
            setConnection("reconnecting");
            reconnectTimer = setTimeout(connect, delay);
          },
        },
      );
    }

    connect();

    return () => {
      active = false;
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
      }
      socket?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [host, token, sessionId]);

  useEffect(() => {
    if (view === "terminal") {
      void refreshSnapshot();
    }
  }, [view, refreshSnapshot]);

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
      {connection !== "open" ? (
        <p className="connection-banner muted">
          {connection === "connecting" ? "Connecting…" : "Reconnecting…"}
        </p>
      ) : null}
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
          {session
            ? events.map((event) => (
                <TranscriptCard
                  event={event}
                  transport={session.transport}
                  key={`${event.sequence}-${event.id ?? "local"}`}
                />
              ))
            : null}
        </section>
      ) : (
        <section className="panel terminal stack">
          <div className="action-row">
            <button
              className="secondary"
              onClick={() => void refreshSnapshot()}
              type="button"
              disabled={snapshotLoading}
            >
              {snapshotLoading ? "Refreshing…" : "Refresh"}
            </button>
          </div>
          <pre>{snapshot || (snapshotLoading ? "Loading…" : "No terminal output yet.")}</pre>
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
