"use client";

import Image from "next/image";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { BackendSwitcher } from "@/components/BackendSwitcher";
import { LaunchPanel } from "@/components/LaunchPanel";
import { LoginForm } from "@/components/LoginForm";
import { SchedulePanel } from "@/components/SchedulePanel";
import { SessionList } from "@/components/SessionList";
import { ThemeToggle } from "@/components/ThemeToggle";
import { useTheme } from "@/lib/theme";
import {
  attachTmux,
  cancelSchedule as cancelScheduleRequest,
  clearScheduleHistory as clearScheduleHistoryRequest,
  connectSessionsSocket,
  createSchedule as createScheduleRequest,
  createSession,
  deleteSession as deleteSessionRequest,
  fetchBackendThreads,
  fetchMe,
  fetchSchedules,
  fetchSessions,
  importBackendThread,
  isAuthError,
  login,
  postAction,
  setSessionPinned,
  setSessionTitle,
} from "@/lib/api";
import { useBackendCatalog } from "@/lib/backends";
import {
  clearToken,
  mergeRecentCwds,
  pushRecentCwd,
  readRecentCwds,
  readHost,
  readLaunchTarget,
  readToken,
  writeHost,
  writeLaunchTarget,
  writeToken,
} from "@/lib/store";
import {
  Backend,
  BackendDescriptor,
  LaunchTargetSummary,
  ScheduleCreateRequest,
  ScheduledSession,
  SessionEnvelope,
  SessionRecord,
} from "@/lib/types";

interface ThreadSummary {
  id: string;
  title: string;
  cwd: string;
  repo_name?: string | null;
  branch?: string | null;
  preview?: string | null;
  created_at: string;
  updated_at: string;
}

type ConnectionState = "idle" | "connecting" | "open" | "reconnecting";

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

function seedRecentCwdsFromHistory(
  host: string,
  sessions: SessionRecord[],
  schedules: ScheduledSession[],
): void {
  const entries: { targetId: string; cwd: string; ts: number }[] = [];
  for (const session of sessions) {
    entries.push({
      targetId: session.launch_target_id ?? "",
      cwd: session.cwd,
      ts: Date.parse(session.updated_at || session.created_at) || 0,
    });
  }
  for (const schedule of schedules) {
    entries.push({
      targetId: schedule.launch_target_id ?? "",
      cwd: schedule.cwd,
      ts: Date.parse(schedule.created_at) || 0,
    });
  }
  entries.sort((a, b) => b.ts - a.ts);
  mergeRecentCwds(
    host,
    entries.map(({ targetId, cwd }) => ({ targetId, cwd })),
  );
}
// Hand-mirrored fallback used until `/api/me` lands. Once the catalog
// arrives we derive `allBackends` from `me.backends.map(b => b.id)`
// so adding a backend at the registry shows up here without an edit.
const FALLBACK_BACKENDS: Backend[] = ["codex", "claude_code"];

