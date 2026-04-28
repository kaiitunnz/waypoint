"use client";

import { useEffect, useState } from "react";

import { BackendSwitcher } from "@/components/BackendSwitcher";
import { LaunchPanel } from "@/components/LaunchPanel";
import { LoginForm } from "@/components/LoginForm";
import { SessionList } from "@/components/SessionList";
import {
  attachTmux,
  connectSessionsSocket,
  createSession,
  deleteSession as deleteSessionRequest,
  fetchSessions,
  isAuthError,
  login,
} from "@/lib/api";
import { clearToken, readHost, readToken, writeHost, writeToken } from "@/lib/store";
import { Backend, SessionEnvelope, SessionRecord } from "@/lib/types";

type ConnectionState = "idle" | "connecting" | "open" | "reconnecting";

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

export default function HomePage() {
  const [host, setHost] = useState("");
  const [token, setToken] = useState("");
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [error, setError] = useState("");
  const [connection, setConnection] = useState<ConnectionState>("idle");

  useEffect(() => {
    const currentHost = readHost();
    const currentToken = readToken();
    setHost(currentHost);
    setToken(currentToken);
  }, []);

  useEffect(() => {
    if (!host || !token) {
      setConnection("idle");
      return;
    }
    let active = true;
    fetchSessions(host, token)
      .then((items) => {
        if (active) {
          setSessions(items);
        }
      })
      .catch((fetchError) => {
        if (active) {
          if (isAuthError(fetchError)) {
            resetAuthState("Session expired. Log in again.");
            return;
          }
          setError(fetchError instanceof Error ? fetchError.message : "failed to fetch sessions");
        }
      });

    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    function connect() {
      setConnection(attempt === 0 ? "connecting" : "reconnecting");
      socket = connectSessionsSocket(
        host,
        token,
        (message: SessionEnvelope) => {
          if (message.type === "session_list_update") {
            setSessions(message.payload.sessions as SessionRecord[]);
          }
          if (message.type === "auth_revoked") {
            resetAuthState("Session expired. Log in again.");
          }
        },
        () => {
          if (active) {
            resetAuthState("Session expired. Log in again.");
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
  }, [host, token]);

  async function handleLogin(nextHost: string, password: string) {
    const nextToken = await login(nextHost, password);
    writeHost(nextHost);
    writeToken(nextToken);
    setHost(nextHost);
    setToken(nextToken);
    setError("");
  }

  async function handleCreate(backend: Backend, cwd: string, title: string) {
    try {
      const session = await createSession(host, token, {
        backend,
        cwd,
        title: title || null,
        source_mode: "managed",
        args: [],
      });
      setSessions((current) => [session, ...current.filter((item) => item.id !== session.id)]);
    } catch (createError) {
      if (isAuthError(createError)) {
        resetAuthState("Session expired. Log in again.");
        return;
      }
      setError(createError instanceof Error ? createError.message : "failed to create session");
    }
  }

  async function handleAttach(target: string, backendHint: Backend) {
    try {
      const session = await attachTmux(host, token, {
        tmux_target: target,
        backend_hint: backendHint,
      });
      setSessions((current) => [session, ...current.filter((item) => item.id !== session.id)]);
    } catch (attachError) {
      if (isAuthError(attachError)) {
        resetAuthState("Session expired. Log in again.");
        return;
      }
      setError(attachError instanceof Error ? attachError.message : "failed to attach session");
    }
  }

  function resetAuthState(message: string) {
    clearToken();
    setToken("");
    setSessions([]);
    setError(message);
  }

  async function handleDelete(sessionId: string) {
    try {
      await deleteSessionRequest(host, token, sessionId);
      setSessions((current) => current.filter((session) => session.id !== sessionId));
    } catch (deleteError) {
      if (isAuthError(deleteError)) {
        resetAuthState("Session expired. Log in again.");
        return;
      }
      setError(deleteError instanceof Error ? deleteError.message : "failed to delete session");
    }
  }

  function handleSwitchBackend(nextHost: string) {
    writeHost(nextHost);
    clearToken();
    setHost(nextHost);
    setToken("");
    setSessions([]);
    setError("Switched backend. Log in to continue.");
  }

  return (
    <main className="page-shell">
      <section className="hero">
        <p className="eyebrow">Waypoint</p>
        <h1>Remote control for live AI coding sessions.</h1>
        <p className="lede">
          Check in on Claude Code and Codex from your phone, respond when they need input, and drop to raw terminal
          when the transcript gets fuzzy.
        </p>
      </section>
      {!token ? <LoginForm defaultHost={host} onSubmit={handleLogin} /> : null}
      {token ? (
        <BackendSwitcher
          host={host}
          token={token}
          onSwitch={handleSwitchBackend}
          onAuthFailure={() => resetAuthState("Session expired. Log in again.")}
        />
      ) : null}
      {token ? <LaunchPanel onAttach={handleAttach} onCreate={handleCreate} /> : null}
      {error ? <p className="error">{error}</p> : null}
      {token && connection !== "open" && connection !== "idle" ? (
        <p className="connection-banner muted">
          {connection === "connecting" ? "Connecting…" : "Reconnecting…"}
        </p>
      ) : null}
      {token ? <SessionList sessions={sessions} onDelete={handleDelete} /> : null}
    </main>
  );
}
