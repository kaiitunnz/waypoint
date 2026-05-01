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
  approveSession,
  connectSessionSocket,
  deleteSession as deleteSessionRequest,
  fetchBackendModels,
  fetchEvents,
  fetchSession,
  fetchTerminalSnapshot,
  isAuthError,
  postAction,
  sendInput,
  setSessionEffort,
  setSessionModel,
  setSessionPermissionMode,
} from "@/lib/api";
import {
  fidelityFor,
  humaniseBackend,
  permissionModesFor,
  supportsResume,
  supportsStructuredApproval,
  transportLabel,
  useBackendCatalog,
} from "@/lib/backends";
import { clearToken } from "@/lib/store";
import { MarkdownMessage } from "@/components/MarkdownMessage";
import {
  AskAnswerEntry,
  CopyMessageButton,
  TranscriptCard,
  ToolPair,
} from "@/components/TranscriptCard";
import {
  BackendModelOption,
  EventRecord,
  SessionEnvelope,
  SessionRecord,
  SessionTransport,
} from "@/lib/types";

const SLASH_COMMANDS: ReadonlyArray<{ command: string; description: string }> = [
  { command: "/help", description: "Forward to the agent's built-in help" },
  { command: "/status", description: "Forward to the agent's status" },
  { command: "/permissions", description: "Forward to the agent's permissions" },
  { command: "/compact", description: "Compact context to reclaim tokens" },
];

const EFFORT_LABEL: Record<string, string> = {
  none: "None",
  minimal: "Minimal",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra high",
  max: "Max",
};

export type ConnectionState = "connecting" | "open" | "reconnecting";

interface SessionDetailProps {
  host: string;
  token: string;
  sessionId: string;
  onAuthFailure?: () => void;
  onConnectionChange?: (state: ConnectionState) => void;
}

type ViewMode = "chat" | "terminal";
type FilterMode = "important" | "all";

// The composer sticks to the viewport bottom; floating scroll affordances
// read `--composer-height` to sit just above it. The fallback keeps things
// sensible for the very first paint before the observer fires.
const COMPOSER_HEIGHT_FALLBACK = 220;
const SHORTCUT_IS_MAC =
  typeof navigator !== "undefined" &&
  /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent || "");

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

