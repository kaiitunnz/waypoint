"use client";

import Image from "next/image";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { BackendSwitcher } from "@/components/BackendSwitcher";
import { CompactLaunch, type LaunchSheetMode } from "@/components/CompactLaunch";
import { HomeNav } from "@/components/HomeNav";
import { InstrumentRail } from "@/components/InstrumentRail";
import { LaunchPanel } from "@/components/LaunchPanel";
import { LoginForm } from "@/components/LoginForm";
import { PresenceFloaters } from "@/components/PresenceFloaters";
import { SchedulePanel } from "@/components/SchedulePanel";
import { SessionList } from "@/components/SessionList";
import { Sheet } from "@/components/Sheet";
import { SessionSettingsModal } from "@/components/SessionSettingsModal";
import { SshConnectModal } from "@/components/SshConnectModal";
import { ThemeToggle } from "@/components/ThemeToggle";
import { useInboxCount } from "@waypoint/web-core/hooks/useInboxCount";
import { markTargetConnected as setTargetConnected } from "@waypoint/web-core/launchTargets";
import { useTheme } from "@/lib/theme";
import {
  attachTmux,
  cancelSchedule as cancelScheduleRequest,
  clearMessageScheduleHistory as clearMessageScheduleHistoryRequest,
  clearScheduleHistory as clearScheduleHistoryRequest,
  connectLaunchTarget,
  connectSessionsSocket,
  createSchedule as createScheduleRequest,
  createSession,
  createSessionPreset,
  deleteMessageSchedule as deleteMessageScheduleRequest,
  deleteExitedSessions,
  deleteSession as deleteSessionRequest,
  deleteSessionPreset,
  deleteThread,
  disconnectLaunchTarget,
  fetchBoardChannels,
  fetchLaunchTargetStatus,
  fetchMe,
  fetchMessageSchedules,
  fetchSchedules,
  fetchLiveSessions,
  fetchSessionHistory,
  fetchSessionPreset,
  fetchSessionsByIds,
  fetchUsageDashboard,
  importBackendThread,
  isAuthError,
  login,
  parseLiveSessionList,
  postAction,
  refreshUsageDashboard,
  setDefaultSessionPreset,
  setSessionPinned,
  setSessionTitle,
  updateSessionPreset,
} from "@waypoint/web-core/api";
import {
  clearToken,
  pushRecentCwd,
  readRecentCwds,
  readHost,
  readLaunchTarget,
  readToken,
  writeHost,
  writeLaunchTarget,
  writeToken,
} from "@waypoint/web-core/store";
import { useBackendCatalog } from "@waypoint/web-core/backends";
import { useResumableThreads } from "@waypoint/web-core/hooks/useResumableThreads";
import { seedRecentCwdsFromHistory } from "@waypoint/web-core/launch/folders";
import { launchDefaults } from "@waypoint/web-core/launch/defaults";
import { classifyLaunchFailure } from "@waypoint/web-core/launch/failure";
import type { PresetSelection } from "@waypoint/web-core/launch/presets";
import {
  type AgentThread,
  threadDiscoveryBackends,
  threadProfileScope,
} from "@waypoint/web-core/launch/threads";
import { reconnectDelay } from "@waypoint/web-core/session/connection";
import {
  applySessionState,
  clearSessionHistory,
  forgetHistorySession,
  invalidateSessionHistory,
  upsertLiveSession,
} from "@waypoint/web-core/sessionHistory";
import {
  AssistantSummary,
  Backend,
  BackendDescriptor,
  BoardChannel,
  LaunchTargetSummary,
  MessageSchedule,
  ScheduleCreateRequest,
  ScheduledSession,
  SessionCreateRequest,
  SessionEnvelope,
  SessionPresetSummary,
  ProviderUsageStatus,
  SessionPresetWriteRequest,
  SessionRecord,
  UsageDashboardBucket,
  UsageProviderOption,
} from "@waypoint/web-core/types";

type ConnectionState = "idle" | "connecting" | "open" | "reconnecting";

