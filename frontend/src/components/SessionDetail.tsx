"use client";

import { useRouter } from "next/navigation";
import {
  KeyboardEvent,
  memo,
  startTransition,
  useCallback,
  useDeferredValue,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  answerAskQuestion,
  approvePlan,
  approveSession,
  connectSessionSocket,
  connectTerminalSocket,
  createSession,
  deleteSession as deleteSessionRequest,
  fetchBackendModels,
  fetchEvents,
  fetchSession,
  fetchSessionCompletionsResponse,
  fetchTerminalSnapshot,
  forkSession,
  isAuthError,
  postAction,
  refreshSessionRateLimitUsage,
  sendInput,
  setSessionEffort,
  setSessionModel,
  setSessionPermissionMode,
  setSessionTitle,
} from "@/lib/api";
import {
  fidelityFor,
  humaniseBackend,
  permissionModesFor,
  supportsApprovalNote,
  supportsPlanApproval,
  supportsResume,
  supportsStructuredApproval,
  transportLabel,
  useBackendCatalog,
} from "@/lib/backends";
import { clearToken } from "@/lib/store";
import {
  isPlanEvent,
  itemIdForEvent,
  planForEvent,
  type PlanDecision,
  type PlanViewModel,
} from "@/lib/events";
import { useTheme } from "@/lib/theme";
import { SessionTerminalView } from "@/components/SessionTerminalView";
import { type XTerminalHandle } from "@/components/XTerminal";
import { ApprovalRequestCard, PlanApprovalCard } from "@/components/ApprovalCard";
import {
  PendingUserInputCard,
  TranscriptCard,
  ToolCallRunGroup,
  readToolName,
  type AskAnswerEntry,
  type ToolPair,
} from "@/components/TranscriptCard";
import { useSwitcher } from "@/components/SwitcherProvider";
import { UsageBar, UsageReadout } from "@/components/UsageReadout";
import {
  BackendModelOption,
  BackendPermissionMode,
  CommandCompletion,
  EventRecord,
  SessionCommandInvocation,
  SessionContextUsage,
  SessionEnvelope,
  SessionRecord,
  SessionTransport,
} from "@/lib/types";
import {
  clampPercent,
  formatRelativeTime,
  formatTokens,
  rateLimitUsageTone,
} from "@/lib/usage";

const COMPLETION_REFRESH_POLL_MS = 750;
const COMPLETION_FETCH_DEBOUNCE_MS = 180;

// `/new` is universal across every backend, so it stays visible during
// the debounced fetch or if the request fails. `/fork` is gated on
// per-plugin `supports_fork` capability and is left to the backend
// response — showing it locally would surface it for tmux sessions
// where the action would 400 on submit.
const LOCAL_BUILTIN_FALLBACK: ReadonlyArray<CommandCompletion> = [
  {
    id: "waypoint:builtin:new",
    trigger: "/",
    replacement: "/new ",
    name: "new",
    description: "Start a new session with the same settings",
    kind: "session_control",
    source: "waypoint",
    dispatch: "frontend_control",
    metadata: {},
  },
];

function completionCommand(entry: CommandCompletion): string {
  return `${entry.trigger}${entry.name}`;
}

function mergeBuiltinFallback(
  backend: ReadonlyArray<CommandCompletion>,
): CommandCompletion[] {
  const seen = new Set(backend.map(completionCommand));
  const merged = [...backend];
  for (const entry of LOCAL_BUILTIN_FALLBACK) {
    if (!seen.has(completionCommand(entry))) {
      merged.push(entry);
    }
  }
  return merged;
}

const EFFORT_LABEL: Record<string, string> = {
  none: "None",
  minimal: "Minimal",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra high",
  max: "Max",
};

function contextUsagePercent(usage: SessionContextUsage): number | null {
  const windowTokens = usage.context_window_tokens;
  if (!windowTokens || windowTokens <= 0) {
    return null;
  }
  return Math.round((usage.used_tokens / windowTokens) * 100);
}

function contextUsageTone(percent: number | null): "good" | "warn" | "danger" {
  if (percent === null) {
    return "good";
  }
  if (percent >= 90) {
    return "danger";
  }
  if (percent >= 70) {
    return "warn";
  }
  return "good";
}

function contextUsageLabel(key: string): string {
  switch (key) {
    case "input_tokens":
      return "Input";
    case "cached_input_tokens":
      return "Cached input";
    case "output_tokens":
      return "Output";
    case "reasoning_output_tokens":
      return "Reasoning";
    case "reasoning_tokens":
      return "Reasoning";
    case "cache_read_tokens":
      return "Cache read";
    case "cache_write_tokens":
      return "Cache write";
    default:
      return key.replaceAll("_", " ");
  }
}

interface SessionDetailProps {
  host: string;
  token: string;
  sessionId: string;
  onAuthFailure?: () => void;
}

type ViewMode = "chat" | "terminal";
type FilterMode = "important" | "all";
type ConnectionState = "connecting" | "open" | "reconnecting";

// The composer sticks to the viewport bottom; floating scroll affordances
// read `--composer-height` to sit just above it. The fallback keeps things
// sensible for the very first paint before the observer fires.
const COMPOSER_HEIGHT_FALLBACK = 220;
const COMPOSER_HEIGHT_STORAGE_KEY = "waypoint-composer-height";
// Mirrors the mobile min-height in globals.css so the resized desktop
// composer can never shrink below what mobile already enforces.
const COMPOSER_MIN_HEIGHT = 56;
const SHORTCUT_IS_MAC =
  typeof navigator !== "undefined" &&
  /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent || "");

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