export function SessionDetail({
  host,
  token,
  sessionId,
  onAuthFailure,
  onConnectionChange,
}: SessionDetailProps) {
  const router = useRouter();
  const catalog = useBackendCatalog(host || null, token || null, null);
  const [session, setSession] = useState<SessionRecord | null>(null);
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [snapshot, setSnapshot] = useState("");
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const [view, setView] = useState<ViewMode>("chat");
  const [filterMode, setFilterMode] = useState<FilterMode>("important");
  const [error, setError] = useState("");
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const [showScrollToTop, setShowScrollToTop] = useState(false);
  const [modeBusy, setModeBusy] = useState(false);
  const [modelOptions, setModelOptions] = useState<BackendModelOption[]>([]);
  const [modelBusy, setModelBusy] = useState(false);
  const [effortBusy, setEffortBusy] = useState(false);
  const [hasOlderEvents, setHasOlderEvents] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  // Tracks the smallest raw sequence ever received from the server. Distinct
  // from `events[0].sequence` because `mergeEvents` advances a coalesced
  // item's sequence to the *last* delta — using that as a cursor would
  // re-fetch every earlier delta of the same logical message. We compute
  // this from the raw payload before coalescing.
  const [oldestRawSequence, setOldestRawSequence] = useState<number | null>(null);
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

  useEffect(() => {
    onConnectionChange?.(connection);
  }, [connection, onConnectionChange]);

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
  useEffect(() => {
    if (!session) {
      return;
    }
    let cancelled = false;
    fetchBackendModels(host, token, session.backend, {
      launchTargetId: session.launch_target_id,
    })
      .then((response) => {
        if (cancelled) return;
        setModelOptions(response.models);
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
      });
    return () => {
      cancelled = true;
    };
  }, [host, token, session?.backend, session?.launch_target_id, handleAuthFailure, session]);

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

  const refresh = useCallback(async () => {
    if (refreshing) {
      return;
    }
    setRefreshing(true);
    setError("");
    // Drop any queued WS events so they don't clobber the freshly-fetched
    // page on the next animation frame; subsequent live events still merge
    // into the new array via the standard event handler.
    pendingEventsRef.current = [];
    if (flushFrameRef.current !== null) {
      window.cancelAnimationFrame(flushFrameRef.current);
      flushFrameRef.current = null;
    }
    try {
      const [loadedSession, loadedPage, loadedSnapshot] = await Promise.all([
        fetchSession(host, token, sessionId),
        fetchEvents(host, token, sessionId),
        fetchTerminalSnapshot(host, token, sessionId),
      ]);
      setSession(loadedSession);
      const sanitized = loadedPage.events.map(sanitizeEvent);
      const coalesced = sanitized.reduce<EventRecord[]>(
        (acc, event) => mergeEvents(acc, event),
        [],
      );
      setEvents(coalesced);
      setHasOlderEvents(loadedPage.has_more);
      setOldestRawSequence(minRawSequence(sanitized));
      setSnapshot(stripAnsi(loadedSnapshot));
    } catch (refreshError) {
      if (isAuthError(refreshError)) {
        handleAuthFailure();
        return;
      }
      setError(
        refreshError instanceof Error ? refreshError.message : "failed to refresh session",
      );
    } finally {
      setRefreshing(false);
    }
  }, [handleAuthFailure, host, refreshing, sessionId, token]);

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

  const submitInput = useCallback(async (text: string) => {
    if (!text.trim()) {
      return false;
    }
    try {
      await sendInput(host, token, sessionId, text);
      return true;
    } catch (sendError) {
      if (isAuthError(sendError)) {
        handleAuthFailure();
        return false;
      }
      setError(sendError instanceof Error ? sendError.message : "failed to send input");
      return false;
    }
  }, [handleAuthFailure, host, token, sessionId]);

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
  const agentBusy = session ? isAgentBusy(session, connection) : false;
  const visibleEvents = filterMode === "all" ? renderedEvents : renderedEvents.filter(isImportantEvent);
  const hiddenEventCount = renderedEvents.length - visibleEvents.length;
  const transcriptItems = buildTranscriptItems(visibleEvents);
  const usageSummary = extractUsageSummary(events);
  // Session has stopped its backend process (clean shutdown or crash).
  const sessionExited = Boolean(
    session && (session.status === "exited" || session.status === "error"),
  );
  // Only structured transports can be brought back via the plugin's
  // restore_session path. Tmux has no resume contract, so the backend
  // hard-fails reattach there and the composer must follow suit.
  const reattachable = Boolean(
    session && fidelityFor(session.transport) === "structured",
  );
  const dormantReattach = sessionExited && reattachable;
  // Block submission until the session record resolves — the parent renders
  // ReplyComposer eagerly to keep layout stable, so without this guard a fast
  // typist could fire requests against an unresolved session view.
  const composerDisabled = !session || (sessionExited && !reattachable);
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
  const interruptSession = useCallback(() => {
    void runAction("interrupt");
  }, [runAction]);
  const resumeSession = useCallback(() => {
    void runAction("resume");
  }, [runAction]);

  return (
    <section className="stack" ref={sectionRef}>
      {view === "chat" && showScrollToTop ? (
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
        <SessionHeader session={session} connection={connection} />
      ) : null}
      {usageSummary ? <UsageCard summary={usageSummary} /> : null}
      <div className="session-toolbar">
        <div className="segmented" role="tablist" aria-label="View">
          <button
            type="button"
            role="tab"
            aria-selected={view === "chat"}
            className={`segmented-item ${view === "chat" ? "active" : ""}`}
            onClick={() => setView("chat")}
          >
            Chat
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === "terminal"}
            className={`segmented-item ${view === "terminal" ? "active" : ""}`}
            onClick={() => setView("terminal")}
          >
            Terminal
          </button>
        </div>
        {view === "chat" ? (
          <div className="segmented segmented-quiet" role="radiogroup" aria-label="Event filter">
            <button
              type="button"
              role="radio"
              aria-checked={filterMode === "important"}
              className={`segmented-item ${filterMode === "important" ? "active" : ""}`}
              onClick={() => setFilterMode("important")}
            >
              Important
            </button>
            <button
              type="button"
              role="radio"
              aria-checked={filterMode === "all"}
              className={`segmented-item ${filterMode === "all" ? "active" : ""}`}
              onClick={() => setFilterMode("all")}
            >
              All events
            </button>
          </div>
        ) : null}
      </div>
      {view === "chat" ? (
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
            ? transcriptItems.map((item) =>
                item.kind === "pair" ? (
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
                ),
              )
            : session
              ? (
                <TranscriptEmpty
                  status={session.status}
                  filterMode={filterMode}
                  hiddenEventCount={hiddenEventCount}
                  onShowAll={() => setFilterMode("all")}
                />
              )
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
      {pendingApproval ? (
        <ApprovalCard event={pendingApproval} onDecide={submitApproval} />
      ) : null}
      {view === "chat" && showScrollToBottom ? (
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
      <ReplyComposer
        backend={session?.backend ?? null}
        canDelete={sessionExited}
        canResume={canResume}
        canTerminate={Boolean(session && !sessionExited)}
        disabled={composerDisabled}
        dormant={dormantReattach}
        placeholder={composerPlaceholder}
        agentBusy={agentBusy}
        modeBusy={modeBusy}
        modelBusy={modelBusy}
        modelOptions={modelOptions}
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
        refreshBusy={refreshing}
        onDelete={removeFromList}
        onInterrupt={interruptSession}
        onModeChange={handlePermissionModeChange}
        onModelChange={handleModelChange}
        onEffortChange={handleEffortChange}
        onRefresh={refresh}
        onResume={resumeSession}
        onSend={submitInput}
        onTerminate={terminate}
      />
    </section>
  );
}

interface ReplyComposerProps {
  agentBusy: boolean;
  backend: SessionRecord["backend"] | null;
  canDelete: boolean;
  canResume: boolean;
  canTerminate: boolean;
  disabled: boolean;
  dormant: boolean;
  placeholder: string;
  modeBusy: boolean;
  modelBusy: boolean;
  modelOptions: BackendModelOption[];
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
  refreshBusy: boolean;
  onDelete: () => void | Promise<void>;
  onInterrupt: () => void | Promise<void>;
  onModeChange: (mode: string) => void | Promise<void>;
  onModelChange: (model: string) => void | Promise<void>;
  onEffortChange: (effort: string) => void | Promise<void>;
  onRefresh: () => void | Promise<void>;
  onResume: () => void | Promise<void>;
  onSend: (text: string) => Promise<boolean>;
  onTerminate: () => void | Promise<void>;
}

const ReplyComposer = memo(function ReplyComposer({
  agentBusy,
  backend,
  canDelete,
  canResume,
  canTerminate,
  disabled,
  dormant,
  placeholder,
  modeBusy,
  modelBusy,
  modelOptions,
  currentModel,
  currentEffort,
  effortBusy,
  permissionMode,
  transport,
  effortRequiresConfirm,
  refreshBusy,
  onDelete,
  onInterrupt,
  onModeChange,
  onModelChange,
  onEffortChange,
  onRefresh,
  onResume,
  onSend,
  onTerminate,
}: ReplyComposerProps) {
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [suggestionIndex, setSuggestionIndex] = useState(0);
  const [suggestionsDismissed, setSuggestionsDismissed] = useState(false);
  const [overflowOpen, setOverflowOpen] = useState(false);
  const [tuneOpen, setTuneOpen] = useState(false);
  // Pending effort for backends that need a session restart to apply (Claude)
  // — staged here until the user confirms via the Apply button. `null` means
  // no pending change.
  const [pendingEffort, setPendingEffort] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const composerRef = useRef<HTMLElement | null>(null);
  const overflowRef = useRef<HTMLDivElement | null>(null);
  const tuneRef = useRef<HTMLDivElement | null>(null);

  // Built-in slash commands are intercepted on the backend only for
  // structured transports (see plugin.maybe_handle_input); skip
  // suggestions on tmux. While the session is still loading
  // (transport=null), default to off.
  const supportsSlash =
    transport !== null && fidelityFor(transport) === "structured";

  const suggestions = supportsSlash && !suggestionsDismissed
    ? SLASH_COMMANDS.filter((entry) => {
        const head = draft.split(/\s/, 1)[0];
        return head.startsWith("/") && entry.command.startsWith(head);
      })
    : [];
  const suggestionsOpen = suggestions.length > 0 && /^\S+$/.test(draft);
  const activeIndex = Math.min(suggestionIndex, Math.max(0, suggestions.length - 1));

  useEffect(() => {
    setSuggestionIndex(0);
  }, [draft]);

  useEffect(() => {
    if (!draft.startsWith("/")) {
      setSuggestionsDismissed(false);
    }
  }, [draft]);

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

  function applySuggestion(index: number) {
    const chosen = suggestions[index];
    if (!chosen) {
      return;
    }
    setDraft(chosen.command + " ");
    setSuggestionsDismissed(true);
    requestAnimationFrame(() => textareaRef.current?.focus());
  }

  async function handleSend() {
    if (!draft.trim()) {
      return;
    }
    setSending(true);
    try {
      const sent = await onSend(draft);
      if (sent) {
        setDraft("");
        setSuggestionsDismissed(false);
      }
    } finally {
      setSending(false);
    }
  }

  function handleDraftKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.nativeEvent.isComposing) {
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

  const modeOptions = backend ? permissionModesFor(backend) : [];
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
  // union of every model's supported levels.
  const matchingModelEntry = currentModel
    ? modelOptions.find((opt) => opt.id === currentModel)
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
      parts.push(matched?.label ?? (currentModel || "Default"));
    }
    if (hasEffortPicker) {
      parts.push(currentEffort ? EFFORT_LABEL[currentEffort] ?? currentEffort : "Default");
    }
    return parts.join(" · ") || "Settings";
  })();

  return (
    <section className="composer" ref={composerRef}>
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
                      <option value="">Default</option>
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
                      <option value="">Default</option>
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
        <span className="composer-shortcut" aria-hidden>
          <kbd>{shortcutKey}</kbd>
          <span>+</span>
          <kbd>↵</kbd>
          <span>to send</span>
        </span>
      </div>
      <div className="reply-textarea-wrap">
        <textarea
          ref={textareaRef}
          className="composer-textarea"
          rows={3}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleDraftKeyDown}
          disabled={disabled}
          placeholder={placeholder}
          aria-label="Reply"
        />
        {suggestionsOpen ? (
          <ul className="slash-suggestions" role="listbox">
            {suggestions.map((entry, index) => (
              <li key={entry.command}>
                <button
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
                  <span className="slash-name">{entry.command}</span>
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
                  disabled={refreshBusy}
                  onClick={() => {
                    setOverflowOpen(false);
                    void onRefresh();
                  }}
                >
                  <span className="glyph">↻</span>
                  {refreshBusy ? "Refreshing…" : "Refresh"}
                </button>
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
  const meta = event.metadata;
  if (typeof meta?.item_id === "string" && meta.item_id) {
    return meta.item_id;
  }
  return null;
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

type TranscriptItem =
  | { kind: "single"; event: EventRecord }
  | { kind: "pair"; event: EventRecord; pair: ToolPair };

function buildTranscriptItems(events: EventRecord[]): TranscriptItem[] {
  const result: TranscriptItem[] = [];
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
      item.pair.result = event;
    }
    item.pair.ts = event.ts;
    item.pair.sequence = Math.max(item.pair.sequence, event.sequence);
  }
  return result;
}

function isToolResultDelta(event: EventRecord): boolean {
  if (event.kind !== "tool_result") {
    return false;
  }
  const method = event.metadata?.method;
  return method === "item/commandExecution/outputDelta" || method === "item/fileChange/outputDelta";
}

function isTodoListEvent(event: EventRecord): boolean {
  return event.metadata?.item_type === "todo_list";
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

function findPendingApproval(events: EventRecord[]): EventRecord | null {
  // Track approvals as a queue: every approval_request enqueues, every
  // "Approval response sent" system note dequeues the oldest. The Claude
  // adapter resolves pending futures in the same order (`next(iter(state.pending))`),
  // so the UI must show the same head-of-queue entry. The previous
  // walk-from-newest-and-bail-on-system-note logic hid the card whenever
  // Claude fired multiple gated tools in parallel — the user accepted the
  // first one and the rest stayed silently pending.
  const queue: EventRecord[] = [];
  for (const event of events) {
    if (event.kind === "approval_request") {
      queue.push(event);
    } else if (
      event.kind === "system_note" &&
      /Approval response sent/i.test(event.text)
    ) {
      queue.shift();
    }
  }
  return queue[0] ?? null;
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
      if (typeof event.metadata?.builtin_command === "string") {
        return true;
      }
      return /(approval response|attached|started|terminated|interrupt|resume|failed|error|exited|compact)/i.test(event.text);
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

interface UsageSummary {
  lastTurn: {
    outputTokens: number | null;
    totalCostUsd: number | null;
    permissionDenials: number;
    ts: string;
  } | null;
  rateLimit: {
    status: string | null;
    type: string | null;
    ts: string;
  } | null;
}

function SessionHeader({
  session,
  connection,
}: {
  session: SessionRecord;
  connection: ConnectionState;
}) {
  const cwdSegments = formatCwdSegments(session.cwd);
  const target = session.launch_target_id ?? null;
  const sourceLabel = session.source === "managed" ? "Managed" : "Attached";
  return (
    <header className="session-header">
      <div className="session-header-top">
        <h2 className="session-header-title">{session.title}</h2>
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
            {session.model}
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

function UsageCard({ summary }: { summary: UsageSummary }) {
  return (
    <section className="panel usage-card">
      <div className="session-row">
        <span className="badge fidelity structured">usage</span>
        <span className="muted">Latest structured telemetry</span>
      </div>
      <div className="usage-grid">
        <div>
          <p className="meta">Last turn</p>
          {summary.lastTurn ? (
            <p className="muted">
              {summary.lastTurn.totalCostUsd !== null ? `Cost ${formatUsd(summary.lastTurn.totalCostUsd)} · ` : ""}
              {summary.lastTurn.outputTokens !== null
                ? `${summary.lastTurn.outputTokens.toLocaleString()} output tokens`
                : "No token count"}
              {summary.lastTurn.permissionDenials > 0
                ? ` · ${summary.lastTurn.permissionDenials} denial${summary.lastTurn.permissionDenials === 1 ? "" : "s"}`
                : ""}
            </p>
          ) : (
            <p className="muted">No turn usage yet.</p>
          )}
        </div>
        <div>
          <p className="meta">Rate limits</p>
          {summary.rateLimit ? (
            <p className="muted">
              {summary.rateLimit.status ?? "unknown"}
              {summary.rateLimit.type ? ` · ${summary.rateLimit.type}` : ""}
            </p>
          ) : (
            <p className="muted">No rate-limit event yet.</p>
          )}
        </div>
      </div>
    </section>
  );
}

function ApprovalCard({ event, onDecide }: ApprovalCardProps) {
  const toolName =
    typeof event.metadata.tool_name === "string"
      ? (event.metadata.tool_name as string)
      : null;
  const toolInput =
    event.metadata.tool_input && typeof event.metadata.tool_input === "object"
      ? (event.metadata.tool_input as Record<string, unknown>)
      : null;
  const copyText = approvalCopyText(event.text, toolName, toolInput);
  return (
    <section className="panel approval">
      <div className="session-row">
        <span className="badge fidelity structured">approval</span>
        <CopyMessageButton text={copyText} label="Copy approval body" />
      </div>
      <ApprovalCardBody
        eventText={event.text}
        toolName={toolName}
        toolInput={toolInput}
      />
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

function approvalCopyText(
  eventText: string,
  toolName: string | null,
  toolInput: Record<string, unknown> | null,
): string {
  if (toolName === "ExitPlanMode" && typeof toolInput?.plan === "string") {
    return toolInput.plan as string;
  }
  if (
    (toolName === "Task" || toolName === "Agent") &&
    typeof toolInput?.prompt === "string"
  ) {
    return toolInput.prompt as string;
  }
  if (toolName === "Bash" && typeof toolInput?.command === "string") {
    return toolInput.command as string;
  }
  return eventText;
}

function ApprovalCardBody({
  eventText,
  toolName,
  toolInput,
}: {
  eventText: string;
  toolName: string | null;
  toolInput: Record<string, unknown> | null;
}) {
  if (toolName === "ExitPlanMode" && typeof toolInput?.plan === "string") {
    return (
      <>
        <p className="approval-prompt">Approve plan and exit plan mode</p>
        <div className="approval-plan">
          <MarkdownMessage text={toolInput.plan as string} />
        </div>
      </>
    );
  }
  if (
    (toolName === "Task" || toolName === "Agent") &&
    toolInput &&
    typeof toolInput.prompt === "string"
  ) {
    const description =
      typeof toolInput.description === "string" ? (toolInput.description as string) : "";
    const subagent =
      typeof toolInput.subagent_type === "string"
        ? (toolInput.subagent_type as string)
        : "";
    return (
      <>
        <p className="approval-prompt">
          Approve subagent task
          {description ? `: ${description}` : ""}
          {subagent ? ` (via ${subagent})` : ""}
        </p>
        <div className="approval-plan">
          <MarkdownMessage text={toolInput.prompt as string} />
        </div>
      </>
    );
  }
  if (toolName === "Bash" && typeof toolInput?.command === "string") {
    const desc =
      typeof toolInput.description === "string"
        ? (toolInput.description as string)
        : "";
    return (
      <>
        <p className="approval-prompt">
          Approve Bash command{desc ? `: ${desc}` : ""}
        </p>
        <pre className="approval-shell">{toolInput.command as string}</pre>
      </>
    );
  }
  return <pre>{eventText}</pre>;
}

function sanitizeEvent(event: EventRecord): EventRecord {
  return {
    ...event,
    text: stripAnsi(event.text),
  };
}

function extractUsageSummary(events: EventRecord[]): UsageSummary | null {
  let lastTurn: UsageSummary["lastTurn"] = null;
  let rateLimit: UsageSummary["rateLimit"] = null;
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    const metadata = asRecord(event.metadata);
    if (!lastTurn && metadata?.method === "result") {
      const payload = asRecord(metadata.payload);
      const usage = asRecord(payload?.usage);
      const permissionDenials = Array.isArray(payload?.permission_denials) ? payload.permission_denials.length : 0;
      lastTurn = {
        outputTokens: typeof usage?.output_tokens === "number" ? usage.output_tokens : null,
        totalCostUsd: typeof payload?.total_cost_usd === "number" ? payload.total_cost_usd : null,
        permissionDenials,
        ts: event.ts,
      };
    }
    if (!rateLimit && metadata?.method === "rate_limit_event") {
      const payload = asRecord(metadata.payload);
      const info = asRecord(payload?.rate_limit_info);
      rateLimit = {
        status: typeof info?.status === "string" ? info.status : null,
        type: typeof info?.rate_limit_type === "string" ? info.rate_limit_type : null,
        ts: event.ts,
      };
    }
    if (lastTurn && rateLimit) {
      break;
    }
  }
  if (!lastTurn && !rateLimit) {
    return null;
  }
  return { lastTurn, rateLimit };
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function formatUsd(value: number): string {
  return `$${value.toFixed(4)}`;
}

function stripAnsi(text: string): string {
  return text
    .replace(/\u001B\][\s\S]*?(?:\u0007|\u001B\\)/g, "")
    .replace(/\u001B\[[0-?]*[ -/]*[@-~]/g, "")
    .replace(/\u001B[@-_]/g, "");
}