const BOARD_REFRESH_DEBOUNCE_MS = 300;
// Exited sessions read when seeding the recent-directory suggestions.
const RECENT_CWD_HISTORY_LIMIT = 50;
// Sources the home list never shows (each has its own surface or is ephemeral).
const HOME_HIDDEN_SOURCES = ["assistant", "telemetry"] as const;

// Hand-mirrored fallback used until `/api/me` lands. Once the catalog
// arrives we derive `allBackends` from `me.backends.map(b => b.id)`
// so adding a backend at the registry shows up here without an edit.
const FALLBACK_BACKENDS: Backend[] = ["codex", "claude_code"];

export default function HomePage() {
  const router = useRouter();
  const { theme } = useTheme();
  const [host, setHost] = useState("");
  const [token, setToken] = useState("");
  // Live and pinned sessions; exited history is paged by SessionList.
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [historyRevision, setHistoryRevision] = useState("");
  // Sessions referenced by message schedules but absent from the live list,
  // fetched by id so schedule rows keep their titles.
  const [referencedSessions, setReferencedSessions] = useState<Record<string, SessionRecord>>({});
  const requestedSessionIdsRef = useRef(new Set<string>());
  const [settingsSession, setSettingsSession] = useState<SessionRecord | null>(
    null,
  );
  const [assistant, setAssistant] = useState<AssistantSummary | null>(null);
  const [error, setError] = useState("");
  // The working directory the last New launch was rejected for (400
  // cwd-not-found), surfaced inline on the launch form's cwd field.
  const [cwdError, setCwdError] = useState<string | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("idle");
  const [defaultBackend, setDefaultBackend] = useState<Backend>("codex");
  const [defaultCwd, setDefaultCwd] = useState("~/");
  const [launchTargets, setLaunchTargets] = useState<LaunchTargetSummary[]>([]);
  const [sessionPresets, setSessionPresets] = useState<SessionPresetSummary[]>([]);
  const [defaultPresetId, setDefaultPresetId] = useState<string | null>(null);
  const [telemetryEnabled, setTelemetryEnabled] = useState(false);
  const [usageProviderOptions, setUsageProviderOptions] = useState<
    UsageProviderOption[]
  >([]);
  const [activeLaunchTargetId, setActiveLaunchTargetId] = useState("");
  // Password-auth SSH connect prompt. ``retry`` re-runs the launch that hit a
  // 409 once the ControlMaster is up; ``null`` when the prompt was opened
  // proactively from the connection banner.
  const [connectPrompt, setConnectPrompt] = useState<{
    target: LaunchTargetSummary;
    retry: (() => Promise<void>) | null;
  } | null>(null);
  const [connectError, setConnectError] = useState("");
  const [schedules, setSchedules] = useState<ScheduledSession[]>([]);
  const [messageSchedules, setMessageSchedules] = useState<MessageSchedule[]>([]);
  const [boardChannels, setBoardChannels] = useState<BoardChannel[]>([]);
  const [usageBuckets, setUsageBuckets] = useState<UsageDashboardBucket[] | null>(
    null,
  );
  const [usageProviders, setUsageProviders] = useState<ProviderUsageStatus[]>([]);
  const [refreshingUsage, setRefreshingUsage] = useState(false);
  const [launchSheetOpen, setLaunchSheetOpen] = useState(false);
  const [launchSheetMode, setLaunchSheetMode] = useState<LaunchSheetMode>("new");
  const [scheduleSheetOpen, setScheduleSheetOpen] = useState(false);
  // The sheet remounts LaunchPanel on every open, so this is read once at mount;
  // every open path sets it explicitly so a stale value never bleeds through.
  const [presetSelection, setPresetSelection] = useState<PresetSelection>({
    kind: "default",
  });
  const inboxCount = useInboxCount(host, token);
  const [recentCwds, setRecentCwds] = useState<string[]>([]);
  // The launch form's live (backend, accountProfileId) selection, reported by
  // LaunchPanel — threads are fetched here app-globally, decoupled from the
  // form, so this is how the active backend's fetch re-scopes to the
  // selected profile.
  const [activeFormBackend, setActiveFormBackend] = useState<Backend | null>(null);
  const [activeFormAccountProfileId, setActiveFormAccountProfileId] = useState<
    string | null
  >(null);
  const handleDiscoveryScopeChange = useCallback(
    (backend: Backend, accountProfileId: string | null) => {
      setActiveFormBackend(backend);
      setActiveFormAccountProfileId(accountProfileId);
    },
    [],
  );
  const [backendDescriptors, setBackendDescriptors] = useState<
    BackendDescriptor[] | null
  >(null);
  const registeredBackends = useMemo<Backend[]>(
    () =>
      backendDescriptors !== null
        ? backendDescriptors.map((entry) => entry.id)
        : FALLBACK_BACKENDS,
    [backendDescriptors],
  );
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
  const activeTargetAuth = activeLaunchTarget?.auth ?? null;
  const {
    launchableBackends,
    defaultBackend: effectiveDefaultBackend,
    defaultCwd: effectiveDefaultCwd,
    defaultLaunchEnvByBackend,
    accountProfilesByBackend,
  } = useMemo(
    () =>
      launchDefaults({
        registeredBackends,
        descriptors: backendDescriptors ?? [],
        hostDefaultBackend: defaultBackend,
        hostDefaultCwd: defaultCwd,
        activeTarget: activeLaunchTarget,
        catalog,
      }),
    [
      registeredBackends,
      backendDescriptors,
      defaultBackend,
      defaultCwd,
      activeLaunchTarget,
      catalog,
    ],
  );

  const resetAuthState = useCallback((message: string) => {
    clearToken();
    clearSessionHistory(readHost());
    setToken("");
    setSessions([]);
    setHistoryRevision("");
    setDefaultBackend("codex");
    setDefaultCwd("~/");
    setLaunchTargets([]);
    setActiveLaunchTargetId("");
    setSchedules([]);
    setError(message);
  }, []);

  const handleAuthFailure = useCallback(() => {
    resetAuthState("Session expired. Log in again.");
  }, [resetAuthState]);

  const handleRefreshUsage = useCallback(async () => {
    if (!host || !token) return;
    setRefreshingUsage(true);
    try {
      const response = await refreshUsageDashboard(host, token);
      setUsageBuckets(response.buckets);
      setUsageProviders(response.providers ?? []);
    } catch (refreshError) {
      if (isAuthError(refreshError)) {
        handleAuthFailure();
        return;
      }
      setError(
        refreshError instanceof Error
          ? refreshError.message
          : "failed to refresh usage",
      );
    } finally {
      setRefreshingUsage(false);
    }
  }, [host, token, handleAuthFailure]);

  useEffect(() => {
    const currentHost = readHost();
    const currentToken = readToken();
    setHost(currentHost);
    setToken(currentToken);
    setActiveLaunchTargetId(readLaunchTarget(currentHost));
  }, []);

  useEffect(() => {
    setRecentCwds(readRecentCwds(host, activeLaunchTargetId));
    // A cwd rejection is target-specific ("not found on <target>"), so drop it
    // when the target changes to avoid showing a now-irrelevant error.
    setCwdError(null);
  }, [activeLaunchTargetId, host]);

  // The `connected` flag from /api/me is a cached snapshot; the master may have
  // dropped since. When a password-auth target becomes active, re-validate it
  // live so the strip reflects reality instead of a stale "connected". Keyed on
  // the auth kind (a stable primitive) — depending on `launchTargets` would loop
  // since `markTargetConnected` rewrites that array.
  useEffect(() => {
    if (!host || !token || !activeLaunchTargetId || activeTargetAuth !== "password") {
      return;
    }
    let cancelled = false;
    fetchLaunchTargetStatus(host, token, activeLaunchTargetId)
      .then((status) => {
        if (!cancelled) {
          markTargetConnected(activeLaunchTargetId, status.connected);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [activeLaunchTargetId, host, token, activeTargetAuth]);

  useEffect(() => {
    if (!host || !token) {
      setConnection("idle");
      return;
    }
    let active = true;
    Promise.all([
      fetchLiveSessions(host, token),
      fetchMe(host, token),
      fetchSchedules(host, token).catch(() => [] as ScheduledSession[]),
      fetchMessageSchedules(host, token).catch(() => [] as MessageSchedule[]),
      fetchBoardChannels(host, token).catch(() => []),
    ])
      .then(([live, me, scheduleItems, messageItems, boardChannels]) => {
        if (!active) {
          return;
        }
        setSessions(live.sessions);
        setHistoryRevision(live.historyRevision);
        setBoardChannels(boardChannels);
        setDefaultBackend(me.default_backend);
        setDefaultCwd(me.default_cwd || "~/");
        setLaunchTargets(me.launch_targets);
        setAssistant(me.assistant ?? null);
        if (me.backends && me.backends.length > 0) {
          setBackendDescriptors(me.backends);
        }
        setSessionPresets(me.session_presets ?? []);
        setDefaultPresetId(me.default_preset_id ?? null);
        setTelemetryEnabled(me.telemetry_enabled ?? false);
        setUsageProviderOptions(me.usage_provider_options ?? []);
        setSchedules(scheduleItems);
        setMessageSchedules(messageItems);
        const storedTargetId = readLaunchTarget(host);
        const nextTargetId = me.launch_targets.some(
          (target) => target.id === storedTargetId,
        )
          ? storedTargetId
          : "";
        setActiveLaunchTargetId(nextTargetId);
        setRecentCwds(readRecentCwds(host, nextTargetId));
        fetchSessionHistory(host, token, { limit: RECENT_CWD_HISTORY_LIMIT })
          .catch(() => ({ sessions: [] as SessionRecord[] }))
          .then((history) => {
            if (!active) return;
            seedRecentCwdsFromHistory(
              host,
              [...live.sessions, ...history.sessions],
              scheduleItems,
            );
            setRecentCwds(readRecentCwds(host, nextTargetId));
          });
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
    // Coalesce bursts of board_update notifications into a single trailing
    // refetch; the board channel list is small but each update otherwise
    // triggers a full round-trip.
    let boardRefreshTimer: ReturnType<typeof setTimeout> | null = null;
    const scheduleBoardRefresh = () => {
      if (boardRefreshTimer !== null) {
        clearTimeout(boardRefreshTimer);
      }
      boardRefreshTimer = setTimeout(() => {
        boardRefreshTimer = null;
        fetchBoardChannels(host, token)
          .then((list) => {
            if (active) {
              setBoardChannels(list);
            }
          })
          .catch(() => {});
      }, BOARD_REFRESH_DEBOUNCE_MS);
    };

    function connect() {
      setConnection(attempt === 0 ? "connecting" : "reconnecting");
      socket = connectSessionsSocket(
        host,
        token,
        (message: SessionEnvelope) => {
          if (message.type === "session_list_update") {
            const live = parseLiveSessionList(message.payload);
            setSessions(live.sessions);
            setHistoryRevision(live.historyRevision);
          }
          if (message.type === "session_state") {
            applySessionState(host, message.payload.session as SessionRecord);
          }
          if (message.type === "schedule_list_update") {
            setSchedules(message.payload.schedules as ScheduledSession[]);
            if ("message_schedules" in message.payload) {
              setMessageSchedules(
                message.payload.message_schedules as MessageSchedule[],
              );
            }
          }
          if (message.type === "board_update") {
            scheduleBoardRefresh();
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
            const delay = reconnectDelay(attempt);
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
      if (boardRefreshTimer !== null) {
        clearTimeout(boardRefreshTimer);
      }
      socket?.close();
    };
  }, [host, resetAuthState, token]);

  // Telemetry tile source: one light per-account rate-limit snapshot from the
  // existing /api/usage endpoint (never the heavy telemetry time-series, never
  // the full dashboard component). Degrades to null so the tile falls back to a
  // plain link when usage is momentarily unavailable.
  useEffect(() => {
    if (!host || !token) {
      setUsageBuckets(null);
      setUsageProviders([]);
      return;
    }
    let active = true;
    fetchUsageDashboard(host, token)
      .then((response) => {
        if (!active) return;
        setUsageBuckets(response.buckets);
        setUsageProviders(response.providers ?? []);
      })
      .catch(() => {
        if (active) setUsageBuckets(null);
      });
    return () => {
      active = false;
    };
  }, [host, token]);

  const discoveryBackends = useMemo(
    () => threadDiscoveryBackends(launchableBackends, catalog),
    [launchableBackends, catalog],
  );
  const {
    threadsByBackend,
    loadingByBackend,
    removeThread,
  } = useResumableThreads({
    host,
    token,
    backends: discoveryBackends,
    launchTargetId: activeLaunchTargetId,
    scopeBackend: activeFormBackend,
    scopeAccountProfileId: activeFormAccountProfileId,
    // A stored target the backend no longer lists waits for the list to settle.
    enabled:
      !activeLaunchTargetId ||
      launchTargets.some((target) => target.id === activeLaunchTargetId),
    // Refetch when a target connects, so a password target lists its threads.
    revision: launchTargets,
    onAuthFailure: handleAuthFailure,
    onError: setError,
  });

  async function handleLogin(nextHost: string, password: string) {
    const nextToken = await login(nextHost, password);
    writeHost(nextHost);
    writeToken(nextToken);
    setHost(nextHost);
    setToken(nextToken);
    setError("");
  }

  function markTargetConnected(targetId: string, connected: boolean) {
    setLaunchTargets((current) => setTargetConnected(current, targetId, connected));
  }

  async function handleConnectTarget(password: string) {
    const prompt = connectPrompt;
    if (!prompt) {
      return;
    }
    setConnectError("");
    try {
      const result = await connectLaunchTarget(
        host,
        token,
        prompt.target.id,
        password,
      );
      markTargetConnected(prompt.target.id, result.connected);
      if (!result.connected) {
        setConnectError(result.detail ?? "authentication failed");
        return;
      }
      setConnectPrompt(null);
      if (prompt.retry) {
        await prompt.retry();
      }
    } catch (connectErr) {
      if (isAuthError(connectErr)) {
        resetAuthState("Session expired. Log in again.");
        return;
      }
      setConnectError(
        connectErr instanceof Error ? connectErr.message : "failed to connect",
      );
    }
  }

  async function handleDisconnectTarget(targetId: string) {
    try {
      await disconnectLaunchTarget(host, token, targetId);
      markTargetConnected(targetId, false);
    } catch (disconnectErr) {
      if (isAuthError(disconnectErr)) {
        resetAuthState("Session expired. Log in again.");
        return;
      }
      setError(
        disconnectErr instanceof Error
          ? disconnectErr.message
          : "failed to disconnect",
      );
    }
  }

  async function handleCreate(request: SessionCreateRequest) {
    setCwdError(null);
    const cwd = request.cwd;
    try {
      const session = await createSession(host, token, request);
      setSessions((current) => [
        session,
        ...current.filter((item) => item.id !== session.id),
      ]);
      setRecentCwds(pushRecentCwd(host, activeLaunchTargetId, cwd));
      router.push(`/session/${session.id}`);
    } catch (createError) {
      const failure = classifyLaunchFailure(createError, "failed to create session");
      if (failure.kind === "auth") {
        resetAuthState("Session expired. Log in again.");
        return;
      }
      if (failure.kind === "cwd") {
        // A field-level validation error: surface it inline on the working
        // directory input rather than the global banner.
        setCwdError(cwd);
        return;
      }
      if (failure.kind === "ssh" && activeLaunchTarget) {
        // The target needs its password-auth ControlMaster opened first;
        // prompt, then retry this exact launch on success.
        setConnectError("");
        setConnectPrompt({
          target: activeLaunchTarget,
          retry: () => handleCreate(request),
        });
        return;
      }
      setError(failure.message);
    }
  }

  function openLaunchSheet(mode: LaunchSheetMode) {
    setLaunchSheetMode(mode);
    setPresetSelection({ kind: "default" });
    setLaunchSheetOpen(true);
  }

  // A null preset opens the sheet with no preset selected (backend default).
  function openLaunchSheetWithPreset(preset: SessionPresetSummary | null) {
    setLaunchSheetMode("new");
    setPresetSelection({ kind: "preset", presetId: preset?.id ?? null });
    setLaunchSheetOpen(true);
  }

  async function refreshPresets() {
    try {
      const me = await fetchMe(host, token);
      setSessionPresets(me.session_presets ?? []);
      setDefaultPresetId(me.default_preset_id ?? null);
    } catch (refreshError) {
      if (isAuthError(refreshError)) {
        resetAuthState("Session expired. Log in again.");
      }
    }
  }

  async function handleSavePreset(
    payload: SessionPresetWriteRequest,
    presetId: string | null,
  ): Promise<string | null> {
    let createdId: string | null = null;
    if (presetId) {
      await updateSessionPreset(host, token, presetId, payload);
    } else {
      const created = await createSessionPreset(host, token, payload);
      createdId = created.id;
    }
    await refreshPresets();
    return createdId;
  }

  async function handleSetDefaultPreset(presetId: string) {
    await setDefaultSessionPreset(host, token, presetId);
    await refreshPresets();
  }

  async function handleDeletePreset(presetId: string) {
    await deleteSessionPreset(host, token, presetId);
    await refreshPresets();
  }

  async function handleAttach(request: Record<string, unknown>) {
    try {
      const session = await attachTmux(host, token, request);
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

  async function handleImportThread(thread: AgentThread, body: Record<string, unknown>) {
    try {
      const session = await importBackendThread(host, token, thread.backend, body);
      setSessions((current) => [
        session,
        ...current.filter((item) => item.id !== session.id),
      ]);
      removeThread(thread.backend, thread.id);
      router.push(`/session/${session.id}`);
    } catch (importError) {
      if (isAuthError(importError)) {
        resetAuthState("Session expired. Log in again.");
        return;
      }
      setError(
        importError instanceof Error
          ? importError.message
          : `failed to import ${thread.backend} thread`,
      );
    }
  }

  async function handleDeleteThread(
    backend: Backend,
    threadId: string,
    launchTargetId?: string,
  ) {
    try {
      await deleteThread(
        host,
        token,
        backend,
        threadId,
        launchTargetId ?? activeLaunchTargetId ?? null,
        // Delete from the same profile store the list was scoped to; without it
        // the runtime resolves the default store and no thread matches (404).
        threadProfileScope(backend, activeFormBackend, activeFormAccountProfileId),
      );
      removeThread(backend, threadId);
    } catch (deleteError) {
      if (isAuthError(deleteError)) {
        resetAuthState("Session expired. Log in again.");
        return;
      }
      setError(
        deleteError instanceof Error
          ? deleteError.message
          : `failed to delete ${backend} thread`,
      );
    }
  }

  async function handleCreateSchedule(payload: ScheduleCreateRequest) {
    try {
      const created = await createScheduleRequest(host, token, payload);
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

  async function handleDeleteMessageSchedule(scheduleId: string) {
    try {
      await deleteMessageScheduleRequest(host, token, scheduleId);
      setMessageSchedules((current) =>
        current.filter((ms) => ms.id !== scheduleId),
      );
    } catch (deleteError) {
      if (isAuthError(deleteError)) {
        resetAuthState("Session expired. Log in again.");
        return;
      }
      setError(deleteError instanceof Error ? deleteError.message : "failed to delete message schedule");
    }
  }

  async function handleClearMessageScheduleHistory() {
    try {
      await clearMessageScheduleHistoryRequest(host, token);
      setMessageSchedules((current) =>
        current.filter((ms) => ms.status === "pending"),
      );
    } catch (clearError) {
      if (isAuthError(clearError)) {
        resetAuthState("Session expired. Log in again.");
        return;
      }
      setError(clearError instanceof Error ? clearError.message : "failed to clear message schedule history");
    }
  }

  async function handleDelete(sessionId: string) {
    try {
      await deleteSessionRequest(host, token, sessionId);
      setSessions((current) => current.filter((session) => session.id !== sessionId));
      forgetHistorySession(host, sessionId);
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
      setSessions((current) => upsertLiveSession(current, updated));
      applySessionState(host, updated);
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
      applySessionState(host, updated);
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
    try {
      await deleteExitedSessions(host, token);
      // The server deletes in the background; each deletion's list broadcast
      // moves the counts, and this drops the rows already on screen.
      setSessions((current) => current.filter((session) => session.status !== "exited"));
      invalidateSessionHistory(host);
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
    setError("Switched backend. Log in to continue.");
  }

  useEffect(() => {
    requestedSessionIdsRef.current = new Set();
    setReferencedSessions({});
  }, [host]);

  useEffect(() => {
    if (!host || !token) return;
    const known = new Set(sessions.map((session) => session.id));
    const missing = [...new Set(messageSchedules.map((message) => message.session_id))].filter(
      (sessionId) => !known.has(sessionId) && !requestedSessionIdsRef.current.has(sessionId),
    );
    if (!missing.length) return;
    // Remembered even on failure or when not found (deleted sessions), so a
    // frequently changing live list never re-requests the same ids.
    for (const sessionId of missing) requestedSessionIdsRef.current.add(sessionId);
    fetchSessionsByIds(host, token, missing)
      .then((found) =>
        setReferencedSessions((current) => ({
          ...current,
          ...Object.fromEntries(found.map((session) => [session.id, session])),
        })),
      )
      .catch(() => {});
  }, [host, token, sessions, messageSchedules]);

  const titleSessions = [
    ...sessions,
    ...Object.values(referencedSessions).filter(
      (session) => !sessions.some((live) => live.id === session.id),
    ),
  ];
  const filteredSessions = sessions.filter(
    (session) =>
      session.source !== "assistant" && session.source !== "telemetry",
  );
  const activeSessionCount = filteredSessions.filter(
    (session) => session.status !== "exited",
  ).length;
  const pendingScheduledCount =
    schedules.filter((schedule) => schedule.status === "pending").length +
    messageSchedules.filter((message) => message.status === "pending").length;
  const launchTargetLabel = activeLaunchTarget?.name ?? "Local";

  return (
    <main className="page-shell page-home">
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
            <h1 className="app-bar-title">Sessions</h1>
          </div>
        </div>
        <div className="app-bar-meta">
          {token ? (
            <BackendSwitcher
              host={host}
              token={token}
              launchTargets={launchTargets}
              targetId={activeLaunchTargetId}
              connection={connection}
              onSwitch={handleSwitchBackend}
              onConnectTarget={(target) => {
                setConnectError("");
                setConnectPrompt({ target, retry: null });
              }}
              onDisconnectTarget={handleDisconnectTarget}
              onAuthFailure={handleAuthFailure}
            />
          ) : host ? (
            <span className="muted">{host}</span>
          ) : null}
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
        <>
          <HomeNav
            activeSessions={activeSessionCount}
            boardChannels={boardChannels.length}
            telemetryAccounts={usageBuckets?.length ?? null}
            inboxCount={inboxCount}
            scheduledCount={pendingScheduledCount}
            onOpenScheduled={() => setScheduleSheetOpen(true)}
          />
          <div className="home-layout">
            <CompactLaunch
              targetLabel={launchTargetLabel}
              presets={sessionPresets}
              defaultPresetId={defaultPresetId}
              defaultBackend={effectiveDefaultBackend}
              onOpenPreset={openLaunchSheetWithPreset}
              onOpenSheet={openLaunchSheet}
            />
            <div className="home-main">
              <SessionList
                host={host}
                token={token}
                sessions={filteredSessions}
                historyRevision={historyRevision}
                hiddenSources={HOME_HIDDEN_SOURCES}
                catalog={catalog}
                onAuthFailure={handleAuthFailure}
                onDelete={handleDelete}
                onDeleteExited={handleDeleteExited}
                onTerminate={handleTerminate}
                onSetPinned={handleSetPinned}
                onSetTitle={handleSetTitle}
                onOpenSettings={setSettingsSession}
              />
            </div>
            <InstrumentRail
              usageBuckets={usageBuckets}
              usageProviders={usageProviders}
              backendCatalog={catalog}
              refreshingUsage={refreshingUsage}
              onRefreshUsage={handleRefreshUsage}
              telemetryEnabled={telemetryEnabled}
              boardChannels={boardChannels}
              schedules={schedules}
              messageSchedules={messageSchedules}
              sessions={titleSessions}
              onOpenScheduled={() => setScheduleSheetOpen(true)}
            />
          </div>
        </>
      ) : null}
      {settingsSession ? (
        <SessionSettingsModal
          host={host}
          token={token}
          session={settingsSession}
          onClose={() => setSettingsSession(null)}
          onApplied={(updated) => {
            setSessions((prev) =>
              prev.map((s) => (s.id === updated.id ? updated : s)),
            );
            applySessionState(host, updated);
          }}
          onAuthFailure={handleAuthFailure}
        />
      ) : null}
      {token ? (
        <PresenceFloaters assistant={assistant} inboxCount={inboxCount} />
      ) : null}
      {token ? (
        <Sheet
          open={launchSheetOpen}
          onClose={() => setLaunchSheetOpen(false)}
          eyebrow={`New session · ${launchTargetLabel}`}
          title="Start a session"
        >
          <LaunchPanel
            host={host}
            token={token}
            defaultBackend={effectiveDefaultBackend}
            defaultCwd={effectiveDefaultCwd}
            defaultLaunchEnvByBackend={defaultLaunchEnvByBackend}
            accountProfilesByBackend={accountProfilesByBackend}
            usageProviderOptions={usageProviderOptions}
            targetLabel={activeLaunchTarget?.name ?? null}
            launchTargetId={activeLaunchTargetId || null}
            recentCwds={recentCwds}
            supportedBackends={launchableBackends}
            catalog={catalog}
            threadsByBackend={threadsByBackend}
            loadingByBackend={loadingByBackend}
            onDiscoveryScopeChange={handleDiscoveryScopeChange}
            onDeleteThread={handleDeleteThread}
            onAttach={handleAttach}
            onCreate={handleCreate}
            onImportThread={handleImportThread}
            onCreateSchedule={handleCreateSchedule}
            onAuthFailure={handleAuthFailure}
            cwdError={cwdError}
            onClearCwdError={() => setCwdError(null)}
            presets={sessionPresets}
            defaultPresetId={defaultPresetId}
            onFetchPresetSpec={(presetId) =>
              fetchSessionPreset(host, token, presetId, true)
            }
            onSavePreset={handleSavePreset}
            onSetDefaultPreset={handleSetDefaultPreset}
            onDeletePreset={handleDeletePreset}
            mode={launchSheetMode}
            onModeChange={setLaunchSheetMode}
            initialPreset={presetSelection}
          />
        </Sheet>
      ) : null}
      {token ? (
        <Sheet
          open={scheduleSheetOpen}
          onClose={() => setScheduleSheetOpen(false)}
          eyebrow="Manage · scheduled"
          title="Scheduled"
        >
          {schedules.length || messageSchedules.length ? (
            <SchedulePanel
              host={host}
              token={token}
              schedules={schedules}
              messageSchedules={messageSchedules}
              sessions={titleSessions}
              catalog={catalog}
              onCancelSchedule={handleCancelSchedule}
              onClearScheduleHistory={handleClearScheduleHistory}
              onDeleteMessage={handleDeleteMessageSchedule}
              onClearMessageHistory={handleClearMessageScheduleHistory}
            />
          ) : (
            <p className="wp-sheet-empty muted">
              Nothing scheduled. Queue a session launch or a message from the
              launch sheet&rsquo;s Schedule tab.
            </p>
          )}
        </Sheet>
      ) : null}
      {connectPrompt ? (
        <SshConnectModal
          targetName={connectPrompt.target.name}
          error={connectError || null}
          onSubmit={handleConnectTarget}
          onCancel={() => {
            setConnectPrompt(null);
            setConnectError("");
          }}
        />
      ) : null}
    </main>
  );
}