export function SessionDetail({ host, token, sessionId, onAuthFailure }: SessionDetailProps) {
  const router = useRouter();
  const catalog = useBackendCatalog(host || null, token || null, null);
  const [session, setSession] = useState<SessionRecord | null>(null);
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [snapshot, setSnapshot] = useState("");
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const [view, setView] = useState<ViewMode>("chat");
  const [filterMode, setFilterMode] = useState<FilterMode>("important");
  const [toolRunsExpanded, setToolRunsExpanded] = useState(false);
  const [error, setError] = useState("");
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const [showScrollToTop, setShowScrollToTop] = useState(false);
  const [modeBusy, setModeBusy] = useState(false);
  const [modelOptions, setModelOptions] = useState<BackendModelOption[]>([]);
  const [defaultModelId, setDefaultModelId] = useState<string | null>(null);
  const [defaultModelLabel, setDefaultModelLabel] = useState<string | null>(null);
  const [defaultEffort, setDefaultEffort] = useState<string | null>(null);
  const [modelBusy, setModelBusy] = useState(false);
  const [effortBusy, setEffortBusy] = useState(false);
  const [rateLimitRefreshBusy, setRateLimitRefreshBusy] = useState(false);
  const [approvalPageIndex, setApprovalPageIndex] = useState(0);
  const [hasOlderEvents, setHasOlderEvents] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const { openSwitcher, setCurrentSession } = useSwitcher();
  // Tracks the smallest raw sequence ever received from the server. Distinct
  // from `events[0].sequence` because `mergeEvents` advances a coalesced
  // item's sequence to the *last* delta — using that as a cursor would
  // re-fetch every earlier delta of the same logical message. We compute
  // this from the raw payload before coalescing.
  const [oldestRawSequence, setOldestRawSequence] = useState<number | null>(null);
  const [optimisticMessages, setOptimisticMessages] = useState<
    { tempId: string; text: string; ts: string; confirmed: boolean }[]
  >([]);
  const sectionRef = useRef<HTMLElement | null>(null);
  const nearBottomRef = useRef(true);
  const pendingEventsRef = useRef<EventRecord[]>([]);
  const flushFrameRef = useRef<number | null>(null);
  const renderedEvents = useDeferredValue(events);

  const handleAuthFailure = useCallback(() => {
    clearToken();
    onAuthFailure?.();
    router.replace("/");
  }, [onAuthFailure, router]);

  const refreshSnapshot = useCallback(async () => {
    setSnapshotLoading(true);
    try {
      const text = await fetchTerminalSnapshot(host, token, sessionId);
      setSnapshot(text);
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

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    // Scroll the actual document to its full height — using the document
    // scrollingElement avoids ambiguity between html/body as scroll root and
    // guarantees we reach the page bottom (composer included) instead of
    // stopping at a sentinel that sits above following layout.
    const target = document.documentElement.scrollHeight;
    window.scrollTo({ top: target, behavior });
  }, []);

  const scrollToTop = useCallback((behavior: ScrollBehavior = "smooth") => {
    window.scrollTo({ top: 0, behavior });
  }, []);

  const handlePermissionModeChange = useCallback(
    async (nextMode: string) => {
      if (!session || nextMode === (session.permission_mode ?? "default")) {
        return;
      }
      setModeBusy(true);
      setError("");
      try {
        const updated = await setSessionPermissionMode(host, token, session.id, nextMode);
        setSession(updated);
      } catch (modeError) {
        if (isAuthError(modeError)) {
          handleAuthFailure();
          return;
        }
        setError(
          modeError instanceof Error ? modeError.message : "failed to update mode",
        );
      } finally {
        setModeBusy(false);
      }
    },
    [host, token, session, handleAuthFailure],
  );

  // Refresh the model picker whenever the active backend or launch target
  // changes. Codex's list is auth/account scoped so it can shift between
  // remote SSH targets; for Claude this just reads the curated config list.
  // Depend only on the specific fields we read — including `session` itself
  // would re-fire on every poll-induced reference change and stampede the
  // backend with /models calls.
  const sessionBackend = session?.backend;
  const sessionLaunchTargetId = session?.launch_target_id;
  useEffect(() => {
    if (!sessionBackend) {
      return;
    }
    let cancelled = false;
    fetchBackendModels(host, token, sessionBackend, {
      launchTargetId: sessionLaunchTargetId,
    })
      .then((response) => {
        if (cancelled) return;
        setModelOptions(response.models);
        setDefaultModelId(response.default_model_id ?? null);
        setDefaultModelLabel(response.default_model_label ?? null);
        setDefaultEffort(response.default_effort ?? null);
      })
      .catch((modelsError) => {
        if (cancelled) return;
        if (isAuthError(modelsError)) {
          handleAuthFailure();
          return;
        }
        // Discovery failure is non-fatal: the picker just falls back to
        // showing whatever model the session already has.
        setModelOptions([]);
        setDefaultModelId(null);
        setDefaultModelLabel(null);
        setDefaultEffort(null);
      });
    return () => {
      cancelled = true;
    };
  }, [host, token, sessionBackend, sessionLaunchTargetId, handleAuthFailure]);

  const handleModelChange = useCallback(
    async (nextModel: string) => {
      if (!session) {
        return;
      }
      const cleaned = nextModel.trim() || null;
      const current = session.model ?? null;
      if (cleaned === current) {
        return;
      }
      setModelBusy(true);
      setError("");
      try {
        const updated = await setSessionModel(host, token, session.id, cleaned);
        setSession(updated);
      } catch (modelError) {
        if (isAuthError(modelError)) {
          handleAuthFailure();
          return;
        }
        setError(
          modelError instanceof Error ? modelError.message : "failed to update model",
        );
      } finally {
        setModelBusy(false);
      }
    },
    [host, token, session, handleAuthFailure],
  );

  const handleEffortChange = useCallback(
    async (nextEffort: string) => {
      if (!session) {
        return;
      }
      const cleaned = nextEffort.trim() || null;
      const current = session.effort ?? null;
      if (cleaned === current) {
        return;
      }
      setEffortBusy(true);
      setError("");
      try {
        const updated = await setSessionEffort(host, token, session.id, cleaned);
        setSession(updated);
      } catch (effortError) {
        if (isAuthError(effortError)) {
          handleAuthFailure();
          return;
        }
        setError(
          effortError instanceof Error ? effortError.message : "failed to update effort",
        );
      } finally {
        setEffortBusy(false);
      }
    },
    [host, token, session, handleAuthFailure],
  );

  const flushPendingEvents = useCallback(() => {
    flushFrameRef.current = null;
    const pending = pendingEventsRef.current;
    if (!pending.length) {
      return;
    }
    pendingEventsRef.current = [];
    const confirmedUserTexts = pending
      .filter((e) => e.kind === "user_input")
      .map((e) => e.text);
    if (confirmedUserTexts.length > 0) {
      setOptimisticMessages((prev) => {
        let next = prev;
        for (const text of confirmedUserTexts) {
          const idx = next.findIndex((m) => !m.confirmed && m.text === text);
          if (idx !== -1) {
            next = [
              ...next.slice(0, idx),
              { ...next[idx], confirmed: true },
              ...next.slice(idx + 1),
            ];
          }
        }
        return next;
      });
    }
    startTransition(() => {
      setEvents((current) =>
        pending.reduce<EventRecord[]>((acc, event) => mergeEvents(acc, event), current),
      );
    });
  }, []);

  const queueIncomingEvent = useCallback(
    (event: EventRecord) => {
      pendingEventsRef.current.push(event);
      if (flushFrameRef.current !== null) {
        return;
      }
      flushFrameRef.current = window.requestAnimationFrame(flushPendingEvents);
    },
    [flushPendingEvents],
  );

  const loadOlderEvents = useCallback(async () => {
    if (loadingOlder || !hasOlderEvents || oldestRawSequence === null) {
      return;
    }
    setLoadingOlder(true);
    // Anchor scroll position to the same DOM offset from the document
    // bottom: when older messages are prepended, scrollHeight grows, so we
    // restore by `scrollTo(scrollY + delta)`. Without this the viewport
    // would visibly jump up to the new oldest content.
    const beforeHeight = document.documentElement.scrollHeight;
    const beforeScrollY = window.scrollY;
    try {
      const page = await fetchEvents(host, token, sessionId, {
        beforeSequence: oldestRawSequence,
      });
      if (page.events.length === 0) {
        setHasOlderEvents(false);
        return;
      }
      const sanitized = page.events.map(sanitizeEvent);
      setEvents((current) => foldOlderEvents(current, sanitized));
      setHasOlderEvents(page.has_more);
      const incomingMin = minRawSequence(sanitized);
      if (incomingMin !== null) {
        setOldestRawSequence((current) =>
          current === null ? incomingMin : Math.min(current, incomingMin),
        );
      }
      // Two rAF ticks: the first lets React commit, the second waits for
      // layout to flush so scrollHeight reflects the new transcript.
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => {
          const delta = document.documentElement.scrollHeight - beforeHeight;
          if (delta > 0) {
            // Disable smooth-scroll one-shot to avoid an animated jump.
            window.scrollTo({ top: beforeScrollY + delta, behavior: "auto" });
          }
        });
      });
    } catch (loadError) {
      if (isAuthError(loadError)) {
        handleAuthFailure();
        return;
      }
      setError(
        loadError instanceof Error ? loadError.message : "failed to load older messages",
      );
    } finally {
      setLoadingOlder(false);
    }
  }, [
    handleAuthFailure,
    hasOlderEvents,
    host,
    loadingOlder,
    oldestRawSequence,
    sessionId,
    token,
  ]);

  // Web apps don't get a native "refresh page" affordance; the overflow-menu
  // entry below is the user-visible substitute, so it does the same thing as
  // the browser's reload button. A full reload is also the simplest way to
  // resync after the websocket has been bouncing — no bespoke refetch path
  // to keep in lockstep with the initial-load path.
  const refresh = useCallback(() => {
    window.location.reload();
  }, []);

  // Publish the current session so the global switcher can mark it.
  useEffect(() => {
    setCurrentSession(session);
    return () => setCurrentSession(null);
  }, [session, setCurrentSession]);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const [loadedSession, loadedPage, loadedSnapshot] = await Promise.all([
          fetchSession(host, token, sessionId),
          fetchEvents(host, token, sessionId),
          fetchTerminalSnapshot(host, token, sessionId),
        ]);
        if (!active) {
          return;
        }
        setSession(loadedSession);
        const sanitized = loadedPage.events.map(sanitizeEvent);
        const coalesced = sanitized.reduce<EventRecord[]>(
          (acc, event) => mergeEvents(acc, event),
          [],
        );
        setEvents(coalesced);
        setHasOlderEvents(loadedPage.has_more);
        setOldestRawSequence(minRawSequence(sanitized));
        setSnapshot(loadedSnapshot);
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
            queueIncomingEvent(event);
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
      if (flushFrameRef.current !== null) {
        window.cancelAnimationFrame(flushFrameRef.current);
        flushFrameRef.current = null;
      }
      pendingEventsRef.current = [];
      socket?.close();
    };
  }, [handleAuthFailure, host, token, sessionId, queueIncomingEvent]);

  useEffect(() => {
    if (view === "terminal") {
      void refreshSnapshot();
    }
  }, [view, refreshSnapshot]);

  useEffect(() => {
    if (!session) return;
    const prev = document.title;
    document.title = `${humaniseBackend(session.backend)} · ${session.title}`;
    return () => { document.title = prev; };
  }, [session]);

  useEffect(() => {
    if (view !== "chat") {
      setShowScrollToBottom(false);
      setShowScrollToTop(false);
      return;
    }
    function updateScrollState() {
      const root = document.documentElement;
      const remaining = root.scrollHeight - window.innerHeight - window.scrollY;
      const nearBottom = remaining < 160;
      nearBottomRef.current = nearBottom;
      setShowScrollToBottom(!nearBottom);
      // The "back to top" affordance only earns its place once the user has
      // scrolled meaningfully past the header — otherwise it competes with
      // the page chrome it would scroll back to.
      setShowScrollToTop(window.scrollY > 480);
    }
    updateScrollState();
    window.addEventListener("scroll", updateScrollState, { passive: true });
    window.addEventListener("resize", updateScrollState);
    return () => {
      window.removeEventListener("scroll", updateScrollState);
      window.removeEventListener("resize", updateScrollState);
    };
  }, [view]);

  useEffect(() => {
    if (view !== "chat") {
      return;
    }
    const node = sectionRef.current;
    if (!node) {
      return;
    }
    // Re-anchor to the bottom whenever content reflows (markdown layout,
    // streamed deltas, image loads, approval card appearing, ...). Using a
    // ResizeObserver on the page section catches all of these without
    // depending on event-array reference equality.
    let pending = false;
    const observer = new ResizeObserver(() => {
      if (!nearBottomRef.current || pending) {
        return;
      }
      pending = true;
      window.requestAnimationFrame(() => {
        pending = false;
        if (nearBottomRef.current) {
          scrollToBottom("auto");
        }
      });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [view, scrollToBottom]);

  const submitInput = useCallback(async (
    text: string,
    command?: SessionCommandInvocation,
  ) => {
    if (!text.trim()) {
      return false;
    }
    
    if (text.startsWith("/new")) {
      if (!session) return false;
      const trailing = text.slice(4).trim();
      try {
        const created = await createSession(host, token, {
          backend: session.backend,
          cwd: session.cwd,
          launch_target_id: session.launch_target_id,
          launch_mode: session.launch_mode ?? "auto",
          model: session.model,
          effort: session.effort,
          permission_mode: session.permission_mode,
          args: session.args,
          config_overrides: session.config_overrides,
        });
        if (trailing) {
          await sendInput(host, token, created.id, trailing);
        }
        router.push(`/session/${created.id}`);
        return true;
      } catch (e) {
        if (isAuthError(e)) {
          handleAuthFailure();
          return false;
        }
        setError(e instanceof Error ? e.message : "failed to create session");
        return false;
      }
    }

    if (text.startsWith("/fork")) {
      if (!session) return false;
      const trailing = text.slice(5).trim();
      try {
        const forked = await forkSession(host, token, sessionId);
        if (trailing) {
          await sendInput(host, token, forked.id, trailing);
        }
        router.push(`/session/${forked.id}`);
        return true;
      } catch (e) {
        if (isAuthError(e)) {
          handleAuthFailure();
          return false;
        }
        setError(e instanceof Error ? e.message : "failed to fork session");
        return false;
      }
    }

    try {
      await sendInput(host, token, sessionId, text, command);
      return true;
    } catch (sendError) {
      if (isAuthError(sendError)) {
        handleAuthFailure();
        return false;
      }
      setError(sendError instanceof Error ? sendError.message : "failed to send input");
      return false;
    }
  }, [handleAuthFailure, host, token, sessionId, session, router]);

  const onSendWithOptimistic = useCallback(
    async (text: string, command?: SessionCommandInvocation) => {
      const tempId = Math.random().toString(36).slice(2);
      const ts = new Date().toISOString();
      setOptimisticMessages((prev) => [...prev, { tempId, text, ts, confirmed: false }]);
      try {
        return await submitInput(text, command);
      } finally {
        setOptimisticMessages((prev) => prev.filter((m) => m.tempId !== tempId));
      }
    },
    [submitInput],
  );

  const submitAskAnswer = useCallback(
    async (
      answer: string,
      toolUseId?: string,
      answers?: AskAnswerEntry[],
    ) => {
      if (!answer.trim()) {
        return false;
      }
      try {
        await answerAskQuestion(
          host,
          token,
          sessionId,
          answer,
          toolUseId,
          answers,
        );
        return true;
      } catch (sendError) {
        if (isAuthError(sendError)) {
          handleAuthFailure();
          return false;
        }
        setError(
          sendError instanceof Error ? sendError.message : "failed to send answer",
        );
        return false;
      }
    },
    [handleAuthFailure, host, token, sessionId],
  );

  const runAction = useCallback(async (action: "interrupt" | "resume") => {
    try {
      await postAction(host, token, sessionId, action);
    } catch (actionError) {
      if (isAuthError(actionError)) {
        handleAuthFailure();
        return;
      }
      setError(actionError instanceof Error ? actionError.message : `failed to ${action}`);
    }
  }, [handleAuthFailure, host, token, sessionId]);

  const reattach = useCallback(async () => {
    try {
      await postAction(host, token, sessionId, "reattach");
    } catch (reattachError) {
      if (isAuthError(reattachError)) {
        handleAuthFailure();
        return;
      }
      setError(
        reattachError instanceof Error
          ? reattachError.message
          : "failed to reconnect",
      );
    }
  }, [handleAuthFailure, host, token, sessionId]);

  const terminate = useCallback(async () => {
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
  }, [handleAuthFailure, host, token, sessionId]);

  const handleRateLimitRefresh = useCallback(async () => {
    setRateLimitRefreshBusy(true);
    try {
      const updated = await refreshSessionRateLimitUsage(host, token, sessionId);
      setSession(updated);
    } catch (refreshError) {
      if (isAuthError(refreshError)) {
        handleAuthFailure();
        return;
      }
      setError(
        refreshError instanceof Error
          ? refreshError.message
          : "failed to refresh rate-limit usage",
      );
    } finally {
      setRateLimitRefreshBusy(false);
    }
  }, [handleAuthFailure, host, sessionId, token]);

  const removeFromList = useCallback(async () => {
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
  }, [handleAuthFailure, host, router, token, sessionId]);

  const handleSetTitle = useCallback(
    async (title: string) => {
      try {
        const updated = await setSessionTitle(host, token, sessionId, title);
        setSession(updated);
      } catch (titleError) {
        if (isAuthError(titleError)) {
          handleAuthFailure();
          return;
        }
        setError(titleError instanceof Error ? titleError.message : "failed to update title");
      }
    },
    [host, token, sessionId, handleAuthFailure],
  );

  async function submitApproval(decision: string, text?: string, approvalId?: string) {
    try {
      await approveSession(host, token, sessionId, decision, text, approvalId);
    } catch (approvalError) {
      if (isAuthError(approvalError)) {
        handleAuthFailure();
        return;
      }
      setError(approvalError instanceof Error ? approvalError.message : "failed to send approval");
    }
  }

  async function submitPlanApproval(
    planItemId: string,
    decision: PlanDecision,
    text?: string,
  ) {
    try {
      const updated = await approvePlan(
        host,
        token,
        sessionId,
        planItemId,
        decision,
        text,
      );
      setSession(updated);
    } catch (approvalError) {
      if (isAuthError(approvalError)) {
        handleAuthFailure();
        return;
      }
      setError(
        approvalError instanceof Error
          ? approvalError.message
          : "failed to approve plan",
      );
    }
  }

  const pendingApprovals =
    session && supportsStructuredApproval(session.transport)
      ? findPendingApprovals(events)
      : [];
  const approvalCount = pendingApprovals.length;
  const safeApprovalPage = approvalCount > 0 ? Math.min(approvalPageIndex, approvalCount - 1) : 0;
  const pendingApproval = pendingApprovals[safeApprovalPage] ?? null;
  const agentBusy = session ? isAgentBusy(session, connection) : false;
  const displayEvents = collapseSupersededPlanEvents(renderedEvents);
  const visibleEvents = filterMode === "all" ? displayEvents : displayEvents.filter(isImportantEvent);
  const hiddenEventCount = displayEvents.length - visibleEvents.length;
  const transcriptEvents =
    optimisticMessages.length > 0
      ? filterOptimisticTranscriptEvents(visibleEvents, optimisticMessages)
      : visibleEvents;
  const pendingPlanApprovalEvent =
    session?.permission_mode === "plan" &&
    supportsPlanApproval(session.backend, catalog)
      ? latestActionablePlanEvent(displayEvents)
      : null;
  const pendingPlanApprovalView: PlanViewModel | null = pendingPlanApprovalEvent
    ? planForEvent(pendingPlanApprovalEvent)
    : null;
  const transcriptEventsForDisplay = pendingPlanApprovalEvent
    ? transcriptEvents.filter((event) => event !== pendingPlanApprovalEvent)
    : transcriptEvents;
  const transcriptItems = buildTranscriptItems(transcriptEventsForDisplay);
  const hasToolRuns = transcriptItems.some((item) => item.kind === "tool_run");
  // Session has stopped its backend process (clean shutdown or crash).
  const sessionExited = Boolean(
    session && (session.status === "exited" || session.status === "error"),
  );
  const terminalOnly = session?.transport === "tmux";
  const dormantReattach = sessionExited;
  const composerDisabled = !session || sessionExited;
  const composerPlaceholder = !session
    ? "Loading session…"
    : dormantReattach
      ? "Session has exited — send a message to reattach…"
      : composerDisabled
        ? "Session has exited — composer disabled."
        : "Reply to the agent…";
  // Tmux's Resume control only makes sense while the pane is alive; once the
  // session has exited there is nothing to resume into.
  const canResume = Boolean(
    session && supportsResume(session.transport) && !sessionExited,
  );
  const activeView: ViewMode = terminalOnly ? "terminal" : view;
  const liveTmux = session?.transport === "tmux";
  const { theme } = useTheme();
  const terminalRef = useRef<XTerminalHandle | null>(null);
  const terminalSocketRef = useRef<WebSocket | null>(null);
  // Bumped on every EXITED → live transition so the terminal-WS effect
  // re-runs against the reborn tmux pane instead of the closed socket.
  const [terminalEpoch, setTerminalEpoch] = useState(0);
  const prevSessionExitedRef = useRef(sessionExited);
  useEffect(() => {
    if (prevSessionExitedRef.current && !sessionExited) {
      setTerminalEpoch((e) => e + 1);
    }
    prevSessionExitedRef.current = sessionExited;
  }, [sessionExited]);
  // Push the latest REST snapshot to xterm whenever it changes (or when the
  // terminal tab mounts). For tmux sessions the WebSocket below seeds and
  // streams the pane directly, so skip the REST replay to avoid duplicating
  // content.
  useEffect(() => {
    if (activeView !== "terminal") return;
    if (liveTmux) return;
    const term = terminalRef.current;
    if (!term) return;
    term.reset();
    if (snapshot) {
      term.write(snapshot);
    }
  }, [activeView, snapshot, liveTmux]);

  // Live tmux pane: connect a WebSocket, write streamed bytes into xterm,
  // forward keystrokes and viewport-resize back to the pane. Reconnects with
  // capped exponential backoff on transient drops.
  //
  // Deliberately does NOT depend on the full ``session`` object — the
  // session-state WS pushes updated SessionRecord references on every change
  // (effort/model/etc.), and re-running this effect would close the terminal
  // socket and ``term.reset()`` xterm on every push, which the user sees as
  // the whole pane blanking out and re-painting. ``liveTmux`` already implies
  // ``session !== null``.
  useEffect(() => {
    if (activeView !== "terminal") return;
    if (!liveTmux) return;
    let active = true;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    function connect() {
      terminalRef.current?.reset();
      socket = connectTerminalSocket(host, token, sessionId, {
        onOpen: () => {
          attempt = 0;
          // Push the current viewport size so tmux resizes the pane to
          // match — otherwise xterm renders the seed at whatever pane size
          // the agent last used. Read the live ref because the term may
          // not have mounted at connect() time; the closure captured null
          // would silently skip this resize and leave the pane stuck at
          // xterm's default 80x24 if fit() produces the same dims and
          // never fires onResize.
          const term = terminalRef.current;
          const cols = term?.cols();
          const rows = term?.rows();
          if (cols && rows && socket?.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: "resize", cols, rows }));
          }
        },
        onChunk: (text) => {
          terminalRef.current?.write(text);
        },
        onAuthFailure: () => {
          handleAuthFailure();
        },
        onSessionExited: () => {
          // The pane is gone (terminate / /exit / Codex crashed). Drop
          // out of the auto-reconnect loop; the UI's Reconnect button
          // is what spins up a fresh tmux session.
          active = false;
        },
        onClose: () => {
          if (!active) return;
          const delay = Math.min(RECONNECT_BASE_MS * 2 ** attempt, RECONNECT_MAX_MS);
          attempt += 1;
          reconnectTimer = setTimeout(() => {
            if (active) connect();
          }, delay);
        },
      });
      terminalSocketRef.current = socket;
    }

    connect();
    return () => {
      active = false;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
      terminalSocketRef.current = null;
    };
  }, [activeView, liveTmux, host, token, sessionId, handleAuthFailure, terminalEpoch]);

  const handleTerminalInput = useCallback((data: string) => {
    const socket = terminalSocketRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "input", data }));
    }
  }, []);

  const [terminalDims, setTerminalDims] = useState<{ cols: number; rows: number } | null>(null);
  const handleTerminalResize = useCallback(
    ({ cols, rows }: { cols: number; rows: number }) => {
      setTerminalDims({ cols, rows });
      const socket = terminalSocketRef.current;
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "resize", cols, rows }));
      }
    },
    [],
  );
  const handleTerminalScrollChip = useCallback(
    (direction: "up" | "down") => {
      // SGR mouse encoding (mode 1006). Button 64 = wheel up, 65 =
      // wheel down. Column/row are the cursor position the inner app
      // attributes the wheel event to; centering on the viewport is
      // a safe stand-in for "user's eye" without coupling to xterm's
      // actual mouse position.
      const cols = terminalDims?.cols ?? 80;
      const rows = terminalDims?.rows ?? 24;
      const col = Math.max(1, Math.floor(cols / 2));
      const row = Math.max(1, Math.floor(rows / 2));
      const button = direction === "up" ? 64 : 65;
      handleTerminalInput(`\x1b[<${button};${col};${row}M`);
    },
    [terminalDims, handleTerminalInput],
  );
  const [termMenuOpen, setTermMenuOpen] = useState(false);
  const [termAtBottom, setTermAtBottom] = useState(true);
  const termMenuWrapRef = useRef<HTMLDivElement | null>(null);
  // Click-outside + Escape close the overflow menu. Bound only while the
  // menu is open so we don't pay the listener cost in the common case.
  useEffect(() => {
    if (!termMenuOpen) return;
    function onPointerDown(event: PointerEvent) {
      const wrap = termMenuWrapRef.current;
      if (wrap && !wrap.contains(event.target as Node)) {
        setTermMenuOpen(false);
      }
    }
    function onKey(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") setTermMenuOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [termMenuOpen]);
  const handleJumpToLive = useCallback(() => {
    terminalRef.current?.scrollToBottom();
  }, []);
  const handleTerminalScrollChange = useCallback((atBottom: boolean) => {
    setTermAtBottom(atBottom);
  }, []);
  // "Refresh" on the terminal page means different things for live vs.
  // read-only sessions: live tmux already streams every byte, so the
  // useful refresh is bumping the WS epoch — that closes the current
  // socket, opens a fresh one, and re-seeds the pane from the server's
  // current state. For read-only snapshots we still hit the REST
  // /snapshot endpoint to pull the latest capture.
  const handleTerminalRefresh = useCallback(() => {
    if (liveTmux) {
      setTerminalEpoch((e) => e + 1);
    } else {
      void refreshSnapshot();
    }
  }, [liveTmux, refreshSnapshot]);
  const interruptSession = useCallback(() => {
    void runAction("interrupt");
  }, [runAction]);
  const resumeSession = useCallback(() => {
    void runAction("resume");
  }, [runAction]);

  return (
    <section className="stack" ref={sectionRef}>
      {!terminalOnly && activeView === "chat" && showScrollToTop ? (
        <div className="scroll-top-floater" aria-hidden={false}>
          <button
            type="button"
            className="scroll-top-pill"
            onClick={() => scrollToTop()}
            aria-label="Back to top"
            title="Back to top"
          >
            ↑
          </button>
        </div>
      ) : null}
      {session ? (
        <SessionHeader
          session={session}
          connection={connection}
          modelOptions={modelOptions}
          onSetTitle={handleSetTitle}
        />
      ) : (
        <div className="session-loading muted" role="status" aria-live="polite">
          Loading session…
        </div>
      )}
      {session ? (
        <div className="session-toolbar">
          {terminalOnly ? (
            <div className="segmented segmented-quiet" aria-label="View mode">
              <span className="segmented-item active">Terminal</span>
            </div>
          ) : (
            <div className="segmented" role="tablist" aria-label="View">
              <button
                type="button"
                role="tab"
                aria-selected={activeView === "chat"}
                className={`segmented-item ${activeView === "chat" ? "active" : ""}`}
                onClick={() => setView("chat")}
              >
                Chat
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={activeView === "terminal"}
                className={`segmented-item ${activeView === "terminal" ? "active" : ""}`}
                onClick={() => setView("terminal")}
              >
                Terminal
              </button>
            </div>
          )}
          {!terminalOnly && activeView === "chat" ? (
            <div className="segmented segmented-quiet" role="radiogroup" aria-label="Event filter">
              <button
                type="button"
                role="radio"
                aria-checked={filterMode === "important"}
                className={`segmented-item ${filterMode === "important" ? "active" : ""}`}
                onClick={() => { setFilterMode("important"); setToolRunsExpanded(false); }}
              >
                Important
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={filterMode === "all"}
                className={`segmented-item ${filterMode === "all" ? "active" : ""}`}
                onClick={() => { setFilterMode("all"); setToolRunsExpanded(true); }}
              >
                All events
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
      {session && !terminalOnly && activeView === "chat" ? (
        <section className="stack transcript-stack">
          {hasOlderEvents ? (
            <div className="transcript-load-older">
              <button
                type="button"
                className="secondary"
                onClick={() => void loadOlderEvents()}
                disabled={loadingOlder}
              >
                {loadingOlder ? "Loading older messages…" : "Load older messages"}
              </button>
            </div>
          ) : null}
          {filterMode === "important" && hiddenEventCount > 0 ? (
            <p className="filter-hint">
              Hiding {hiddenEventCount} low-signal event{hiddenEventCount === 1 ? "" : "s"} ·{" "}
              <button
                type="button"
                className="link-button"
                onClick={() => setFilterMode("all")}
              >
                show all
              </button>
            </p>
          ) : null}
          {session && transcriptItems.length > 0
            ? transcriptItems.map((item, index) => {
                if (item.kind === "tool_run") {
                  const toolNames = item.items
                    .map((child) => {
                      const event = child.kind === "pair" ? child.pair.call ?? child.pair.result : child.event;
                      return event ? readToolName(event) : null;
                    })
                    .filter((name): name is string => name !== null);
                  return (
                    <ToolCallRunGroup
                      key={`run-${index}`}
                      toolNames={toolNames}
                      initiallyOpen={toolRunsExpanded || filterMode === "all"}
                    >
                      {item.items.map((child) =>
                        child.kind === "pair" ? (
                          <TranscriptCard
                            event={child.pair.call ?? child.pair.result ?? child.event}
                            pair={child.pair}
                            transport={session.transport}
                            onAnswerAskQuestion={submitAskAnswer}
                            key={`pair-${child.pair.itemId}`}
                          />
                        ) : (
                          <TranscriptCard
                            event={child.event}
                            transport={session.transport}
                            onAnswerAskQuestion={submitAskAnswer}
                            key={`${child.event.sequence}-${child.event.id ?? "local"}`}
                          />
                        ),
                      )}
                    </ToolCallRunGroup>
                  );
                }
                return item.kind === "pair" ? (
                  <TranscriptCard
                    event={item.pair.call ?? item.pair.result ?? item.event}
                    pair={item.pair}
                    transport={session.transport}
                    onAnswerAskQuestion={submitAskAnswer}
                    key={`pair-${item.pair.itemId}`}
                  />
                ) : (
                  <TranscriptCard
                    event={item.event}
                    transport={session.transport}
                    onAnswerAskQuestion={submitAskAnswer}
                    key={`${item.event.sequence}-${item.event.id ?? "local"}`}
                  />
                );
              })
            : session && optimisticMessages.length === 0 && !pendingPlanApprovalEvent
              ? (
                <TranscriptEmpty
                  status={session.status}
                  filterMode={filterMode}
                  hiddenEventCount={hiddenEventCount}
                  onShowAll={() => setFilterMode("all")}
                />
              )
              : null}
          {session
            ? optimisticMessages.map((msg) => (
              <PendingUserInputCard
                  key={msg.tempId}
                  text={msg.text}
                  ts={msg.ts}
                  confirmed={msg.confirmed}
                />
              ))
            : null}
        </section>
      ) : null}
      {session && activeView === "terminal" ? (
        <SessionTerminalView
          session={session}
          liveTmux={liveTmux}
          terminalRef={terminalRef}
          theme={theme}
          terminalDims={terminalDims}
          snapshotLoading={snapshotLoading}
          sessionExited={sessionExited}
          dormantReattach={dormantReattach}
          locked={terminalOnly}
          termMenuOpen={termMenuOpen}
          setTermMenuOpen={setTermMenuOpen}
          termMenuWrapRef={termMenuWrapRef}
          termAtBottom={termAtBottom}
          onTerminalInput={handleTerminalInput}
          onTerminalResize={handleTerminalResize}
          onTerminalScrollChip={handleTerminalScrollChip}
          onTerminalScrollChange={handleTerminalScrollChange}
          onJumpToLive={handleJumpToLive}
          onRefresh={handleTerminalRefresh}
          onReattach={reattach}
          onTerminate={terminate}
          onRemoveFromList={removeFromList}
          onSwitchSession={openSwitcher}
        />
      ) : null}
      {!terminalOnly && pendingApproval ? (
        <>
          {approvalCount > 1 ? (
            <div className="approval-pager">
              <button
                type="button"
                className="approval-pager-btn"
                onClick={() => setApprovalPageIndex((i) => Math.max(0, i - 1))}
                disabled={safeApprovalPage === 0}
                aria-label="Previous approval"
              >
                ‹
              </button>
              <span className="approval-pager-label">
                {safeApprovalPage + 1} / {approvalCount}
              </span>
              <button
                type="button"
                className="approval-pager-btn"
                onClick={() => setApprovalPageIndex((i) => Math.min(approvalCount - 1, i + 1))}
                disabled={safeApprovalPage === approvalCount - 1}
                aria-label="Next approval"
              >
                ›
              </button>
            </div>
          ) : null}
          <ApprovalRequestCard
            event={pendingApproval}
            onDecide={submitApproval}
            supportsNote={session ? supportsApprovalNote(session.backend, catalog) : false}
          />
        </>
      ) : null}
      {!terminalOnly && !pendingApproval && pendingPlanApprovalView ? (
        <PlanApprovalCard
          agentLabel={session ? humaniseBackend(session.backend) : "Codex"}
          canApprove
          decisions={pendingPlanApprovalView.decisions}
          onDecide={(decision, note) =>
            submitPlanApproval(pendingPlanApprovalView.id, decision, note)
          }
          plan={pendingPlanApprovalView.text}
        />
      ) : null}
      {!terminalOnly && activeView === "chat" && showScrollToBottom ? (
        <div className="scroll-latest-floater" aria-hidden={false}>
          <button
            type="button"
            className="scroll-latest-pill"
            onClick={() => scrollToBottom()}
            aria-label="Scroll to latest"
          >
            <span className="arrow">↓</span>
            <span>Jump to latest</span>
          </button>
        </div>
      ) : null}
      {error ? (
        <div className="session-error-toast" role="alert">
          <span>{error}</span>
          <button
            type="button"
            className="session-error-toast-dismiss"
            onClick={() => setError("")}
            aria-label="Dismiss error"
          >
            ×
          </button>
        </div>
      ) : null}
      {!terminalOnly ? (
        <ReplyComposer
          host={host}
          token={token}
          sessionId={sessionId}
          session={session}
          permissionModeOptions={
            session ? permissionModesFor(session.backend, catalog) : []
          }
          canDelete={sessionExited}
          canResume={canResume}
          canReattach={dormantReattach}
          canTerminate={Boolean(session && !sessionExited)}
          connection={connection}
          disabled={composerDisabled}
          rateLimitRefreshBusy={rateLimitRefreshBusy}
          dormant={dormantReattach}
          placeholder={composerPlaceholder}
          agentBusy={agentBusy}
          modeBusy={modeBusy}
          modelBusy={modelBusy}
          modelOptions={modelOptions}
          defaultModelId={defaultModelId}
          defaultModelLabel={defaultModelLabel}
          defaultEffort={defaultEffort}
          currentModel={session?.model ?? null}
          currentEffort={session?.effort ?? null}
          effortBusy={effortBusy}
          permissionMode={session?.permission_mode ?? null}
          transport={session?.transport ?? null}
          effortRequiresConfirm={
            // The plugin advertises "effort swap requires a restart"
            // (Claude does, Codex doesn't) and we surface the confirm
            // step accordingly. Falls back to false until the catalog
            // hydrates so a fresh load doesn't gate the picker.
            Boolean(
              session &&
                catalog
                  .byId(session.backend)
                  ?.capabilities.supports_set_effort_with_restart,
            )
          }
          hasToolRuns={hasToolRuns}
          toolRunsExpanded={toolRunsExpanded}
          onToggleToolRuns={() => {
            const next = !toolRunsExpanded;
            document.querySelectorAll<HTMLDetailsElement>("details.tool-call-run").forEach((el) => { el.open = next; });
            setToolRunsExpanded(next);
          }}
          onDelete={removeFromList}
          onInterrupt={interruptSession}
          onModeChange={handlePermissionModeChange}
          onModelChange={handleModelChange}
          onEffortChange={handleEffortChange}
          onRefresh={refresh}
          onRateLimitRefresh={handleRateLimitRefresh}
          onReattach={reattach}
          onResume={resumeSession}
          onSwitchSession={openSwitcher}
          onSend={onSendWithOptimistic}
          onTerminate={terminate}
        />
      ) : null}
    </section>
  );
}

interface ReplyComposerProps {
  host: string;
  token: string;
  sessionId: string;
  session: SessionRecord | null;
  agentBusy: boolean;
  permissionModeOptions: readonly BackendPermissionMode[];
  hasToolRuns: boolean;
  toolRunsExpanded: boolean;
  onToggleToolRuns: () => void;
  canDelete: boolean;
  canResume: boolean;
  canReattach: boolean;
  canTerminate: boolean;
  connection: ConnectionState;
  disabled: boolean;
  rateLimitRefreshBusy: boolean;
  dormant: boolean;
  placeholder: string;
  modeBusy: boolean;
  modelBusy: boolean;
  modelOptions: BackendModelOption[];
  defaultModelId: string | null;
  defaultModelLabel: string | null;
  defaultEffort: string | null;
  currentModel: string | null;
  currentEffort: string | null;
  effortBusy: boolean;
  permissionMode: string | null;
  transport: SessionTransport | null;
  // True when the backend's effort swap requires a session restart
  // (Claude respawns the CLI). Drives the "confirm before applying"
  // UX so the user knows the session will restart, vs. Codex which
  // applies inline.
  effortRequiresConfirm: boolean;
  onDelete: () => void | Promise<void>;
  onInterrupt: () => void | Promise<void>;
  onModeChange: (mode: string) => void | Promise<void>;
  onModelChange: (model: string) => void | Promise<void>;
  onEffortChange: (effort: string) => void | Promise<void>;
  onRefresh: () => void;
  onRateLimitRefresh: () => void | Promise<void>;
  onReattach: () => void | Promise<void>;
  onResume: () => void | Promise<void>;
  onSwitchSession: () => void;
  onSend: (text: string, command?: SessionCommandInvocation) => Promise<boolean>;
  onTerminate: () => void | Promise<void>;
}

const ReplyComposer = memo(function ReplyComposer({
  host,
  token,
  sessionId,
  session,
  agentBusy,
  permissionModeOptions,
  canDelete,
  canResume,
  canReattach,
  canTerminate,
  connection,
  disabled,
  rateLimitRefreshBusy,
  dormant,
  placeholder,
  modeBusy,
  modelBusy,
  modelOptions,
  defaultModelId,
  defaultModelLabel,
  defaultEffort,
  currentModel,
  currentEffort,
  effortBusy,
  permissionMode,
  transport,
  effortRequiresConfirm,
  hasToolRuns,
  toolRunsExpanded,
  onToggleToolRuns,
  onDelete,
  onInterrupt,
  onModeChange,
  onModelChange,
  onEffortChange,
  onRefresh,
  onRateLimitRefresh,
  onReattach,
  onResume,
  onSwitchSession,
  onSend,
  onTerminate,
}: ReplyComposerProps) {
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [suggestionIndex, setSuggestionIndex] = useState(0);
  const [suggestionsDismissed, setSuggestionsDismissed] = useState(false);
  const [backendCompletions, setBackendCompletions] = useState<
    CommandCompletion[]
  >([]);
  const [selectedCompletion, setSelectedCompletion] =
    useState<CommandCompletion | null>(null);
  const [overflowOpen, setOverflowOpen] = useState(false);
  const [contextUsageOpen, setContextUsageOpen] = useState(false);
  const [tuneOpen, setTuneOpen] = useState(false);
  const [reattaching, setReattaching] = useState(false);
  // Pending effort for backends that need a session restart to apply (Claude)
  // — staged here until the user confirms via the Apply button. `null` means
  // no pending change.
  const [pendingEffort, setPendingEffort] = useState<string | null>(null);
  const [textareaHeight, setTextareaHeight] = useState<number | undefined>(undefined);

  // Rehydrate from localStorage post-mount so SSR and client first-render
  // produce identical markup (no inline height) and React doesn't warn
  // about a hydration mismatch.
  useEffect(() => {
    const stored = window.localStorage.getItem(COMPOSER_HEIGHT_STORAGE_KEY);
    if (!stored) return;
    const parsed = Number.parseInt(stored, 10);
    if (Number.isFinite(parsed) && parsed >= COMPOSER_MIN_HEIGHT) {
      setTextareaHeight(parsed);
    }
  }, []);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const composerRef = useRef<HTMLElement | null>(null);
  const suggestionsRef = useRef<HTMLUListElement | null>(null);
  const suggestionItemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const overflowRef = useRef<HTMLDivElement | null>(null);
  const contextUsageRef = useRef<HTMLDivElement | null>(null);
  const tuneRef = useRef<HTMLDivElement | null>(null);

  // Built-in slash commands are intercepted on the backend only for
  // structured transports (see plugin.maybe_handle_input); skip
  // suggestions on tmux. While the session is still loading
  // (transport=null), default to off.
  const supportsSlash =
    transport !== null && fidelityFor(transport) === "structured";

  const completionHead = draft.split(/\s/, 1)[0];
  const completionTrigger = completionHead.startsWith("/")
    ? "/"
    : completionHead.startsWith("$")
      ? "$"
      : null;
  const suggestions = supportsSlash && !suggestionsDismissed
    ? (completionTrigger === "/"
        ? mergeBuiltinFallback(backendCompletions)
        : backendCompletions
      ).filter(
        (entry) =>
          completionTrigger !== null &&
          completionCommand(entry).startsWith(completionHead),
      )
    : [];
  const suggestionsOpen = suggestions.length > 0 && /^\S+$/.test(draft);
  const activeIndex = Math.min(suggestionIndex, Math.max(0, suggestions.length - 1));

  useEffect(() => {
    if (!supportsSlash || completionTrigger === null) {
      setBackendCompletions([]);
      return;
    }
    const controller = new AbortController();
    let debounceTimer: number | null = null;
    let pollTimer: number | null = null;
    const loadCompletions = () => {
      debounceTimer = null;
      fetchSessionCompletionsResponse(
        host,
        token,
        sessionId,
        completionTrigger,
        completionHead,
        false,
        controller.signal,
      )
        .then((payload) => {
          if (controller.signal.aborted) {
            return;
          }
          setBackendCompletions(payload.completions);
          if (payload.refreshing) {
            pollTimer = window.setTimeout(
              loadCompletions,
              COMPLETION_REFRESH_POLL_MS,
            );
          }
        })
        .catch((error) => {
          if (error instanceof DOMException && error.name === "AbortError") {
            return;
          }
          setBackendCompletions([]);
        });
    };
    debounceTimer = window.setTimeout(
      loadCompletions,
      COMPLETION_FETCH_DEBOUNCE_MS,
    );
    return () => {
      controller.abort();
      if (debounceTimer !== null) {
        window.clearTimeout(debounceTimer);
      }
      if (pollTimer !== null) {
        window.clearTimeout(pollTimer);
      }
    };
  }, [host, token, sessionId, supportsSlash, completionTrigger, completionHead]);

  useEffect(() => {
    setSuggestionIndex(0);
  }, [completionHead]);

  useEffect(() => {
    if (!suggestionsOpen) return;
    const active = suggestionItemRefs.current[activeIndex];
    const list = suggestionsRef.current;
    if (!active || !list) return;
    const activeTop = active.offsetTop;
    const activeBottom = activeTop + active.offsetHeight;
    const visibleTop = list.scrollTop;
    const visibleBottom = visibleTop + list.clientHeight;
    if (activeTop < visibleTop) {
      list.scrollTop = activeTop;
    } else if (activeBottom > visibleBottom) {
      list.scrollTop = activeBottom - list.clientHeight;
    }
  }, [activeIndex, suggestionsOpen, suggestions.length]);

  useEffect(() => {
    if (!draft.startsWith("/") && !draft.startsWith("$")) {
      setSuggestionsDismissed(false);
    }
  }, [draft]);

  useEffect(() => {
    if (
      selectedCompletion &&
      !draft.startsWith(completionCommand(selectedCompletion))
    ) {
      setSelectedCompletion(null);
    }
  }, [draft, selectedCompletion]);

  // Publish the composer's actual height as a CSS custom property so the
  // transcript end-spacer and floating "Jump to latest" pill can position
  // themselves accurately. Falls back to a reasonable default before the
  // observer fires.
  useEffect(() => {
    const node = composerRef.current;
    if (!node) {
      return;
    }
    const apply = () => {
      const height = Math.round(node.getBoundingClientRect().height);
      document.documentElement.style.setProperty(
        "--composer-height",
        `${height || COMPOSER_HEIGHT_FALLBACK}px`,
      );
    };
    apply();
    const observer = new ResizeObserver(apply);
    observer.observe(node);
    return () => {
      observer.disconnect();
      document.documentElement.style.removeProperty("--composer-height");
    };
  }, []);

  // Close the overflow menu on outside click / Escape so destructive actions
  // don't linger if the user changes their mind.
  useEffect(() => {
    if (!overflowOpen) {
      return;
    }
    function onPointer(event: PointerEvent) {
      if (!overflowRef.current) return;
      if (overflowRef.current.contains(event.target as Node)) return;
      setOverflowOpen(false);
    }
    function onKey(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        setOverflowOpen(false);
      }
    }
    window.addEventListener("pointerdown", onPointer);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onPointer);
      window.removeEventListener("keydown", onKey);
    };
  }, [overflowOpen]);

  useEffect(() => {
    if (!contextUsageOpen) {
      return;
    }
    function onPointer(event: PointerEvent) {
      if (!contextUsageRef.current) return;
      if (contextUsageRef.current.contains(event.target as Node)) return;
      setContextUsageOpen(false);
    }
    function onKey(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        setContextUsageOpen(false);
      }
    }
    window.addEventListener("pointerdown", onPointer);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onPointer);
      window.removeEventListener("keydown", onKey);
    };
  }, [contextUsageOpen]);

  useEffect(() => {
    if (!tuneOpen) {
      return;
    }
    function onPointer(event: PointerEvent) {
      if (!tuneRef.current) return;
      if (tuneRef.current.contains(event.target as Node)) return;
      setTuneOpen(false);
    }
    function onKey(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        setTuneOpen(false);
      }
    }
    window.addEventListener("pointerdown", onPointer);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onPointer);
      window.removeEventListener("keydown", onKey);
    };
  }, [tuneOpen]);

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const handle = e.currentTarget;
    const pointerId = e.pointerId;
    handle.setPointerCapture(pointerId);
    const startY = e.clientY;
    const startHeight = textareaRef.current?.getBoundingClientRect().height ?? 88;
    let latestHeight = startHeight;

    const onPointerMove = (moveEvent: PointerEvent) => {
      const deltaY = startY - moveEvent.clientY;
      const newHeight = Math.max(COMPOSER_MIN_HEIGHT, startHeight + deltaY);
      latestHeight = newHeight;
      setTextareaHeight(newHeight);
    };

    const finishDrag = () => {
      try {
        handle.releasePointerCapture(pointerId);
      } catch {
        // Capture may already be released (e.g. on pointercancel).
      }
      handle.removeEventListener("pointermove", onPointerMove);
      handle.removeEventListener("pointerup", finishDrag);
      handle.removeEventListener("pointercancel", finishDrag);
      try {
        window.localStorage.setItem(
          COMPOSER_HEIGHT_STORAGE_KEY,
          String(Math.round(latestHeight)),
        );
      } catch {
        // localStorage unavailable (private mode, quota); skip persistence.
      }
    };

    handle.addEventListener("pointermove", onPointerMove);
    handle.addEventListener("pointerup", finishDrag);
    handle.addEventListener("pointercancel", finishDrag);
  };

  function selectedCommandInvocation(text: string): SessionCommandInvocation | undefined {
    if (!selectedCompletion || selectedCompletion.dispatch === "frontend_control") {
      return undefined;
    }
    const command = completionCommand(selectedCompletion);
    if (text !== command && !text.startsWith(`${command} `)) {
      return undefined;
    }
    return {
      completion_id: selectedCompletion.id,
      name: selectedCompletion.name,
      arguments: text.slice(command.length).trim(),
      dispatch: selectedCompletion.dispatch,
      metadata: selectedCompletion.metadata,
    };
  }

  function applySuggestion(index: number) {
    const chosen = suggestions[index];
    if (!chosen) {
      return;
    }
    setDraft(chosen.replacement);
    setSelectedCompletion(chosen);
    setSuggestionsDismissed(true);
    requestAnimationFrame(() => textareaRef.current?.focus());
  }

  async function handleSend() {
    const text = draft.trim();
    if (!text) {
      return;
    }
    setSending(true);
    setDraft("");
    setSelectedCompletion(null);
    setSuggestionsDismissed(false);
    try {
      const sent = await onSend(text, selectedCommandInvocation(text));
      if (!sent) {
        setDraft(text);
      }
    } finally {
      setSending(false);
    }
  }

  function handleDraftKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.nativeEvent.isComposing) {
      return;
    }
    if (event.key === "Tab" && event.shiftKey) {
      event.preventDefault();
      if (permissionModeOptions.length > 0 && !modeBusy) {
        const currentIndex = permissionModeOptions.findIndex(
          (opt) => opt.id === (permissionMode ?? "")
        );
        const nextIndex = (currentIndex + 1) % permissionModeOptions.length;
        void onModeChange(permissionModeOptions[nextIndex].id);
      }
      return;
    }
    if (suggestionsOpen) {
      if (
        event.key === "Tab" ||
        (event.key === "Enter" && !(event.metaKey || event.ctrlKey) && !event.shiftKey)
      ) {
        event.preventDefault();
        applySuggestion(activeIndex);
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setSuggestionIndex((index) => Math.min(suggestions.length - 1, index + 1));
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setSuggestionIndex((index) => Math.max(0, index - 1));
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setSuggestionsDismissed(true);
        return;
      }
    }
    // Treat Cmd+Enter (mac) and Ctrl+Enter (windows/linux) as the send
    // shortcut. Plain Enter inserts a newline as expected.
    if (event.key !== "Enter" || event.shiftKey) {
      return;
    }
    if (!event.metaKey && !event.ctrlKey) {
      return;
    }
    event.preventDefault();
    void handleSend();
  }

  const modeOptions = permissionModeOptions;
  // Refresh is always available, so the overflow menu is always present.
  const hasOverflow = true;
  const shortcutKey = SHORTCUT_IS_MAC ? "⌘" : "Ctrl";
  const hasModelPicker = modelOptions.length > 0 || currentModel !== null;
  // Surface a custom-named model the user already has even if it's not in the
  // curated list, so the dropdown reflects the truth instead of silently
  // showing "Default".
  const modelEntries: BackendModelOption[] =
    currentModel && !modelOptions.some((opt) => opt.id === currentModel)
      ? [
          {
            id: currentModel,
            label: `Custom · ${currentModel}`,
            description: null,
          },
          ...modelOptions,
        ]
      : modelOptions;
  // Effort levels gated by the currently-selected model. With no explicit
  // model pick (rare for a live session, but possible), fall back to the
  // default model's supported levels.
  const resolvedModelId = currentModel || defaultModelId;
  const matchingModelEntry = resolvedModelId
    ? modelOptions.find((opt) => opt.id === resolvedModelId)
    : undefined;
  const effortOptions: string[] = matchingModelEntry
    ? matchingModelEntry.supported_efforts ?? []
    : Array.from(
        new Set(
          modelOptions.flatMap((opt) => opt.supported_efforts ?? []),
        ),
      );
  const hasEffortPicker = effortOptions.length > 0 || currentEffort !== null;
  const effortDisplayValue = pendingEffort ?? (currentEffort ?? "");
  const effortPendingDiffers =
    effortRequiresConfirm &&
    pendingEffort !== null &&
    pendingEffort !== (currentEffort ?? "");
  const contextUsage = session?.context_usage ?? null;
  const rateLimitUsage = session?.rate_limit_usage ?? null;
  const contextUsagePercentValue = contextUsage
    ? contextUsagePercent(contextUsage)
    : null;
  const contextUsagePercentDisplay = clampPercent(contextUsagePercentValue);
  const contextUsageToneValue = contextUsage
    ? contextUsageTone(contextUsagePercentValue)
    : "good";
  const rateLimitUsageToneValue = rateLimitUsageTone(rateLimitUsage);
  const contextUsageBreakdown = contextUsage
    ? Object.entries(contextUsage.breakdown ?? {})
    : [];
  const contextUsageHasWindow =
    contextUsage !== null &&
    typeof contextUsage.context_window_tokens === "number" &&
    contextUsage.context_window_tokens > 0;
  const contextUsageWindowTokens = contextUsageHasWindow
    ? contextUsage.context_window_tokens
    : null;
  const contextUsageWindowDisplay = contextUsageWindowTokens ?? 0;
  const contextUsageSummary = contextUsage
    ? contextUsageWindowTokens !== null && contextUsagePercentDisplay !== null
      ? `${formatTokens(contextUsage.used_tokens)} / ${formatTokens(contextUsageWindowDisplay)} (${contextUsagePercentDisplay}%)`
      : formatTokens(contextUsage.used_tokens)
    : null;
  const rateLimitUsageSummary = rateLimitUsage
    ? rateLimitUsage.windows.length > 0
      ? rateLimitUsage.windows
          .map((window) => `${window.label} ${Math.round(window.used_percent)}%`)
          .join(" · ")
      : rateLimitUsage.notes?.length
        ? rateLimitUsage.notes.join(" · ")
        : null
    : null;
  const rateLimitSourceLabel = rateLimitUsage
    ? rateLimitUsage.notes?.length
      ? rateLimitUsage.notes.join(" · ")
      : humaniseBackend(rateLimitUsage.source)
    : "Unavailable";
  const usageToneValue = (() => {
    if (contextUsage === null) {
      return rateLimitUsageToneValue;
    }
    if (rateLimitUsage === null) {
      return contextUsageToneValue;
    }
    if (
      contextUsageToneValue === "danger" ||
      rateLimitUsageToneValue === "danger"
    ) {
      return "danger";
    }
    if (contextUsageToneValue === "warn" || rateLimitUsageToneValue === "warn") {
      return "warn";
    }
    return "good";
  })();
  const showUsagePopover = contextUsage !== null || rateLimitUsage !== null;
  const usagePopoverTitle = [
    contextUsageSummary ? `Context ${contextUsageSummary}` : null,
    rateLimitUsageSummary ? `Rate limits ${rateLimitUsageSummary}` : null,
  ]
    .filter((part): part is string => part !== null)
    .join(" · ");

  const handleEffortSelect = (next: string) => {
    if (effortRequiresConfirm) {
      // Stage the pick locally; the parent's onEffortChange only fires after
      // explicit confirm so the user knows the session will restart.
      setPendingEffort(next === (currentEffort ?? "") ? null : next);
      return;
    }
    void onEffortChange(next);
  };

  const applyPendingEffort = async () => {
    if (pendingEffort === null) return;
    const value = pendingEffort;
    setPendingEffort(null);
    setTuneOpen(false);
    await onEffortChange(value);
  };

  const tuneVisible = modeOptions.length > 0 || hasModelPicker || hasEffortPicker;
  const tuneSummary = (() => {
    const parts: string[] = [];
    if (modeOptions.length > 0) {
      const matched = modeOptions.find(
        (option) => option.id === (permissionMode ?? "default"),
      );
      parts.push(matched?.label ?? "Default");
    }
    if (hasModelPicker) {
      const matched = modelEntries.find((option) => option.id === (currentModel ?? ""));
       parts.push(matched?.label ?? (currentModel || (defaultModelLabel ? `Default (${defaultModelLabel})` : "Default")));
    }
    if (hasEffortPicker) {
      parts.push(currentEffort ? EFFORT_LABEL[currentEffort] ?? currentEffort : "Default");
    }
    return parts.join(" · ") || "Settings";
  })();

  return (
    <section className="composer" ref={composerRef}>
      <div
        className="composer-resize-handle"
        onPointerDown={handlePointerDown}
        title="Drag to resize composer"
        aria-hidden="true"
      />
      <div className="composer-toprow">
        {tuneVisible ? (
          <div className="composer-tune" ref={tuneRef}>
            <button
              type="button"
              className={`composer-tune-trigger ${tuneOpen ? "open" : ""}`}
              aria-haspopup="dialog"
              aria-expanded={tuneOpen}
              onClick={() => setTuneOpen((open) => !open)}
            >
              <span className="composer-tune-glyph" aria-hidden>
                ⚙
              </span>
              <span className="composer-tune-summary">{tuneSummary}</span>
              {effortPendingDiffers ? (
                <span
                  className="composer-tune-pending"
                  aria-label="Pending change"
                  title="Pending change waits for Apply"
                />
              ) : null}
            </button>
            {tuneOpen ? (
              <div
                className="composer-tune-popover"
                role="dialog"
                aria-label="Session settings"
              >
                {modeOptions.length > 0 ? (
                  <label className="composer-tune-field">
                    <span>Permission mode</span>
                    <select
                      value={permissionMode ?? "default"}
                      onChange={(event) => void onModeChange(event.target.value)}
                      disabled={modeBusy || disabled}
                    >
                      {modeOptions.map((option) => (
                        <option key={option.id} value={option.id}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : null}
                {hasModelPicker ? (
                  <label className="composer-tune-field">
                    <span>Model</span>
                    <select
                      value={currentModel ?? ""}
                      onChange={(event) => void onModelChange(event.target.value)}
                      disabled={modelBusy || disabled}
                    >
                       <option value="">{defaultModelLabel ? `Default (${defaultModelLabel})` : "Default"}</option>
                      {modelEntries.map((option) => (
                        <option key={option.id} value={option.id}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : null}
                {hasEffortPicker ? (
                  <label className="composer-tune-field">
                    <span>Reasoning effort</span>
                    <select
                      value={effortDisplayValue}
                      onChange={(event) => handleEffortSelect(event.target.value)}
                      disabled={effortBusy || disabled}
                    >
                      <option value="">
                        {defaultEffort
                          ? `Default (${EFFORT_LABEL[defaultEffort] ?? defaultEffort})`
                          : "Default"}
                      </option>
                      {effortOptions.map((option) => (
                        <option key={option} value={option}>
                          {EFFORT_LABEL[option] ?? option}
                        </option>
                      ))}
                      {currentEffort && !effortOptions.includes(currentEffort) ? (
                        <option value={currentEffort}>{currentEffort}</option>
                      ) : null}
                    </select>
                  </label>
                ) : null}
                {effortPendingDiffers && pendingEffort ? (
                  <div className="composer-tune-restart">
                    <p>
                      Restart Claude with{" "}
                      <strong>{EFFORT_LABEL[pendingEffort] ?? pendingEffort}</strong> effort?
                      The current turn is interrupted and the session resumes at the new
                      level.
                    </p>
                    <button
                      type="button"
                      className="composer-tune-restart-apply"
                      onClick={() => void applyPendingEffort()}
                      disabled={effortBusy}
                    >
                      {effortBusy ? "Restarting…" : "Apply restart"}
                    </button>
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}
        {agentBusy ? (
          <div
            className="composer-activity"
            role="status"
            aria-live="polite"
            aria-label="Agent is working"
          >
            <span className="composer-activity-spinner" aria-hidden />
          </div>
        ) : null}
        <div className="composer-toprow-trail">
          {showUsagePopover ? (
            <div className="composer-context" ref={contextUsageRef}>
              <button
                type="button"
                className={`composer-connection composer-context-trigger tone-${usageToneValue} ${connection} ${contextUsageOpen ? "open" : ""}`}
                title={
                  usagePopoverTitle
                    ? `Backend socket ${connection}. ${usagePopoverTitle}`
                    : `Backend socket ${connection}. Click for usage details`
                }
                aria-live="polite"
                aria-haspopup="dialog"
                aria-expanded={contextUsageOpen}
                aria-label={`Backend socket ${connection}. Usage details`}
                onClick={() => setContextUsageOpen((open) => !open)}
              >
                {connection === "open"
                  ? "live"
                  : connection === "reconnecting"
                    ? "reconnecting"
                    : "connecting"}
              </button>
              {contextUsageOpen ? (
                <div
                  className={`usage-panel tone-${usageToneValue}`}
                  role="dialog"
                  aria-label="Usage details"
                >
                  <span className="usage-panel-rail" aria-hidden="true" />

                  {contextUsage ? (
                    <section className="usage-block">
                      <header className="usage-block-head">
                        <h3 className="usage-block-eyebrow">
                          <span aria-hidden className="usage-block-mark">
                            ◆
                          </span>
                          Context
                        </h3>
                        <span className="usage-block-tag">
                          {humaniseBackend(contextUsage.source)}
                        </span>
                      </header>
                      <div className="usage-block-body">
                        <div className={`usage-numeral tone-${contextUsageToneValue}`}>
                          <strong>
                            {contextUsagePercentDisplay !== null
                              ? contextUsagePercentDisplay
                              : "—"}
                          </strong>
                          <em>%</em>
                        </div>
                        <div className="usage-block-stack">
                          <p className="usage-line">
                            <span>{formatTokens(contextUsage.used_tokens)}</span>
                            <em>of</em>
                            <span>
                              {contextUsageWindowTokens !== null
                                ? formatTokens(contextUsageWindowDisplay)
                                : "—"}
                            </span>
                            <em>tokens</em>
                          </p>
                          <UsageBar
                            percent={contextUsagePercentDisplay}
                            tone={contextUsageToneValue}
                            disabled={
                              !contextUsageHasWindow ||
                              contextUsagePercentDisplay === null
                            }
                          />
                          <p className="usage-line-meta">
                            <em>updated</em>
                            <span title={new Date(contextUsage.updated_at).toLocaleString()}>
                              {formatRelativeTime(contextUsage.updated_at)}
                            </span>
                          </p>
                        </div>
                      </div>
                      {contextUsageBreakdown.length > 0 ? (
                        <ul className="usage-chips">
                          {contextUsageBreakdown.map(([key, value]) => (
                            <li key={key}>
                              <em>{contextUsageLabel(key)}</em>
                              <strong>{formatTokens(value)}</strong>
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </section>
                  ) : null}

                  {contextUsage && rateLimitUsage ? (
                    <hr className="usage-divider" aria-hidden="true" />
                  ) : null}

                  {rateLimitUsage ? (
                    <UsageReadout
                      usage={rateLimitUsage}
                      sourceLabel={rateLimitSourceLabel}
                      onRefresh={onRateLimitRefresh}
                      refreshing={rateLimitRefreshBusy}
                    />
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : (
            <span
              className={`composer-connection ${connection}`}
              title={`Backend socket ${connection}`}
              role="status"
              aria-live="polite"
            >
              {connection === "open"
                ? "live"
                : connection === "reconnecting"
                  ? "reconnecting"
                  : "connecting"}
            </span>
          )}
        </div>
      </div>
      <div className="reply-textarea-wrap">
        <textarea
          ref={textareaRef}
          className="composer-textarea"
          style={textareaHeight ? { height: textareaHeight } : undefined}
          rows={3}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleDraftKeyDown}
          disabled={disabled}
          placeholder={placeholder}
          aria-label="Reply"
        />
        {suggestionsOpen ? (
          <ul className="slash-suggestions" role="listbox" ref={suggestionsRef}>
            {suggestions.map((entry, index) => (
              <li key={entry.id}>
                <button
                  ref={(node) => {
                    suggestionItemRefs.current[index] = node;
                  }}
                  type="button"
                  role="option"
                  aria-selected={index === activeIndex}
                  className={`slash-suggestion ${index === activeIndex ? "active" : ""}`}
                  onMouseDown={(event) => {
                    event.preventDefault();
                    applySuggestion(index);
                  }}
                  onMouseEnter={() => setSuggestionIndex(index)}
                >
                  <span className="slash-name">
                    {completionCommand(entry)}
                    {entry.argument_hint ? (
                      <span className="slash-hint">{entry.argument_hint}</span>
                    ) : null}
                  </span>
                  <span className="slash-desc">{entry.description}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <div className="composer-actions">
        <button
          className="primary send"
          onClick={() => void handleSend()}
          type="button"
          disabled={disabled || sending || !draft.trim()}
        >
          {sending ? "Sending…" : "Send"}
        </button>
        <button
          className="ghost interrupt"
          onClick={() => void onInterrupt()}
          type="button"
          disabled={disabled || dormant}
          title="Interrupt the agent's current turn"
        >
          Interrupt
        </button>
        {canResume ? (
          <button
            className="ghost"
            onClick={() => void onResume()}
            type="button"
            disabled={disabled}
            title="Resume the underlying tmux session"
          >
            Resume
          </button>
        ) : null}
        <div className="composer-actions-trail">
          <span className="composer-shortcut" aria-hidden>
            <kbd>{shortcutKey}</kbd>
            <span>+</span>
            <kbd>↵</kbd>
            <span>to send</span>
          </span>
          {hasOverflow ? (
            <div className="composer-overflow" ref={overflowRef}>
              <button
                type="button"
                className={`composer-overflow-trigger ${overflowOpen ? "open" : ""}`}
                aria-haspopup="menu"
                aria-expanded={overflowOpen}
                aria-label="More actions"
                onClick={() => setOverflowOpen((open) => !open)}
              >
                ⋯
              </button>
              {overflowOpen ? (
                <div className="composer-overflow-menu" role="menu">
                  <button
                    type="button"
                    role="menuitem"
                    className="composer-overflow-item"
                    onClick={() => {
                      setOverflowOpen(false);
                      onRefresh();
                    }}
                  >
                    <span className="glyph">↻</span>
                    Refresh
                  </button>
                  {canReattach ? (
                    <button
                      type="button"
                      role="menuitem"
                      className="composer-overflow-item"
                      disabled={reattaching}
                      onClick={async () => {
                        setOverflowOpen(false);
                        setReattaching(true);
                        try {
                          await onReattach();
                        } finally {
                          setReattaching(false);
                        }
                      }}
                    >
                      <span className="glyph">↺</span>
                      {reattaching ? "Reconnecting…" : "Reconnect session"}
                    </button>
                  ) : null}
                  <button
                    type="button"
                    role="menuitem"
                    className="composer-overflow-item"
                    onClick={() => {
                      setOverflowOpen(false);
                      onSwitchSession();
                    }}
                  >
                    <span className="glyph">⇄</span>
                    Switch session…
                  </button>
                  {hasToolRuns ? (
                    <button
                      type="button"
                      role="menuitem"
                      className="composer-overflow-item"
                      onClick={() => { onToggleToolRuns(); setOverflowOpen(false); }}
                    >
                      <span className="glyph">{toolRunsExpanded ? "⊟" : "⊞"}</span>
                      {toolRunsExpanded ? "Collapse tools" : "Expand tools"}
                    </button>
                  ) : null}
                  <div className="composer-overflow-separator" />
                  {canTerminate ? (
                    <button
                      type="button"
                      role="menuitem"
                      className="composer-overflow-item danger"
                      onClick={() => {
                        setOverflowOpen(false);
                        void onTerminate();
                      }}
                    >
                      <span className="glyph">⏻</span>
                      Terminate session
                    </button>
                  ) : null}
                  {canDelete ? (
                    <button
                      type="button"
                      role="menuitem"
                      className="composer-overflow-item danger"
                      onClick={() => {
                        setOverflowOpen(false);
                        void onDelete();
                      }}
                    >
                      <span className="glyph">✕</span>
                      Delete transcript
                    </button>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
});

function minRawSequence(events: EventRecord[]): number | null {
  let min: number | null = null;
  for (const event of events) {
    if (min === null || event.sequence < min) {
      min = event.sequence;
    }
  }
  return min;
}

function foldOlderEvents(
  current: EventRecord[],
  older: EventRecord[],
): EventRecord[] {
  // `older` arrives ascending and sits entirely before `current` in
  // sequence space. We must replicate what the forward merge would have
  // produced if the events had originally arrived in true sequence order
  // — naive text prepending breaks tool_result snapshots (a final
  // non-delta supersedes earlier deltas; mergeEventText handles this,
  // but only when we run it in the right direction). So: build a
  // per-target accumulator by replaying mergeEvents over the older
  // events for that item_id, then merge the accumulator with the
  // matching current entry exactly as if the accumulator had arrived
  // first and the current entry second.
  const seenIds = new Set<number>();
  const seenSequences = new Set<number>();
  for (const event of current) {
    if (typeof event.id === "number") seenIds.add(event.id);
    seenSequences.add(event.sequence);
  }
  const currentItemIndex = new Map<string, number>();
  for (let i = 0; i < current.length; i += 1) {
    const event = current[i];
    if (event.kind !== "agent_output" && event.kind !== "tool_result") continue;
    const itemId = readItemId(event);
    if (!itemId) continue;
    currentItemIndex.set(`${event.kind}:${itemId}`, i);
  }
  // For each target current index, the (single) accumulator EventRecord
  // built up from older events sharing that item_id, in sequence order.
  const accumulators = new Map<number, EventRecord>();
  // Older events that don't fold into anything currently visible.
  // Coalesced amongst themselves via mergeEvents — the forward pass is
  // correct here because they are sequence-ascending.
  let standalone: EventRecord[] = [];
  for (const event of older) {
    if (typeof event.id === "number" && seenIds.has(event.id)) continue;
    if (seenSequences.has(event.sequence)) continue;
    const itemId = readItemId(event);
    if ((event.kind === "agent_output" || event.kind === "tool_result") && itemId) {
      const targetIdx = currentItemIndex.get(`${event.kind}:${itemId}`);
      if (targetIdx !== undefined) {
        const existing = accumulators.get(targetIdx);
        if (existing === undefined) {
          accumulators.set(targetIdx, event);
        } else {
          // Replay mergeEvents on a one-element array to get the same
          // text/metadata combine the forward path would have produced.
          const merged = mergeEvents([existing], event);
          accumulators.set(targetIdx, merged[0]);
        }
        continue;
      }
    }
    standalone = mergeEvents(standalone, event);
  }
  let next = current;
  if (accumulators.size > 0) {
    next = current.map((event, index) => {
      const acc = accumulators.get(index);
      if (acc === undefined) return event;
      // Final fold: accumulator (older) plays the role of "existing",
      // the current entry plays the role of "incoming". This routes
      // the merge through mergeEventText with the same arguments
      // forward processing would have used, so all of its branches
      // (snapshot supersedes deltas, todo_list state-replacement,
      // delta append, newline separator for non-delta concat) keep
      // working on the backward path.
      const merged = mergeEvents([acc], event);
      return merged[0];
    });
  }
  return [...standalone, ...next];
}

function mergeEvents(current: EventRecord[], incoming: EventRecord): EventRecord[] {
  const dup = current.some((event) => event.id === incoming.id || event.sequence === incoming.sequence);
  if (dup) {
    return current;
  }
  const incomingItemId = readItemId(incoming);
  if ((incoming.kind === "agent_output" || incoming.kind === "tool_result") && incomingItemId) {
    const index = current.findIndex(
      (event) => event.kind === incoming.kind && readItemId(event) === incomingItemId,
    );
    if (index !== -1) {
      const next = current.slice();
      const existing = next[index];
      next[index] = {
        ...existing,
        text: mergeEventText(existing, incoming),
        metadata: { ...existing.metadata, ...incoming.metadata },
        ts: incoming.ts,
        sequence: incoming.sequence,
      };
      return next;
    }
  }
  return [...current, incoming];
}

function readItemId(event: EventRecord): string | null {
  return itemIdForEvent(event);
}

function mergeEventText(existing: EventRecord, incoming: EventRecord): string {
  if (incoming.kind === "agent_output") {
    return `${existing.text}${incoming.text}`;
  }
  if (incoming.kind !== "tool_result") {
    return incoming.text;
  }
  if (isTodoListEvent(existing) || isTodoListEvent(incoming)) {
    return incoming.text || existing.text;
  }
  if (isToolResultDelta(incoming)) {
    return `${existing.text}${incoming.text}`;
  }
  if (isToolResultDelta(existing)) {
    return existing.text || incoming.text;
  }
  if (!existing.text) {
    return incoming.text;
  }
  if (!incoming.text || existing.text === incoming.text) {
    return existing.text;
  }
  const separator = existing.text.endsWith("\n") || incoming.text.startsWith("\n") ? "" : "\n";
  return `${existing.text}${separator}${incoming.text}`;
}

function collapseSupersededPlanEvents(events: EventRecord[]): EventRecord[] {
  const latestPlanByItemId = new Map<string, EventRecord>();
  for (const event of events) {
    if (!isPlanEvent(event)) {
      continue;
    }
    const itemId = readItemId(event);
    if (itemId) {
      latestPlanByItemId.set(itemId, event);
    }
  }
  if (latestPlanByItemId.size === 0) {
    return events;
  }
  return events.filter((event) => {
    if (!isPlanEvent(event)) {
      return true;
    }
    const itemId = readItemId(event);
    return !itemId || latestPlanByItemId.get(itemId) === event;
  });
}

function latestActionablePlanEvent(events: EventRecord[]): EventRecord | null {
  const decidedPlanIds = new Set<string>();
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    const decisionPlanId = planDecisionItemId(event);
    if (decisionPlanId) {
      decidedPlanIds.add(decisionPlanId);
      continue;
    }
    const plan = planForEvent(event);
    if (!plan || event.kind === "approval_request") {
      continue;
    }
    return decidedPlanIds.has(plan.id) ? null : event;
  }
  return null;
}

function planDecisionItemId(event: EventRecord): string | null {
  const planItemId = event.metadata.plan_item_id;
  if (typeof planItemId !== "string" || !planItemId) {
    return null;
  }
  const decision = event.metadata.plan_decision;
  return typeof decision === "string" ? planItemId : null;
}

type TranscriptItem =
  | { kind: "single"; event: EventRecord }
  | { kind: "pair"; event: EventRecord; pair: ToolPair }
  | { kind: "tool_run"; items: (Extract<TranscriptItem, { kind: "single" | "pair" }>)[] };

function buildTranscriptItems(events: EventRecord[]): TranscriptItem[] {
  const result: (Extract<TranscriptItem, { kind: "single" | "pair" }>)[] = [];
  const pairIndex = new Map<string, number>();
  for (const event of events) {
    if (event.kind !== "tool_call" && event.kind !== "tool_result") {
      result.push({ kind: "single", event });
      continue;
    }
    const itemId = readItemId(event);
    if (!itemId) {
      result.push({ kind: "single", event });
      continue;
    }
    const existingIdx = pairIndex.get(itemId);
    if (existingIdx === undefined) {
      const pair: ToolPair = {
        itemId,
        call: event.kind === "tool_call" ? event : null,
        result: event.kind === "tool_result" ? event : null,
        ts: event.ts,
        sequence: event.sequence,
      };
      pairIndex.set(itemId, result.length);
      result.push({ kind: "pair", event, pair });
      continue;
    }
    const item = result[existingIdx];
    if (item.kind !== "pair") {
      continue;
    }
    if (event.kind === "tool_call") {
      item.pair.call = event;
    } else {
      item.pair.result = item.pair.result
        ? mergeToolResultEvent(item.pair.result, event)
        : event;
    }
    item.pair.ts = event.ts;
    item.pair.sequence = Math.max(item.pair.sequence, event.sequence);
  }

  // Classify each item so the grouping loop is easy to reason about.
  // "content"  → user messages, agent text, approvals, todos, ask-questions:
  //              always breaks (and terminates) the current tool run.
  // "tool"     → ordinary tool call/result pairs/singles: join the run.
  // "absorbed" → system_note / status_update lifecycle noise: silently join
  //              the active run (rendered as quiet separators when expanded),
  //              or fall through as standalone if no run is active yet.
  function classifyItem(item: Extract<TranscriptItem, { kind: "single" | "pair" }>): "content" | "tool" | "absorbed" {
    if (item.kind === "pair") {
      const { call, result } = item.pair;
      const isSpecial = (e: EventRecord | null) =>
        e !== null && (isTodoListEvent(e) || readToolName(e) === "AskUserQuestion");
      return isSpecial(call) || isSpecial(result) ? "content" : "tool";
    }
    const { event } = item;
    switch (event.kind) {
      case "user_input":
      case "agent_output":
      case "approval_request":
        return "content";
      case "tool_call":
      case "tool_result":
        return isTodoListEvent(event) || readToolName(event) === "AskUserQuestion"
          ? "content"
          : "tool";
      default:
        return isPlanEvent(event) ? "content" : "absorbed";
    }
  }

  const grouped: TranscriptItem[] = [];
  let currentRun: (Extract<TranscriptItem, { kind: "single" | "pair" }>)[] = [];

  for (const item of result) {
    const cls = classifyItem(item);
    if (cls === "tool") {
      currentRun.push(item);
    } else if (cls === "absorbed") {
      if (currentRun.length > 0) {
        currentRun.push(item);
      } else {
        grouped.push(item);
      }
    } else {
      if (currentRun.length >= 1) {
        grouped.push({ kind: "tool_run", items: currentRun });
        currentRun = [];
      }
      grouped.push(item);
    }
  }
  if (currentRun.length >= 1) {
    grouped.push({ kind: "tool_run", items: currentRun });
  }

  return grouped;
}

function mergeToolResultEvent(existing: EventRecord, incoming: EventRecord): EventRecord {
  return {
    ...existing,
    text: mergeEventText(existing, incoming),
    metadata: { ...existing.metadata, ...incoming.metadata },
    ts: incoming.ts,
    sequence: Math.max(existing.sequence, incoming.sequence),
  };
}

function filterOptimisticTranscriptEvents(
  events: EventRecord[],
  optimisticMessages: { text: string }[],
): EventRecord[] {
  const pendingCounts = new Map<string, number>();
  for (const message of optimisticMessages) {
    pendingCounts.set(message.text, (pendingCounts.get(message.text) ?? 0) + 1);
  }
  if (pendingCounts.size === 0) {
    return events;
  }
  const filtered: EventRecord[] = [];
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.kind === "user_input") {
      const pendingCount = pendingCounts.get(event.text) ?? 0;
      if (pendingCount > 0) {
        pendingCounts.set(event.text, pendingCount - 1);
        continue;
      }
    }
    filtered.push(event);
  }
  filtered.reverse();
  return filtered;
}

function isToolResultDelta(event: EventRecord): boolean {
  if (event.kind !== "tool_result") {
    return false;
  }
  const method = event.metadata?.method;
  return method === "item/commandExecution/outputDelta" || method === "item/fileChange/outputDelta";
}

function isTodoListEvent(event: EventRecord): boolean {
  return event.metadata?.item_type === "todo_list" || readToolName(event) === "TodoWrite";
}

function isAgentBusy(
  session: SessionRecord,
  connection: ConnectionState,
): boolean {
  // Trust session.status: the runtime flips it to RUNNING when the user's
  // input lands and to IDLE/ERROR/etc. once the turn ends. The previous
  // implementation walked `events` to corroborate, but events flush via
  // startTransition + rAF, lagging the urgent setSession update — so the
  // walk could see the prior turn's `result` system_note and report idle
  // for the entire window between "user sent" and "agent's first chunk."
  return connection === "open" && session.status === "running";
}

function findPendingApprovals(events: EventRecord[]): EventRecord[] {
  // Track approvals as a queue: every approval_request enqueues, every
  // "Approval response sent" / "Approval timed out" system note dequeues
  // its matching entry (by approval_id, falling back to oldest-first).
  const queue: EventRecord[] = [];
  for (const event of events) {
    if (event.kind === "approval_request") {
      queue.push(event);
    } else if (event.kind === "system_note" && isApprovalResolutionEvent(event)) {
      const approvalId = event.metadata?.approval_id;
      if (typeof approvalId === "string") {
        const index = queue.findIndex((e) => e.metadata?.approval_id === approvalId);
        if (index !== -1) queue.splice(index, 1);
      } else {
        queue.shift();
      }
    }
  }
  return queue;
}

function isApprovalResolutionEvent(event: EventRecord): boolean {
  if (event.metadata?.method === "approval.invalidated") {
    return true;
  }
  return /(Approval response sent|Approval timed out)/i.test(event.text);
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
      if (event.metadata?.method === "approval.invalidated") {
        return true;
      }
      if (isPlanEvent(event)) {
        return true;
      }
      if (typeof event.metadata?.builtin_command === "string") {
        return true;
      }
      return /(approval response|approval timed out|attached|started|terminated|interrupt|resume|failed|error|exited|compact)/i.test(event.text);
    case "raw_terminal_chunk":
      return false;
    default:
      return false;
  }
}

function SessionHeader({
  session,
  connection,
  modelOptions,
  onSetTitle,
}: {
  session: SessionRecord;
  connection: ConnectionState;
  modelOptions: BackendModelOption[];
  onSetTitle?: (title: string) => void | Promise<void>;
}) {
  const cwdSegments = formatCwdSegments(session.cwd);
  const target = session.launch_target_id ?? null;
  const sourceLabel = session.source === "managed" ? "Managed" : "Attached";
  const [isEditing, setIsEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState("");

  function startEditing(event: React.MouseEvent<HTMLButtonElement>) {
    event.preventDefault();
    setIsEditing(true);
    setDraftTitle(session.title);
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      setIsEditing(false);
      setDraftTitle("");
    } else if (event.key === "Enter") {
      commitEditing();
    }
  }

  function commitEditing() {
    setIsEditing(false);
    const newTitle = draftTitle.trim();
    if (newTitle && newTitle !== session.title && onSetTitle) {
      void onSetTitle(newTitle);
    }
  }

  return (
    <header className="session-header">
      <div className="session-header-top">
        <div className="session-header-title-row">
          {isEditing ? (
            <input
              className="inline-title-input"
              type="text"
              value={draftTitle}
              onChange={(e) => setDraftTitle(e.target.value)}
              onKeyDown={handleKeyDown}
              onBlur={commitEditing}
              autoFocus
            />
          ) : (
            <>
              <h2 className="session-header-title">{session.title}</h2>
              {onSetTitle ? (
                <button
                  className="link-button edit-title-btn"
                  type="button"
                  onClick={startEditing}
                  title="Rename session"
                  aria-label="Rename session"
                >
                  ✎
                </button>
              ) : null}
            </>
          )}
        </div>
        <span
          className={`session-pulse ${connectionVariant(connection, session.status)}`}
          title={connectionTitle(connection, session.status)}
        >
          <span className="session-pulse-dot" aria-hidden />
          <span className="session-pulse-label">
            {connectionLabel(connection, session.status)}
          </span>
        </span>
      </div>
      <p className="session-header-cwd" title={session.cwd}>
        {cwdSegments.map((segment, index) => (
          <span key={index}>
            {index > 0 ? <span className="cwd-sep" aria-hidden>/</span> : null}
            <span className={index === cwdSegments.length - 1 ? "cwd-leaf" : "cwd-segment"}>
              {segment}
            </span>
          </span>
        ))}
        {target ? <span className="session-header-target"> · {target}</span> : null}
      </p>
      <div className="session-header-tags">
        <span className={`badge ${session.backend}`}>
          {humaniseBackend(session.backend)}
        </span>
        <span className={`badge transport ${session.transport}`}>
          {transportLabel(session.transport)}
        </span>
        <span className={`badge fidelity ${fidelityFor(session.transport)}`}>
          {fidelityFor(session.transport)}
        </span>
        {session.model ? (
          <span className="badge model" title={`Model: ${session.model}`}>
            {modelOptions.find((opt) => opt.id === session.model)?.label ?? session.model}
          </span>
        ) : null}
        {session.effort ? (
          <span className="badge effort" title={`Effort: ${session.effort}`}>
            {session.effort}
          </span>
        ) : null}
        <span className="session-header-meta">
          {sourceLabel}
          {typeof session.transport_state?.thread_id === "string"
            ? ` · ${session.transport_state.thread_id}`
            : null}
        </span>
      </div>
    </header>
  );
}

function formatCwdSegments(cwd: string): string[] {
  if (!cwd) return [""];
  // Render the path as breadcrumb-style segments, but keep the leading slash
  // (or `~`) attached to the first segment so root paths still read correctly.
  const trimmed = cwd.replace(/\/+$/, "");
  const segments = trimmed.split("/").filter(Boolean);
  if (trimmed.startsWith("/")) {
    return segments.length ? [`/${segments[0]}`, ...segments.slice(1)] : ["/"];
  }
  return segments.length ? segments : [trimmed];
}

function connectionVariant(
  connection: ConnectionState,
  status: SessionRecord["status"],
): string {
  if (connection !== "open") return connection;
  if (status === "running" || status === "waiting_input") return "open running";
  if (status === "error" || status === "interrupted") return "open warn";
  if (status === "exited") return "open dim";
  return "open";
}

function connectionLabel(
  connection: ConnectionState,
  status: SessionRecord["status"],
): string {
  if (connection === "connecting") return "Connecting";
  if (connection === "reconnecting") return "Reconnecting";
  switch (status) {
    case "running":
      return "Running";
    case "waiting_input":
      return "Waiting on you";
    case "interrupted":
      return "Interrupted";
    case "error":
      return "Error";
    case "exited":
      return "Exited";
    case "starting":
      return "Starting";
    case "idle":
    default:
      return "Live";
  }
}

function connectionTitle(
  connection: ConnectionState,
  status: SessionRecord["status"],
): string {
  if (connection !== "open") {
    return `Socket ${connection}`;
  }
  return `Socket open · session ${status.replace("_", " ")}`;
}

function TranscriptEmpty({
  status,
  filterMode,
  hiddenEventCount,
  onShowAll,
}: {
  status: SessionRecord["status"];
  filterMode: FilterMode;
  hiddenEventCount: number;
  onShowAll: () => void;
}) {
  if (filterMode === "important" && hiddenEventCount > 0) {
    return (
      <div className="transcript-empty">
        <p className="transcript-empty-title">Nothing important yet</p>
        <p className="transcript-empty-sub">
          {hiddenEventCount} low-signal event{hiddenEventCount === 1 ? "" : "s"} hidden by the
          Important filter.
        </p>
        <button type="button" className="link-button" onClick={onShowAll}>
          Show all events →
        </button>
      </div>
    );
  }
  if (status === "exited") {
    return (
      <div className="transcript-empty">
        <p className="transcript-empty-title">Session exited</p>
        <p className="transcript-empty-sub">No transcript was captured before this session ended.</p>
      </div>
    );
  }
  return (
    <div className="transcript-empty">
      <span className="transcript-empty-pulse" aria-hidden />
      <p className="transcript-empty-title">Waiting for the agent…</p>
      <p className="transcript-empty-sub">
        Streamed events from the runtime will appear here as soon as they arrive.
      </p>
    </div>
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
