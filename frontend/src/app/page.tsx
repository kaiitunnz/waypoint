"use client";

import Image from "next/image";
import { useRouter } from "next/navigation";
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
  fetchMe,
  fetchSessions,
  isAuthError,
  login,
  postAction,
} from "@/lib/api";
import { clearToken, readHost, readLaunchTarget, readToken, writeHost, writeLaunchTarget, writeToken } from "@/lib/store";
import { Backend, LaunchTargetSummary, SessionEnvelope, SessionRecord } from "@/lib/types";

type ConnectionState = "idle" | "connecting" | "open" | "reconnecting";

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;
const ALL_BACKENDS: Backend[] = ["codex", "claude_code"];

export default function HomePage() {
  const router = useRouter();
  const [host, setHost] = useState("");
  const [token, setToken] = useState("");
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [error, setError] = useState("");
  const [connection, setConnection] = useState<ConnectionState>("idle");
  const [defaultBackend, setDefaultBackend] = useState<Backend>("codex");
  const [defaultCwd, setDefaultCwd] = useState("~/");
  const [launchTargets, setLaunchTargets] = useState<LaunchTargetSummary[]>([]);
  const [activeLaunchTargetId, setActiveLaunchTargetId] = useState("");

  useEffect(() => {
    const currentHost = readHost();
    const currentToken = readToken();
    setHost(currentHost);
    setToken(currentToken);
    setActiveLaunchTargetId(readLaunchTarget(currentHost));
  }, []);

  useEffect(() => {
    if (!host || !token) {
      setConnection("idle");
      return;
    }
    let active = true;
    Promise.all([fetchSessions(host, token), fetchMe(host, token)])
      .then(([items, me]) => {
        if (!active) {
          return;
        }
        setSessions(items);
        setDefaultBackend(me.default_backend);
        setDefaultCwd(me.default_cwd || "~/");
        setLaunchTargets(me.launch_targets);
        const storedTargetId = readLaunchTarget(host);
        const nextTargetId = me.launch_targets.some((target) => target.id === storedTargetId) ? storedTargetId : "";
        setActiveLaunchTargetId(nextTargetId);
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
  }, [host, token]);

  async function handleLogin(nextHost: string, password: string) {
    const nextToken = await login(nextHost, password);
    writeHost(nextHost);
    writeToken(nextToken);
    setHost(nextHost);
    setToken(nextToken);
    setError("");
  }

  async function handleCreate(backend: Backend, cwd: string, title: string, remoteCwd?: string) {
    try {
      const session = await createSession(host, token, {
        backend,
        cwd,
        remote_cwd: remoteCwd || null,
        launch_target_id: activeLaunchTargetId || null,
        title: title || null,
        source_mode: "managed",
        args: [],
      });
      setSessions((current) => [session, ...current.filter((item) => item.id !== session.id)]);
      router.push(`/session/${session.id}`);
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
      router.push(`/session/${session.id}`);
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
    setDefaultBackend("codex");
    setDefaultCwd("~/");
    setLaunchTargets([]);
    setActiveLaunchTargetId("");
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

  async function handleTerminate(sessionId: string) {
    try {
      await postAction(host, token, sessionId, "terminate");
    } catch (terminateError) {
      if (isAuthError(terminateError)) {
        resetAuthState("Session expired. Log in again.");
        return;
      }
      setError(terminateError instanceof Error ? terminateError.message : "failed to terminate");
    }
  }

  async function handleDeleteExited() {
    const exitedIds = sessions.filter((session) => session.status === "exited").map((session) => session.id);
    try {
      for (const sessionId of exitedIds) {
        await deleteSessionRequest(host, token, sessionId);
      }
      setSessions((current) => current.filter((session) => session.status !== "exited"));
    } catch (deleteError) {
      if (isAuthError(deleteError)) {
        resetAuthState("Session expired. Log in again.");
        return;
      }
      setError(deleteError instanceof Error ? deleteError.message : "failed to delete exited sessions");
    }
  }

  function handleSwitchBackend(nextHost: string, nextTargetId: string) {
    if (nextHost === host) {
      writeLaunchTarget(host, nextTargetId);
      setActiveLaunchTargetId(nextTargetId);
      setError("");
      return;
    }
    writeHost(nextHost);
    clearToken();
    setHost(nextHost);
    setToken("");
    setSessions([]);
    setLaunchTargets([]);
    setActiveLaunchTargetId(readLaunchTarget(nextHost));
    setError("Switched backend. Log in to continue.");
  }

  const activeLaunchTarget = launchTargets.find((target) => target.id === activeLaunchTargetId) ?? null;
  const supportedBackends = activeLaunchTarget?.supported_backends.length
    ? activeLaunchTarget.supported_backends
    : ALL_BACKENDS;
  const effectiveDefaultBackend = supportedBackends.includes(activeLaunchTarget?.default_backend ?? defaultBackend)
    ? (activeLaunchTarget?.default_backend ?? defaultBackend)
    : supportedBackends[0];
  const effectiveRemoteCwd = activeLaunchTarget?.default_remote_cwd ?? null;

  return (
    <main className="page-shell">
      <section className="hero">
        <div className="brand">
          <Image
            src="/icons/icon-192.png"
            alt="Waypoint"
            width={48}
            height={48}
            priority
          />
          <p className="eyebrow">Waypoint</p>
        </div>
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
          launchTargets={launchTargets}
          targetId={activeLaunchTargetId}
          onSwitch={handleSwitchBackend}
          onAuthFailure={() => resetAuthState("Session expired. Log in again.")}
        />
      ) : null}
      {token ? (
        <LaunchPanel
          defaultBackend={effectiveDefaultBackend}
          defaultCwd={defaultCwd}
          defaultRemoteCwd={effectiveRemoteCwd}
          targetLabel={activeLaunchTarget?.name ?? null}
          supportedBackends={supportedBackends}
          onAttach={handleAttach}
          onCreate={handleCreate}
        />
      ) : null}
      {error ? <p className="error">{error}</p> : null}
      {token && connection !== "open" && connection !== "idle" ? (
        <p className="connection-banner muted">
          {connection === "connecting" ? "Connecting…" : "Reconnecting…"}
        </p>
      ) : null}
      {token ? (
        <SessionList
          sessions={sessions}
          onDelete={handleDelete}
          onDeleteExited={handleDeleteExited}
          onTerminate={handleTerminate}
        />
      ) : null}
    </main>
  );
}