export default function HomePage() {
  const router = useRouter();
  const { theme } = useTheme();
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
  const [recentCwds, setRecentCwds] = useState<string[]>([]);
  const [threadsByBackend, setThreadsByBackend] = useState<
    Record<Backend, ThreadSummary[]>
  >({});
  const [loadingByBackend, setLoadingByBackend] = useState<
    Record<Backend, boolean>
  >({});
  const [backendDescriptors, setBackendDescriptors] = useState<
    BackendDescriptor[] | null
  >(null);
  const registeredBackends: Backend[] =
    backendDescriptors !== null
      ? backendDescriptors.map((entry) => entry.id)
      : FALLBACK_BACKENDS;
  const catalog = useBackendCatalog(
    host || null,
    token || null,
    backendDescriptors !== null
      ? {
          authenticated: true,
          default_backend: defaultBackend,
          default_cwd: defaultCwd,
          launch_targets: launchTargets,
          backends: backendDescriptors,
        }
      : null,
  );

  const activeLaunchTarget =
    launchTargets.find((target) => target.id === activeLaunchTargetId) ?? null;
  const supportedBackends = activeLaunchTarget?.supported_backends.length
    ? activeLaunchTarget.supported_backends
    : registeredBackends;
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
    setRecentCwds(readRecentCwds(host, activeLaunchTargetId));
  }, [activeLaunchTargetId, host]);

  useEffect(() => {
    if (!host || !token) {
      setConnection("idle");
      setThreadsByBackend({});
      setLoadingByBackend({});
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
        if (me.backends && me.backends.length > 0) {
          setBackendDescriptors(me.backends);
        }
        setSchedules(scheduleItems);
        const storedTargetId = readLaunchTarget(host);
        const nextTargetId = me.launch_targets.some(
          (target) => target.id === storedTargetId,
        )
          ? storedTargetId
          : "";
        setActiveLaunchTargetId(nextTargetId);
        seedRecentCwdsFromHistory(host, items, scheduleItems);
        setRecentCwds(readRecentCwds(host, nextTargetId));
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

  // The set of backends we should fetch threads for: those that
  // advertise `supports_thread_discovery=True` AND are listed by the
  // active launch target (or all registered backends when launching
  // locally). Computed via JSON for stable dep keys so the effect does
  // not retrigger on identity-only changes.
  const discoveryBackends = supportedBackends.filter((id) => {
    const caps = catalog.byId(id)?.capabilities;
    // Default to True so a fresh page load (catalog not yet hydrated)
    // tries the call rather than silently skipping the per-backend
    // fetch — the dispatcher returns 400 cheaply for tmux either way.
    return caps?.supports_thread_discovery ?? true;
  });
  const discoveryBackendsKey = JSON.stringify(discoveryBackends);

  useEffect(() => {
    if (!host || !token) {
      setThreadsByBackend({});
      setLoadingByBackend({});
      return;
    }
    if (
      activeLaunchTargetId &&
      !launchTargets.some((target) => target.id === activeLaunchTargetId)
    ) {
      return;
    }
    let active = true;
    const ids = JSON.parse(discoveryBackendsKey) as Backend[];
    setLoadingByBackend((current) => {
      const next: Record<Backend, boolean> = {};
      for (const id of ids) {
        next[id] = current[id] ?? true;
      }
      return next;
    });
    setThreadsByBackend((current) => {
      const next: Record<Backend, ThreadSummary[]> = {};
      for (const id of ids) {
        next[id] = current[id] ?? [];
      }
      return next;
    });
    for (const id of ids) {
      setLoadingByBackend((current) => ({ ...current, [id]: true }));
      fetchBackendThreads<ThreadSummary>(
        host,
        token,
        id,
        {
          launchTargetId: activeLaunchTargetId || undefined,
        },
      )
        .then((threads) => {
          if (!active) {
            return;
          }
          setThreadsByBackend((current) => ({ ...current, [id]: threads }));
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
              : `failed to fetch ${id} threads`,
          );
        })
        .finally(() => {
          if (active) {
            setLoadingByBackend((current) => ({ ...current, [id]: false }));
          }
        });
    }
    return () => {
      active = false;
    };
  }, [
    activeLaunchTargetId,
    host,
    launchTargets,
    discoveryBackendsKey,
    token,
  ]);

  async function handleLogin(nextHost: string, password: string) {
    const nextToken = await login(nextHost, password);
    writeHost(nextHost);
    writeToken(nextToken);
    setHost(nextHost);
    setToken(nextToken);
    setError("");
  }

  async function handleCreate(
    backend: Backend,
    cwd: string,
    title: string,
    model: string | null,
    effort: string | null,
    args: string[] = [],
    configOverrides: string[] = [],
  ) {
    try {
      const session = await createSession(host, token, {
        backend,
        cwd,
        launch_target_id: activeLaunchTargetId || null,
        title: title || null,
        source_mode: "managed",
        args,
        config_overrides: configOverrides,
        model,
        effort,
      });
      setSessions((current) => [
        session,
        ...current.filter((item) => item.id !== session.id),
      ]);
      setRecentCwds(pushRecentCwd(host, activeLaunchTargetId, cwd));
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

  async function handleImportThread(
    backend: Backend,
    threadId: string,
    cwd: string,
  ) {
    try {
      const payload = {
        thread_id: threadId,
        launch_target_id: activeLaunchTargetId || null,
        cwd,
      };
      const session = await importBackendThread(host, token, backend, payload);
      setSessions((current) => [
        session,
        ...current.filter((item) => item.id !== session.id),
      ]);
      setThreadsByBackend((current) => ({
        ...current,
        [backend]: (current[backend] ?? []).filter(
          (thread) => thread.id !== threadId,
        ),
      }));
      router.push(`/session/${session.id}`);
    } catch (importError) {
      if (isAuthError(importError)) {
        resetAuthState("Session expired. Log in again.");
        return;
      }
      setError(
        importError instanceof Error
          ? importError.message
          : `failed to import ${backend} thread`,
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
    setThreadsByBackend({});
    setLoadingByBackend({});
    setError(message);
  }

  function handleAuthFailure() {
    resetAuthState("Session expired. Log in again.");
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
      setRecentCwds(pushRecentCwd(host, activeLaunchTargetId, payload.cwd));
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

  async function handleSetTitle(sessionId: string, title: string) {
    const previous = sessions.find((session) => session.id === sessionId);
    setSessions((current) =>
      current.map((session) =>
        session.id === sessionId ? { ...session, title } : session,
      ),
    );
    try {
      const updated = await setSessionTitle(host, token, sessionId, title);
      setSessions((current) =>
        current.map((session) => (session.id === sessionId ? updated : session)),
      );
    } catch (titleError) {
      if (previous) {
        setSessions((current) =>
          current.map((session) =>
            // Only revert if our optimistic title is still the current one;
            // a newer rename or a WS update may have superseded it.
            session.id === sessionId && session.title === title ? previous : session,
          ),
        );
      }
      if (isAuthError(titleError)) {
        resetAuthState("Session expired. Log in again.");
        return;
      }
      setError(
        titleError instanceof Error ? titleError.message : "failed to update title",
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
    setThreadsByBackend({});
    setLoadingByBackend({});
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
            <Image
              src={theme === "light" ? "/waypoint-light.svg" : "/waypoint.svg"}
              alt=""
              width={38}
              height={38}
              priority
            />
          </div>
          <div className="app-bar-titles">
            <p className="app-bar-eyebrow">Waypoint</p>
            <h1 className="app-bar-title">Coding session control deck</h1>
          </div>
        </div>
        <div className="app-bar-meta">
          <span className={`app-bar-status ${connection}`}>{connectionLabel}</span>
          {host ? <span className="muted">{host}</span> : null}
          <ThemeToggle />
        </div>
      </header>
      {error ? (
        <div className="error-banner" role="alert">
          <span>{error}</span>
          <button className="error-banner-dismiss" onClick={() => setError("")} aria-label="Dismiss">×</button>
        </div>
      ) : null}
      {!token ? <LoginForm defaultHost={host} onSubmit={handleLogin} /> : null}
      {token ? (
        <BackendSwitcher
          host={host}
          token={token}
          launchTargets={launchTargets}
          targetId={activeLaunchTargetId}
          onSwitch={handleSwitchBackend}
          onAuthFailure={handleAuthFailure}
        />
      ) : null}
      {token ? (
        <LaunchPanel
          host={host}
          token={token}
          defaultBackend={effectiveDefaultBackend}
          defaultCwd={effectiveDefaultCwd}
          targetLabel={activeLaunchTarget?.name ?? null}
          launchTargetId={activeLaunchTargetId || null}
          recentCwds={recentCwds}
          supportedBackends={supportedBackends}
          catalog={catalog}
          threadsByBackend={threadsByBackend}
          loadingByBackend={loadingByBackend}
          onAttach={handleAttach}
          onCreate={handleCreate}
          onImportThread={handleImportThread}
          onAuthFailure={handleAuthFailure}
        />
      ) : null}
      {token ? (
        <SchedulePanel
          host={host}
          token={token}
          defaultBackend={effectiveDefaultBackend}
          defaultCwd={effectiveDefaultCwd}
          targetLabel={activeLaunchTarget?.name ?? null}
          launchTargetId={activeLaunchTargetId || null}
          recentCwds={recentCwds}
          supportedBackends={supportedBackends}
          catalog={catalog}
          schedules={schedules}
          onCreate={handleCreateSchedule}
          onCancel={handleCancelSchedule}
          onClearHistory={handleClearScheduleHistory}
          onAuthFailure={handleAuthFailure}
        />
      ) : null}
      {token ? (
        <SessionList
          sessions={sessions}
          onDelete={handleDelete}
          onDeleteExited={handleDeleteExited}
          onTerminate={handleTerminate}
          onSetPinned={handleSetPinned}
          onSetTitle={handleSetTitle}
        />
      ) : null}
    </main>
  );
}
