"use client";

import Image from "next/image";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { BackendSwitcher } from "@/components/BackendSwitcher";
import { LaunchPanel } from "@/components/LaunchPanel";
import { LoginForm } from "@/components/LoginForm";
import { SchedulePanel } from "@/components/SchedulePanel";
import { SessionList } from "@/components/SessionList";
import {
  attachTmux,
  cancelSchedule as cancelScheduleRequest,
  clearScheduleHistory as clearScheduleHistoryRequest,
  connectSessionsSocket,
  createSchedule as createScheduleRequest,
  createSession,
  deleteSession as deleteSessionRequest,
  fetchCodexThreads,
  fetchMe,
  fetchSchedules,
  fetchSessions,
  importCodexThread as importCodexThreadRequest,
  isAuthError,
  login,
  postAction,
  setSessionPinned,
} from "@/lib/api";
import {
  clearToken,
  readHost,
  readLaunchTarget,
  readToken,
  writeHost,
  writeLaunchTarget,
  writeToken,
} from "@/lib/store";
import {
  Backend,
  CodexThreadSummary,
  LaunchTargetSummary,
  ScheduleCreateRequest,
  ScheduledSession,
  SessionEnvelope,
  SessionRecord,
} from "@/lib/types";

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
  const [schedules, setSchedules] = useState<ScheduledSession[]>([]);
  const [codexThreads, setCodexThreads] = useState<CodexThreadSummary[]>([]);
  const [codexThreadsLoading, setCodexThreadsLoading] = useState(false);

  const activeLaunchTarget =
    launchTargets.find((target) => target.id === activeLaunchTargetId) ?? null;
  const supportedBackends = activeLaunchTarget?.supported_backends.length
    ? activeLaunchTarget.supported_backends
    : ALL_BACKENDS;
  const effectiveDefaultBackend = supportedBackends.includes(activeLaunchTarget?.default_backend ?? defaultBackend)
    ? (activeLaunchTarget?.default_backend ?? defaultBackend)
    : supportedBackends[0];
  const effectiveDefaultCwd = activeLaunchTarget?.default_cwd ?? defaultCwd;

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
      setCodexThreads([]);
      setCodexThreadsLoading(false);
      return;
    }
    let active = true;
    Promise.all([
      fetchSessions(host, token),
      fetchMe(host, token),
      fetchSchedules(host, token).catch(() => [] as ScheduledSession[]),
    ])
      .then(([items, me, scheduleItems]) => {
        if (!active) {
          return;
        }
        setSessions(items);
        setDefaultBackend(me.default_backend);
        setDefaultCwd(me.default_cwd || "~/");
        setLaunchTargets(me.launch_targets);
        setSchedules(scheduleItems);
        const storedTargetId = readLaunchTarget(host);
        const nextTargetId = me.launch_targets.some(
          (target) => target.id === storedTargetId,
        )
          ? storedTargetId
          : "";
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
          if (message.type === "schedule_list_update") {
            setSchedules(message.payload.schedules as ScheduledSession[]);
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

  useEffect(() => {
    if (!host || !token) {
      setCodexThreads([]);
      setCodexThreadsLoading(false);
      return;
    }
    if (
      activeLaunchTargetId &&
      !launchTargets.some((target) => target.id === activeLaunchTargetId)
    ) {
      return;
    }
    if (!supportedBackends.includes("codex")) {
      setCodexThreads([]);
      setCodexThreadsLoading(false);
      return;
    }
    let active = true;
    setCodexThreadsLoading(true);
    fetchCodexThreads(host, token, activeLaunchTargetId || undefined)
      .then((threads) => {
        if (!active) {
          return;
        }
        setCodexThreads(threads);
      })
      .catch((fetchError) => {
        if (!active) {
          return;
        }
        if (isAuthError(fetchError)) {
          resetAuthState("Session expired. Log in again.");
          return;
        }
        setError(
          fetchError instanceof Error
            ? fetchError.message
            : "failed to fetch codex threads",
        );
      })
      .finally(() => {
        if (active) {
          setCodexThreadsLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [activeLaunchTargetId, host, launchTargets, supportedBackends, token]);

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
        launch_target_id: activeLaunchTargetId || null,
        title: title || null,
        source_mode: "managed",
        args: [],
      });
      setSessions((current) => [
        session,
        ...current.filter((item) => item.id !== session.id),
      ]);
      router.push(`/session/${session.id}`);
    } catch (createError) {
      if (isAuthError(createError)) {
        resetAuthState("Session expired. Log in again.");
        return;
      }
      setError(
        createError instanceof Error
          ? createError.message
          : "failed to create session",
      );
    }
  }

  async function handleAttach(target: string, backendHint: Backend) {
    try {
      const session = await attachTmux(host, token, {
        tmux_target: target,
        backend_hint: backendHint,
      });
      setSessions((current) => [
        session,
        ...current.filter((item) => item.id !== session.id),
      ]);
      router.push(`/session/${session.id}`);
    } catch (attachError) {
      if (isAuthError(attachError)) {
        resetAuthState("Session expired. Log in again.");
        return;
      }
      setError(
        attachError instanceof Error
          ? attachError.message
          : "failed to attach session",
      );
    }
  }

  async function handleImportCodexThread(threadId: string) {
    try {
      const session = await importCodexThreadRequest(host, token, {
        thread_id: threadId,
        launch_target_id: activeLaunchTargetId || null,
      });
      setSessions((current) => [
        session,
        ...current.filter((item) => item.id !== session.id),
      ]);
      setCodexThreads((current) => current.filter((thread) => thread.id !== threadId));
      router.push(`/session/${session.id}`);
    } catch (importError) {
      if (isAuthError(importError)) {
        resetAuthState("Session expired. Log in again.");
        return;
      }
      setError(
        importError instanceof Error
          ? importError.message
          : "failed to import codex thread",
      );
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
    setSchedules([]);
    setCodexThreads([]);
    setCodexThreadsLoading(false);
    setError(message);
  }

  async function handleCreateSchedule(payload: ScheduleCreateRequest) {
    try {
      const created = await createScheduleRequest(host, token, {
        ...payload,
        launch_target_id: activeLaunchTargetId || null,
      });
      setSchedules((current) => [
        created,
        ...current.filter((item) => item.id !== created.id),
      ]);
    } catch (createError) {
      if (isAuthError(createError)) {
        resetAuthState("Session expired. Log in again.");
        return;
      }
      throw createError;
    }
  }

  async function handleCancelSchedule(scheduleId: string) {
    const previous = schedules.find((schedule) => schedule.id === scheduleId);
    try {
      await cancelScheduleRequest(host, token, scheduleId);
      setSchedules((current) => {
        if (!previous || previous.status === "pending") {
          return current.map((schedule) =>
            schedule.id === scheduleId ? { ...schedule, status: "cancelled" } : schedule,
          );
        }
        return current.filter((schedule) => schedule.id !== scheduleId);
      });
    } catch (cancelError) {
      if (isAuthError(cancelError)) {
        resetAuthState("Session expired. Log in again.");
        return;
      }
      setError(cancelError instanceof Error ? cancelError.message : "failed to cancel");
    }
  }

  async function handleClearScheduleHistory() {
    try {
      await clearScheduleHistoryRequest(host, token);
      setSchedules((current) => current.filter((schedule) => schedule.status === "pending"));
    } catch (clearError) {
      if (isAuthError(clearError)) {
        resetAuthState("Session expired. Log in again.");
        return;
      }
      setError(clearError instanceof Error ? clearError.message : "failed to clear schedules");
    }
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
      setError(
        deleteError instanceof Error
          ? deleteError.message
          : "failed to delete session",
      );
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
      setError(
        terminateError instanceof Error
          ? terminateError.message
          : "failed to terminate",
      );
    }
  }

  async function handleSetPinned(sessionId: string, pinned: boolean) {
    try {
      const updated = await setSessionPinned(host, token, sessionId, pinned);
      setSessions((current) =>
        current.map((session) => (session.id === sessionId ? updated : session)),
      );
    } catch (pinError) {
      if (isAuthError(pinError)) {
        resetAuthState("Session expired. Log in again.");
        return;
      }
      setError(
        pinError instanceof Error ? pinError.message : "failed to update pin",
      );
    }
  }

  async function handleDeleteExited() {
    const exitedIds = sessions
      .filter((session) => session.status === "exited")
      .map((session) => session.id);
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
      setError(
        deleteError instanceof Error
          ? deleteError.message
          : "failed to delete exited sessions",
      );
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
    setCodexThreads([]);
    setCodexThreadsLoading(false);
    setError("Switched backend. Log in to continue.");
  }

  const connectionLabel = token
    ? connection === "open"
      ? "live"
      : connection === "reconnecting"
        ? "reconnecting"
        : connection === "connecting"
          ? "connecting"
          : "idle"
    : "signed out";

  return (
    <main className="page-shell">
      <header className="app-bar">
        <div className="app-bar-brand">
          <div className="app-bar-mark" aria-hidden="true">
            <Image src="/waypoint.svg" alt="" width={38} height={38} priority />
          </div>
          <div className="app-bar-titles">
            <p className="app-bar-eyebrow">Waypoint</p>
            <h1 className="app-bar-title">Coding session control deck</h1>
          </div>
        </div>
        <div className="app-bar-meta">
          <span className={`app-bar-status ${connection}`}>{connectionLabel}</span>
          {host ? <span className="muted">{host}</span> : null}
        </div>
      </header>
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
          defaultCwd={effectiveDefaultCwd}
          targetLabel={activeLaunchTarget?.name ?? null}
          supportedBackends={supportedBackends}
          codexThreads={codexThreads}
          codexThreadsLoading={codexThreadsLoading}
          onAttach={handleAttach}
          onCreate={handleCreate}
          onImportCodexThread={handleImportCodexThread}
        />
      ) : null}
      {token ? (
        <SchedulePanel
          defaultBackend={effectiveDefaultBackend}
          defaultCwd={effectiveDefaultCwd}
          targetLabel={activeLaunchTarget?.name ?? null}
          supportedBackends={supportedBackends}
          schedules={schedules}
          onCreate={handleCreateSchedule}
          onCancel={handleCancelSchedule}
          onClearHistory={handleClearScheduleHistory}
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
          onSetPinned={handleSetPinned}
        />
      ) : null}
    </main>
  );
}
