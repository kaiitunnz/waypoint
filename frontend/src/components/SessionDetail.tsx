"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import {
  approveSession,
  connectSessionSocket,
  deleteSession as deleteSessionRequest,
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
type FilterMode = "important" | "all";
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
  const [filterMode, setFilterMode] = useState<FilterMode>("important");
  const [error, setError] = useState("");
  const [connection, setConnection] = useState<ConnectionState>("connecting");

  const handleAuthFailure = useCallback(() => {
    clearToken();
    onAuthFailure?.();
    router.replace("/");
  }, [onAuthFailure, router]);

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
  }, [handleAuthFailure, host, token, sessionId]);

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
        const coalesced = loadedEvents
          .map(sanitizeEvent)
          .reduce<EventRecord[]>((acc, event) => mergeEvents(acc, event), []);
        setEvents(coalesced);
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
  }, [handleAuthFailure, host, token, sessionId]);

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

  async function terminate() {
    if (!window.confirm("Terminate this session? Any running command will be stopped.")) {
      return;
    }
    try {
      await postAction(host, token, sessionId, "terminate");
    } catch (terminateError) {
      if (isAuthError(terminateError)) {
        handleAuthFailure();
        return;
      }
      setError(terminateError instanceof Error ? terminateError.message : "failed to terminate");
    }
  }

  async function removeFromList() {
    if (!window.confirm("Delete this session and its transcript? This cannot be undone.")) {
      return;
    }
    try {
      await deleteSessionRequest(host, token, sessionId);
      router.replace("/");
    } catch (deleteError) {
      if (isAuthError(deleteError)) {
        handleAuthFailure();
        return;
      }
      setError(deleteError instanceof Error ? deleteError.message : "failed to delete");
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

  const pendingApproval =
    session && supportsStructuredApproval(session.transport) && session.status === "waiting_input"
      ? findPendingApproval(events)
      : null;
  const visibleEvents = filterMode === "all" ? events : events.filter(isImportantEvent);
  const hiddenEventCount = events.length - visibleEvents.length;

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
          {session.remote_cwd ? <p className="muted">Remote: {session.remote_cwd}</p> : null}
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
          <div className="action-row">
            <button
              className={filterMode === "important" ? "primary" : "secondary"}
              onClick={() => setFilterMode("important")}
              type="button"
            >
              Important
            </button>
            <button
              className={filterMode === "all" ? "primary" : "secondary"}
              onClick={() => setFilterMode("all")}
              type="button"
            >
              All events
            </button>
            {filterMode === "important" && hiddenEventCount > 0 ? (
              <span className="muted">
                Hiding {hiddenEventCount} low-signal event{hiddenEventCount === 1 ? "" : "s"}
              </span>
            ) : null}
          </div>
          {session
            ? visibleEvents.map((event) => (
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
          <textarea
            rows={4}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            disabled={session?.status === "exited"}
          />
        </label>
        <div className="action-row">
          <button
            className="primary"
            onClick={() => void submitInput()}
            type="button"
            disabled={session?.status === "exited"}
          >
            Send
          </button>
          <button
            className="secondary"
            onClick={() => void runAction("interrupt")}
            type="button"
            disabled={session?.status === "exited"}
          >
            Interrupt
          </button>
          {session && supportsResume(session.transport) ? (
            <button
              className="secondary"
              onClick={() => void runAction("resume")}
              type="button"
              disabled={session.status === "exited"}
            >
              Resume
            </button>
          ) : null}
          {session && session.status !== "exited" ? (
            <button className="danger" onClick={() => void terminate()} type="button">
              Terminate
            </button>
          ) : null}
          {session && session.status === "exited" ? (
            <button className="danger" onClick={() => void removeFromList()} type="button">
              Delete
            </button>
          ) : null}
        </div>
      </section>
    </section>
  );
}

function mergeEvents(current: EventRecord[], incoming: EventRecord): EventRecord[] {
  const dup = current.some((event) => event.id === incoming.id || event.sequence === incoming.sequence);
  if (dup) {
    return current;
  }
  const incomingItemId = readItemId(incoming);
  if (incoming.kind === "agent_output" && incomingItemId) {
    const index = current.findIndex(
      (event) => event.kind === "agent_output" && readItemId(event) === incomingItemId,
    );
    if (index !== -1) {
      const next = current.slice();
      const existing = next[index];
      next[index] = {
        ...existing,
        text: `${existing.text}${incoming.text}`,
        ts: incoming.ts,
        sequence: incoming.sequence,
      };
      return next;
    }
  }
  return [...current, incoming];
}

function readItemId(event: EventRecord): string | null {
  const meta = event.metadata;
  if (typeof meta?.item_id === "string" && meta.item_id) {
    return meta.item_id;
  }
  return null;
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

function isImportantEvent(event: EventRecord): boolean {
  switch (event.kind) {
    case "user_input":
    case "agent_output":
    case "tool_call":
    case "tool_result":
    case "approval_request":
      return true;
    case "system_note":
    case "status_update":
      return /(approval response|attached|started|terminated|interrupt|resume|failed|error|exited)/i.test(event.text);
    case "raw_terminal_chunk":
      return false;
    default:
      return false;
  }
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
