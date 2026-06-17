"use client";

import { useRouter } from "next/navigation";
import {
  ChangeEvent,
  ClipboardEvent,
  DragEvent,
  KeyboardEvent,
  memo,
  startTransition,
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
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
  forkSession,
  isAuthError,
  postAction,
  refreshSessionRateLimitUsage,
  sendInput,
  setSessionEffort,
  setSessionModel,
  setSessionPermissionMode,
  setSessionPinned,
  setSessionTitle,
} from "@/lib/api";
import {
  agentTransports,
  approvalDecisionsFor,
  type BackendCatalog,
  defaultTransportFor,
  displayAgentFor,
  fidelityFor,
  hasTerminalPane,
  humaniseBackend,
  launchableAgents,
  liveTerminal,
  permissionModesFor,
  supportsApprovalNote,
  supportsAttachments,
  supportsFork,
  supportsPlanApproval,
  supportsReattachAfterExit,
  supportsStructuredApproval,
  terminalInteractive,
  terminalResizable,
  transportLabel,
  transportPresentation,
  useBackendCatalog,
} from "@/lib/backends";
import { clearToken } from "@/lib/store";
import { type TerminalSubmitResult } from "@/lib/composer";
import { useCommandCompletions } from "@/lib/composer-completions";
import { useFileMentions } from "@/lib/use-file-mentions";
import {
  isPlanEvent,
  itemIdForEvent,
  planForEvent,
  type PlanDecision,
  type PlanViewModel,
} from "@/lib/events";
import { useTheme } from "@/lib/theme";
import { formatRelativeTime } from "@/lib/usage";
import {
  AttachmentContextProvider,
  AttachmentTray,
  filesFromDataTransfer,
  PaperclipIcon,
  useAttachments,
} from "@/components/AttachmentTray";
import { SessionFilesPanel } from "@/components/SessionFilesPanel";
import { SessionTerminalView } from "@/components/SessionTerminalView";
import { SessionUsagePill } from "@/components/SessionUsagePill";
import { CommandSuggestions } from "@/components/CommandSuggestions";
import { FileMentions } from "@/components/FileMentions";
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
import { TaskProgressDock } from "@/components/TaskProgressDock";
import { readTodoEntries, summarizeTodos } from "@/lib/todos";
import { useSwitcher } from "@/components/SwitcherProvider";
import {
  Backend,
  BackendDescriptor,
  BackendModelOption,
  BackendPermissionMode,
  EventRecord,
  SessionCommandInvocation,
  SessionEnvelope,
  SessionRecord,
  SessionTransport,
} from "@/lib/types";

// iOS Safari rejects ``navigator.clipboard.writeText`` even from inside a
// click handler — the Promise microtask ends the transient activation
// window before the write resolves. ``document.execCommand("copy")`` is
// deprecated everywhere but remains the one synchronous clipboard write
// that survives that gauntlet across iOS Safari, desktop Safari, and
// modern Chromium.
//
// A contenteditable ``<div>`` is the right host: textarea's ``.value``
// lives in an internal buffer (not DOM children), so
// ``Range.selectNodeContents(textarea)`` selects nothing — execCommand
// then reports success against an empty selection and the paste is
// blank. The div holds the text in real text nodes; ``white-space: pre``
// preserves newlines so multi-line ``/copy`` results survive intact.
function copyTextSync(text: string): boolean {
  if (typeof document === "undefined") return false;
  const host = document.createElement("div");
  host.textContent = text;
  host.setAttribute("contenteditable", "true");
  // ``left: -9999px`` keeps the node selectable but visually off-canvas
  // (opacity 0 in the same coordinate space can still flash a focus
  // ring on iOS). ``font-size: 16px`` prevents iOS's auto-zoom on
  // focused editable fields. ``white-space: pre`` carries newlines.
  host.style.cssText = [
    "position:fixed",
    "top:0",
    "left:-9999px",
    "width:1px",
    "height:1px",
    "white-space:pre",
    "font-size:16px",
    "user-select:text",
    "-webkit-user-select:text",
  ].join(";");
  document.body.appendChild(host);
  let ok = false;
  const sel = window.getSelection();
  try {
    const range = document.createRange();
    range.selectNodeContents(host);
    if (sel) {
      sel.removeAllRanges();
      sel.addRange(range);
    }
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  } finally {
    sel?.removeAllRanges();
    document.body.removeChild(host);
  }
  return ok;
}

// The session id is otherwise only readable from the URL — invisible in the
// installed PWA where the address bar is hidden. This puts a quiet,
// click-to-copy copy of it in the header meta row for the human operator (who
// occasionally needs the raw id for a `waypoint sessions …` call or to hand to
// an agent). Copies the full id even though the display truncates it.
function SessionIdCopy({ id }: { id: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className={`session-id-copy${copied ? " copied" : ""}`}
      title={copied ? "Copied" : `Copy session id: ${id}`}
      aria-label={copied ? "Session id copied" : `Copy session id ${id}`}
      onClick={(event) => {
        event.stopPropagation();
        if (copyTextSync(id)) {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        }
      }}
    >
      <span className="session-id-copy-key">id</span>
      <span className="session-id-copy-value">{id}</span>
      <span aria-hidden>{copied ? "✓" : "⎘"}</span>
    </button>
  );
}

// WebKit (desktop Safari + every iOS browser, since the App Store
// requires WebKit) gates ``navigator.clipboard.readText()`` behind a
// system "Paste" banner that the user must click to authorize. Other
// browsers either cache the permission (Chromium) or expose it via a
// URL-bar affordance (Firefox), so the one-tap path Just Works there.
// We use this flag to decide whether to short-circuit straight to
// sending or to stage the text behind a tap-to-confirm pill.
function isSafariFamily(): boolean {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent;
  const iOS =
    /\b(iPad|iPhone|iPod)\b/.test(ua) ||
    (ua.includes("Mac") && (navigator.maxTouchPoints ?? 0) > 1);
  if (iOS) return true;
  return /Safari\//.test(ua) && !/Chrome|Chromium|Edg\/|OPR\//.test(ua);
}

// Bracketed-paste wraps the payload so TUIs that have enabled mode
// 2004 treat the bytes as a single paste event rather than a stream of
// keystrokes. Normalize ``\r\n`` and lone ``\r`` to ``\n`` first so a
// stray CR can't fire a mid-paste submit on the way in. Wrapped CLIs
// (CC, Codex, OpenCode) all enable mode 2004; raw shells will show the
// literal escape codes, which is the documented trade-off.
function wrapBracketedPaste(text: string): string {
  return `\x1b[200~${text.replace(/\r\n?/g, "\n")}\x1b[201~`;
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


// An existing backend-native thread the assistant can adopt.
export interface AssistantThreadOption {
  id: string;
  title: string;
  // ISO timestamp of last activity — the assistant's own threads share the
  // "Personal Assistant" title, so the picker leans on this to tell them apart.
  updatedAt: string;
  // First-message snippet when the backend provides one; another distinguisher.
  preview: string | null;
}

// Assistant-only lifecycle actions surfaced inside the composer settings
// popover (backend switch + attach existing thread + clear context +
// terminate/reattach). The owning page wires these to /api/assistant/* and
// refreshes itself afterwards; when absent, the composer renders no assistant
// controls.
export interface AssistantControls {
  backends: BackendDescriptor[];
  supportsReattach: boolean;
  // Rebuild the assistant on the chosen agent and transport. Changing either
  // starts a fresh thread, since the transport is fixed at launch.
  onSwitchBackend: (
    backend: Backend,
    transport: SessionTransport,
  ) => Promise<void> | void;
  onAttachThread: (backend: Backend, threadId: string) => Promise<void> | void;
  onClearContext: () => Promise<void> | void;
  onTerminate: () => Promise<void> | void;
  onReattach: () => Promise<void> | void;
  // Lists importable threads for a backend (empty when discovery unsupported or
  // it fails); used to populate the "resume an existing thread" picker.
  listThreads: (backend: Backend) => Promise<AssistantThreadOption[]>;
}

interface SessionDetailProps {
  host: string;
  token: string;
  sessionId: string;
  onAuthFailure?: () => void;
  // Renders the persistent personal-assistant variant: suppresses task-session
  // chrome (cwd, transport/source badges, rename) and destructive/branching
  // actions (terminate, delete, /new, /fork), and shows a capability-led empty
  // state instead of "Waiting for the agent…".
  assistant?: boolean;
  // Assistant lifecycle handlers; only meaningful when `assistant` is set.
  assistantControls?: AssistantControls | null;
}

type ViewMode = "chat" | "terminal";
type FilterMode = "important" | "all";
type ConnectionState = "connecting" | "open" | "reconnecting";

// The composer sticks to the viewport bottom; floating scroll affordances
// read `--composer-height` to sit just above it. The fallback keeps things
// sensible for the very first paint before the observer fires.
const COMPOSER_HEIGHT_FALLBACK = 220;

// Per-session dismissal of the task progress dock, persisted so a refresh
// keeps it dismissed. We store the dismissed todo-event sequence: any later
// task update emits a higher-sequence event and re-shows the dock, while a
// bare refresh (no new event) keeps the same sequence and stays dismissed.
// sessionStorage (not localStorage) scopes it to the tab and self-cleans.
const TASK_DOCK_DISMISSED_STORAGE_PREFIX = "waypoint-task-dock-dismissed:";

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

export function SessionDetail({ host, token, sessionId, onAuthFailure, assistant = false, assistantControls = null }: SessionDetailProps) {
  const router = useRouter();
  const catalog = useBackendCatalog(host || null, token || null, null);
  const attachmentContext = useMemo(
    () => ({ host, token, sessionId }),
    [host, token, sessionId],
  );
  const [session, setSession] = useState<SessionRecord | null>(null);
  const [events, setEvents] = useState<EventRecord[]>([]);
  // The todo-event sequence the user last dismissed from the progress dock.
  // `undefined` means we haven't read the persisted value yet — the dock stays
  // hidden until then so a dismissed dock doesn't flash on load (and to avoid
  // an SSR hydration mismatch from reading sessionStorage during render).
  const [dismissedTaskSequence, setDismissedTaskSequence] = useState<
    number | null | undefined
  >(undefined);
  // Read the persisted dismissal after mount (never during render) so SSR and
  // the first client render agree. Re-reads when the session changes, since
  // the route can reuse this component across `/session/[id]` navigations.
  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    try {
      const raw = window.sessionStorage.getItem(
        `${TASK_DOCK_DISMISSED_STORAGE_PREFIX}${sessionId}`,
      );
      const parsed = raw !== null ? Number(raw) : NaN;
      setDismissedTaskSequence(Number.isFinite(parsed) ? parsed : null);
    } catch {
      setDismissedTaskSequence(null);
    }
  }, [sessionId]);
  const [view, setView] = useState<ViewMode>("chat");
  const [filterMode, setFilterMode] = useState<FilterMode>("important");
  const [toolRunsExpanded, setToolRunsExpanded] = useState(false);
  const [error, setError] = useState("");
  // Safari rejects ``navigator.clipboard.writeText`` outside a synchronous
  // user-gesture handler, and the ``/copy`` clipboard payload arrives via
  // a WS message (no surviving gesture). When the async write fails we
  // stash the text and surface a tap-to-copy pill so the user's tap
  // provides a fresh gesture; the retry uses ``document.execCommand`` so
  // the write stays synchronous (no Promise microtask consuming the
  // activation, which iOS Safari requires).
  const [pendingClipboard, setPendingClipboard] = useState<string | null>(null);
  const [clipboardCopied, setClipboardCopied] = useState(false);
  // Bumped on every clipboard_copy envelope and on each successful copy
  // so React remounts the pill — pure CSS ``animation`` only plays once
  // per element, so consecutive /copies on the same DOM node would
  // otherwise silently swap text with no visible motion.
  const [clipboardSeq, setClipboardSeq] = useState(0);
  // Paste-side mirror of the copy flow. On WebKit the readText prompt
  // is a system-level "Paste" banner the user must approve, so even
  // after we have the bytes we stage them behind a second tap-to-send
  // pill that previews the char count. On Chromium/Firefox we skip
  // the pill and send straight through.
  const [pendingPaste, setPendingPaste] = useState<string | null>(null);
  const [pasteSeq, setPasteSeq] = useState(0);
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
  // Auto-dismiss the "Copied!" pill after a beat so it doesn't squat on
  // the toast slot. The fallback "Tap to copy" toast stays until the user
  // acts on it — clipboard access can't be reattempted programmatically
  // and there's no point auto-hiding the only remaining affordance.
  useEffect(() => {
    if (!clipboardCopied) return;
    const t = window.setTimeout(() => setClipboardCopied(false), 1800);
    return () => window.clearTimeout(t);
  }, [clipboardCopied]);
  const copyPendingClipboard = useCallback(() => {
    if (pendingClipboard === null) return;
    if (copyTextSync(pendingClipboard)) {
      setClipboardCopied(true);
      setPendingClipboard(null);
      setClipboardSeq((s) => s + 1);
    }
    // Sync path failed — the async API rarely fares better from here on
    // iOS Safari, but try anyway as a courtesy. Leave the pill up either
    // way so the user knows the copy didn't land.
    else {
      // Second ``?.`` is required: when ``navigator.clipboard`` is
      // undefined the optional chain short-circuits to ``undefined`` and
      // ``.then`` on that would throw uncaught.
      navigator.clipboard?.writeText(pendingClipboard)?.then(
        () => {
          setClipboardCopied(true);
          setPendingClipboard(null);
          setClipboardSeq((s) => s + 1);
        },
        () => {},
      );
    }
  }, [pendingClipboard]);
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

  // Backends that apply settings by relaunching the process (claude_tty)
  // interrupt the live turn, so confirm before changing one mid-turn.
  const confirmTurnInterrupt = useCallback(
    (field: string): boolean => {
      if (!session) return false;
      const interrupts = catalog.byId(session.backend)?.capabilities
        .settings_change_interrupts_turn;
      const running =
        session.status === "running" || session.status === "waiting_input";
      if (!interrupts || !running) return true;
      return window.confirm(
        `Changing the ${field} restarts this session and interrupts the current turn. Continue?`,
      );
    },
    [session, catalog],
  );

  const handlePermissionModeChange = useCallback(
    async (nextMode: string) => {
      if (!session || nextMode === (session.permission_mode ?? "default")) {
        return;
      }
      if (!confirmTurnInterrupt("permission mode")) {
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
    [host, token, session, handleAuthFailure, confirmTurnInterrupt],
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
      if (!confirmTurnInterrupt("model")) {
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
    [host, token, session, handleAuthFailure, confirmTurnInterrupt],
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
      if (!confirmTurnInterrupt("effort")) {
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
    [host, token, session, handleAuthFailure, confirmTurnInterrupt],
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
        const [loadedSession, loadedPage] = await Promise.all([
          fetchSession(host, token, sessionId),
          fetchEvents(host, token, sessionId),
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
          if (message.type === "clipboard_copy") {
            // Claude's /copy server-side interceptor (structured CC) and
            // the tmux WS handler's OSC 52 extractor (tmux-wrapped CC)
            // both publish through this envelope. Chromium accepts the
            // async writeText; Safari rejects it because the keystroke's
            // user activation has expired by the time the WS frame lands.
            // On rejection we surface a tap-to-copy pill — the user's
            // tap provides a fresh gesture for the synchronous retry.
            const text = (message.payload as { text?: string }).text;
            if (typeof text === "string" && text.length > 0) {
              // New envelope replaces any in-flight confirm pill and
              // bumps the key so the entry animation replays even when
              // the previous /copy hasn't fully faded.
              setClipboardCopied(false);
              setClipboardSeq((s) => s + 1);
              const writer = navigator.clipboard?.writeText(text);
              if (writer) {
                writer.then(
                  () => {
                    setClipboardCopied(true);
                    setPendingClipboard(null);
                    setClipboardSeq((s) => s + 1);
                  },
                  () => setPendingClipboard(text),
                );
              } else {
                setPendingClipboard(text);
              }
            }
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
    if (!session) return;
    const prev = document.title;
    document.title = `${humaniseBackend(session.backend, catalog)} · ${session.title}`;
    return () => { document.title = prev; };
  }, [session, catalog]);

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

  // Waypoint-level slash commands (``/new``, ``/fork``) are intercepted on
  // the frontend rather than forwarded to the backend — they're the only
  // ``frontend_control`` completions in the system. Both composers route
  // through here so terminal sessions get the same behaviour as chat ones.
  // Returns ``"handled"`` / ``"error"`` when the text was a control command,
  // or ``null`` when the caller should fall through to its normal send path.
  // ``allowFork`` reflects the agent's fork capability (and the launchpad guard).
  const runFrontendControlCommand = useCallback(
    async (
      text: string,
      opts?: { allowFork?: boolean; allowNew?: boolean },
    ): Promise<"handled" | "error" | null> => {
      if (!session) return null;
      const allowFork = opts?.allowFork ?? true;
      const allowNew = opts?.allowNew ?? true;

      const newArgs = allowNew ? matchControlCommand(text, "new") : null;
      if (newArgs !== null) {
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
          if (newArgs) {
            await sendInput(host, token, created.id, newArgs);
          }
          router.push(`/session/${created.id}`);
          return "handled";
        } catch (e) {
          if (isAuthError(e)) {
            handleAuthFailure();
            return "error";
          }
          setError(e instanceof Error ? e.message : "failed to create session");
          return "error";
        }
      }

      const forkArgs = allowFork ? matchControlCommand(text, "fork") : null;
      if (forkArgs !== null) {
        try {
          const forked = await forkSession(host, token, sessionId);
          if (forkArgs) {
            await sendInput(host, token, forked.id, forkArgs);
          }
          router.push(`/session/${forked.id}`);
          return "handled";
        } catch (e) {
          if (isAuthError(e)) {
            handleAuthFailure();
            return "error";
          }
          setError(e instanceof Error ? e.message : "failed to fork session");
          return "error";
        }
      }

      return null;
    },
    [session, host, token, sessionId, router, handleAuthFailure],
  );

  // Whether /fork is offered. Fork-ability is (agent supports it) AND (the
  // transport allows it): the live-terminal/tmux-wrapper transport has no fork
  // path (TmuxPlugin.fork_session is unimplemented), so an agent that otherwise
  // forks (claude_code) still can't when wrapped in a pane. The persistent
  // assistant additionally suppresses it (forking would spawn a stray managed
  // session off the launchpad).
  const canFork = Boolean(
    session &&
      supportsFork(session.backend, catalog) &&
      !liveTerminal(session.transport, catalog),
  );

  const submitInput = useCallback(async (
    text: string,
    command?: SessionCommandInvocation,
    attachments?: string[],
  ) => {
    if (!text.trim() && !attachments?.length) {
      return false;
    }

    // The persistent assistant isn't a launchpad — `/new` and `/fork` would
    // spawn stray managed sessions, so let them flow to the agent as text.
    // Attachment-bearing turns are never control commands, so skip the check.
    if (!attachments?.length) {
      const handled = await runFrontendControlCommand(text, {
        allowFork: canFork && !assistant,
        allowNew: !assistant,
      });
      if (handled !== null) {
        return handled === "handled";
      }
    }

    try {
      await sendInput(host, token, sessionId, text, command, attachments);
      return true;
    } catch (sendError) {
      if (isAuthError(sendError)) {
        handleAuthFailure();
        return false;
      }
      setError(sendError instanceof Error ? sendError.message : "failed to send input");
      return false;
    }
  }, [runFrontendControlCommand, handleAuthFailure, host, token, sessionId, assistant, canFork]);

  const onSendWithOptimistic = useCallback(
    async (
      text: string,
      command?: SessionCommandInvocation,
      attachments?: string[],
    ) => {
      const tempId = Math.random().toString(36).slice(2);
      const ts = new Date().toISOString();
      setOptimisticMessages((prev) => [...prev, { tempId, text, ts, confirmed: false }]);
      try {
        // Sending a message to an exited session restarts it: the placeholder
        // "Session has exited — send a message to reattach…" promises this UX.
        // Reattach first, then submit; both are sequenced so the input lands
        // after the freshly-restored transport accepts stdin.
        if (
          session &&
          (session.status === "exited" || session.status === "error") &&
          supportsReattachAfterExit(session.backend, catalog)
        ) {
          try {
            await postAction(host, token, sessionId, "reattach");
          } catch (reattachError) {
            if (isAuthError(reattachError)) {
              handleAuthFailure();
              return false;
            }
            setError(
              reattachError instanceof Error
                ? reattachError.message
                : "failed to reconnect",
            );
            return false;
          }
        }
        return await submitInput(text, command, attachments);
      } finally {
        setOptimisticMessages((prev) => prev.filter((m) => m.tempId !== tempId));
      }
    },
    [
      submitInput,
      session,
      catalog,
      host,
      token,
      sessionId,
      handleAuthFailure,
    ],
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

  const handleSetPinned = useCallback(
    async (pinned: boolean) => {
      try {
        const updated = await setSessionPinned(host, token, sessionId, pinned);
        setSession(updated);
      } catch (pinError) {
        if (isAuthError(pinError)) {
          handleAuthFailure();
          return;
        }
        setError(pinError instanceof Error ? pinError.message : "failed to update pin");
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
    session && supportsStructuredApproval(session.transport, catalog)
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
  // Latest task group, read off the raw event stream so the dock reflects the
  // true current state regardless of the transcript's event filter.
  const currentTaskEvent = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i -= 1) {
      if (isTodoListEvent(events[i])) {
        return events[i];
      }
    }
    return null;
  }, [events]);
  const taskProgress = useMemo(
    () => summarizeTodos(readTodoEntries(currentTaskEvent)),
    [currentTaskEvent],
  );
  // Session has stopped its backend process (clean shutdown or crash).
  const sessionExited = Boolean(
    session && (session.status === "exited" || session.status === "error"),
  );
  // The transport renders a live xterm pane (and locks the chat composer into
  // terminal mode) rather than a structured transcript.
  const terminalOnly = Boolean(session && liveTerminal(session.transport, catalog));
  const canReattachAfterExit = Boolean(
    session && supportsReattachAfterExit(session.backend, catalog),
  );
  const dormantReattach = sessionExited && canReattachAfterExit;
  const composerDisabled =
    !session || (sessionExited && !canReattachAfterExit);
  const composerPlaceholder = !session
    ? "Loading session…"
    : dormantReattach
      ? "Session has exited — send a message to reattach…"
      : composerDisabled
        ? "Session has exited — composer disabled."
        : "Reply to the agent…";
  // Whether the transport exposes a terminal pane (WS-backed xterm mirror).
  // Drives terminal tab visibility and the WS-pane effect below.
  const canShowTerminal = session
    ? hasTerminalPane(session.transport, catalog)
    : false;
  // Whether the user can type into the terminal pane.
  const canTerminalInteract = session
    ? terminalInteractive(session.transport, catalog)
    : false;
  // Whether the terminal pane accepts resize frames.
  const canTerminalResize = session
    ? terminalResizable(session.transport, catalog)
    : false;
  const activeView: ViewMode = terminalOnly
    ? "terminal"
    : !canShowTerminal
      ? "chat"
      : view;
  const showTaskDock =
    activeView === "chat" &&
    taskProgress !== null &&
    currentTaskEvent !== null &&
    dismissedTaskSequence !== undefined &&
    currentTaskEvent.sequence !== dismissedTaskSequence;
  const { theme } = useTheme();
  const terminalRef = useRef<XTerminalHandle | null>(null);
  const terminalSocketRef = useRef<WebSocket | null>(null);
  // Bumped to reconnect the terminal WS: on EXITED → live transitions and
  // when the pane target (tmux_pane) changes under a running session.
  const [terminalEpoch, setTerminalEpoch] = useState(0);
  const prevSessionExitedRef = useRef(sessionExited);
  useEffect(() => {
    if (prevSessionExitedRef.current && !sessionExited) {
      setTerminalEpoch((e) => e + 1);
    }
    prevSessionExitedRef.current = sessionExited;
  }, [sessionExited]);
  const tmuxPane =
    typeof session?.transport_state?.tmux_pane === "string"
      ? session.transport_state.tmux_pane
      : null;
  const prevTmuxPaneRef = useRef<string | null>(null);
  useEffect(() => {
    if (
      prevTmuxPaneRef.current !== null &&
      tmuxPane !== null &&
      tmuxPane !== prevTmuxPaneRef.current
    ) {
      setTerminalEpoch((e) => e + 1);
    }
    prevTmuxPaneRef.current = tmuxPane;
  }, [tmuxPane]);

  // Terminal pane: connect a WebSocket when the terminal tab is open and the
  // transport has a pane (lazy — defers connection until the tab is visible).
  // Write streamed bytes into xterm; for interactive transports forward
  // keystrokes and resize frames back. Reconnects with capped exponential
  // backoff on transient drops.
  //
  // Deliberately does NOT depend on the full ``session`` object — the
  // session-state WS pushes updated SessionRecord references on every change
  // (effort/model/etc.), and re-running this effect would close the terminal
  // socket and ``term.reset()`` xterm on every push, which the user sees as
  // the whole pane blanking out and re-painting.
  const paneTransport = session?.transport ?? null;
  useEffect(() => {
    if (activeView !== "terminal") return;
    if (!paneTransport || !hasTerminalPane(paneTransport, catalog)) return;
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
          // never fires onResize. Non-resizable panes (claude_tty) own their
          // geometry server-side and must never be resized from here, so skip
          // the seed resize entirely for them.
          if (!paneTransport || !terminalResizable(paneTransport, catalog)) {
            return;
          }
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
        onResize: (cols, rows) => {
          // Fixed-grid panes (claude_tty) are pinned server-side; match our
          // grid so the cell-positioned stream aligns instead of rewrapping.
          terminalRef.current?.resize(cols, rows);
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
  }, [activeView, paneTransport, catalog, host, token, sessionId, handleAuthFailure, terminalEpoch]);

  const handleTerminalInput = useCallback((data: string) => {
    const socket = terminalSocketRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "input", data }));
    }
  }, []);

  // Whole-message submission from the quick-compose drawer — the backend
  // appends Enter when ``submit`` is true so each call lands as one
  // logical message rather than a stream of keystrokes. ``/new`` is caught
  // here as a Waypoint control command instead of being typed into the
  // wrapped CLI; ``/fork`` follows the agent's fork capability.
  const handleTerminalSubmit = useCallback(async (text: string): Promise<TerminalSubmitResult> => {
    const handled = await runFrontendControlCommand(text, { allowFork: canFork });
    if (handled !== null) {
      return handled === "handled" ? "ok" : "command-error";
    }
    const socket = terminalSocketRef.current;
    if (socket?.readyState !== WebSocket.OPEN) return "socket-closed";
    socket.send(JSON.stringify({ type: "input_submit", text, submit: true }));
    return "ok";
  }, [runFrontendControlCommand, canFork]);

  // Attachment-bearing terminal submits go over HTTP, not the terminal WS:
  // handle_input routes them through the tmux transport, which appends the
  // host file paths to the message so the wrapped CLI can read them.
  const handleTerminalSubmitWithAttachments = useCallback(
    async (text: string, attachmentIds: string[]): Promise<TerminalSubmitResult> => {
      const sent = await submitInput(text, undefined, attachmentIds);
      return sent ? "ok" : "command-error";
    },
    [submitInput],
  );

  const requestPaste = useCallback(async () => {
    setError("");
    if (typeof navigator === "undefined" || !navigator.clipboard?.readText) {
      setError("Clipboard read is unavailable in this browser");
      return;
    }
    let text: string;
    try {
      text = await navigator.clipboard.readText();
    } catch {
      setError("Clipboard access blocked — approve the paste prompt and retry");
      return;
    }
    if (!text) return;
    if (isSafariFamily()) {
      // Stash the raw text so the preview char count reflects what the
      // user actually pasted; bracketed-paste wrapping happens at send
      // time inside ``sendPendingPaste``.
      setPendingPaste(text);
      setPasteSeq((s) => s + 1);
    } else {
      handleTerminalInput(wrapBracketedPaste(text));
    }
  }, [handleTerminalInput]);

  const sendPendingPaste = useCallback(() => {
    if (pendingPaste === null) return;
    handleTerminalInput(wrapBracketedPaste(pendingPaste));
    setPendingPaste(null);
  }, [pendingPaste, handleTerminalInput]);

  const dismissPendingPaste = useCallback(() => {
    setPendingPaste(null);
  }, []);

  const dismissClipboardPill = useCallback(() => {
    setPendingClipboard(null);
    setClipboardCopied(false);
  }, []);

  const [terminalDims, setTerminalDims] = useState<{ cols: number; rows: number } | null>(null);
  const handleTerminalResize = useCallback(
    ({ cols, rows }: { cols: number; rows: number }) => {
      setTerminalDims({ cols, rows });
      if (!canTerminalResize) return;
      const socket = terminalSocketRef.current;
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "resize", cols, rows }));
      }
    },
    [canTerminalResize],
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
      const target = event.target as Element | null;
      // The menu is portaled to document.body, so a click on an item is not
      // inside the trigger wrapper — match the portaled menu separately so it
      // isn't dismissed before the item's handler runs.
      if (target?.closest("[data-term-overflow-menu]")) return;
      if (wrap && !wrap.contains(target)) {
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
  const handleTerminalRefresh = useCallback(() => {
    setTerminalEpoch((e) => e + 1);
  }, []);
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
          catalog={catalog}
          onSetTitle={handleSetTitle}
          onSetPinned={handleSetPinned}
          assistant={assistant}
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
          ) : canShowTerminal ? (
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
          ) : null}
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
        <AttachmentContextProvider value={attachmentContext}>
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
                      if (!event) return null;
                      // Codex's todo event carries no tool_name; tag every todo
                      // as "TodoWrite" so the run summary can show a todos chip.
                      return isTodoListEvent(event) ? "TodoWrite" : readToolName(event);
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
                            catalog={catalog}
                            onAnswerAskQuestion={submitAskAnswer}
                            key={`pair-${child.pair.itemId}`}
                          />
                        ) : (
                          <TranscriptCard
                            event={child.event}
                            transport={session.transport}
                            catalog={catalog}
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
                    catalog={catalog}
                    onAnswerAskQuestion={submitAskAnswer}
                    key={`pair-${item.pair.itemId}`}
                  />
                ) : (
                  <TranscriptCard
                    event={item.event}
                    transport={session.transport}
                    catalog={catalog}
                    onAnswerAskQuestion={submitAskAnswer}
                    key={`${item.event.sequence}-${item.event.id ?? "local"}`}
                  />
                );
              })
            : session && optimisticMessages.length === 0 && !pendingPlanApprovalEvent
              ? assistant && session.status !== "exited" ? (
                <AssistantWelcome onPick={onSendWithOptimistic} />
              ) : (
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
        </AttachmentContextProvider>
      ) : null}
      {session && activeView === "terminal" ? (
        <SessionTerminalView
          host={host}
          token={token}
          sessionId={sessionId}
          session={session}
          interactive={canTerminalInteract}
          terminalRef={terminalRef}
          theme={theme}
          terminalDims={terminalDims}
          sessionExited={sessionExited}
          dormantReattach={dormantReattach}
          // The Terminal tab is presented fullscreen (sticky, viewport-filling)
          // for every session, so an emulated session's pane matches a
          // terminal-only session's height. The pane only renders in terminal
          // view, so this is effectively always on here.
          locked={terminalOnly || activeView === "terminal"}
          termMenuOpen={termMenuOpen}
          setTermMenuOpen={setTermMenuOpen}
          termMenuWrapRef={termMenuWrapRef}
          termAtBottom={termAtBottom}
          connection={connection}
          rateLimitRefreshBusy={rateLimitRefreshBusy}
          onRateLimitRefresh={handleRateLimitRefresh}
          onTerminalInput={handleTerminalInput}
          onTerminalSubmit={handleTerminalSubmit}
          onTerminalSubmitWithAttachments={handleTerminalSubmitWithAttachments}
          attachmentsEnabled={
            session ? supportsAttachments(session.backend, catalog) : false
          }
          onRequestPaste={requestPaste}
          onTerminalResize={handleTerminalResize}
          onTerminalScrollChip={handleTerminalScrollChip}
          onTerminalScrollChange={handleTerminalScrollChange}
          onJumpToLive={handleJumpToLive}
          onRefresh={handleTerminalRefresh}
          onResume={resumeSession}
          onReattach={reattach}
          onTerminate={terminate}
          onRemoveFromList={removeFromList}
          onSwitchSession={openSwitcher}
          onError={setError}
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
            decisions={session ? approvalDecisionsFor(session.backend, catalog) : undefined}
          />
        </>
      ) : null}
      {!terminalOnly && !pendingApproval && pendingPlanApprovalView ? (
        <PlanApprovalCard
          agentLabel={session ? humaniseBackend(session.backend, catalog) : "Codex"}
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
      {pendingPaste === null && pendingClipboard !== null ? (
        <div
          key={`clipboard-pending-${clipboardSeq}`}
          className="clipboard-prompt"
          role="dialog"
          aria-label="Copy response to clipboard"
        >
          <span className="clipboard-prompt__glyph" aria-hidden="true">
            <svg
              viewBox="0 0 16 16"
              width="14"
              height="14"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <rect x="3.5" y="3" width="9" height="11" rx="1.5" />
              <path d="M6 3V2.25c0-.41.34-.75.75-.75h2.5c.41 0 .75.34.75.75V3" />
              <path d="M6 7.5h4M6 10h4" />
            </svg>
          </span>
          <button
            type="button"
            className="clipboard-prompt__send"
            onMouseDown={(event) => event.preventDefault()}
            onClick={copyPendingClipboard}
            aria-label={`Copy ${pendingClipboard.length} characters to clipboard`}
          >
            <span className="clipboard-prompt__label">Response ready</span>
            <span className="clipboard-prompt__meta">
              {pendingClipboard.length.toLocaleString()} chars · tap to copy
            </span>
          </button>
          <button
            type="button"
            className="clipboard-prompt__dismiss"
            onMouseDown={(event) => event.preventDefault()}
            onClick={dismissClipboardPill}
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      ) : pendingPaste === null && clipboardCopied ? (
        <div
          key={`clipboard-copied-${clipboardSeq}`}
          className="clipboard-prompt is-copied"
          role="status"
        >
          <span className="clipboard-prompt__glyph" aria-hidden="true">
            <svg
              viewBox="0 0 16 16"
              width="14"
              height="14"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M3.5 8.5L6.5 11.5L12.5 5" />
            </svg>
          </span>
          <span className="clipboard-prompt__body">
            <span className="clipboard-prompt__label">Copied</span>
            <span className="clipboard-prompt__meta">paste anywhere</span>
          </span>
          <button
            type="button"
            className="clipboard-prompt__dismiss"
            onMouseDown={(event) => event.preventDefault()}
            onClick={dismissClipboardPill}
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      ) : null}
      {pendingPaste !== null ? (
        <div
          key={`paste-pending-${pasteSeq}`}
          className="clipboard-prompt is-paste"
          role="dialog"
          aria-label="Confirm paste"
        >
          <span className="clipboard-prompt__glyph" aria-hidden="true">
            <svg
              viewBox="0 0 16 16"
              width="14"
              height="14"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <rect x="3.5" y="3" width="9" height="11" rx="1.5" />
              <path d="M6 3V2.25c0-.41.34-.75.75-.75h2.5c.41 0 .75.34.75.75V3" />
              <path d="M8 6.25v4.25M6 8.5l2 2 2-2" />
            </svg>
          </span>
          <button
            type="button"
            className="clipboard-prompt__send"
            onMouseDown={(event) => event.preventDefault()}
            onClick={sendPendingPaste}
            aria-label={`Send ${pendingPaste.length} characters from clipboard`}
          >
            <span className="clipboard-prompt__label">Paste ready</span>
            <span className="clipboard-prompt__meta">
              {pendingPaste.length.toLocaleString()} chars · tap to send
            </span>
          </button>
          <button
            type="button"
            className="clipboard-prompt__dismiss"
            onMouseDown={(event) => event.preventDefault()}
            onClick={dismissPendingPaste}
            aria-label="Cancel paste"
          >
            ×
          </button>
        </div>
      ) : null}
      {showTaskDock && taskProgress ? (
        <TaskProgressDock
          progress={taskProgress}
          onDismiss={() => {
            const sequence = currentTaskEvent?.sequence ?? null;
            setDismissedTaskSequence(sequence);
            if (sequence !== null && typeof window !== "undefined") {
              try {
                window.sessionStorage.setItem(
                  `${TASK_DOCK_DISMISSED_STORAGE_PREFIX}${sessionId}`,
                  String(sequence),
                );
              } catch {
                // sessionStorage unavailable (private mode, quota) — the
                // in-memory dismissal above still applies for this view.
              }
            }
          }}
        />
      ) : null}
      {session && !terminalOnly ? (
        <ReplyComposer
          host={host}
          token={token}
          sessionId={sessionId}
          session={session}
          permissionModeOptions={
            session ? permissionModesFor(session.backend, catalog) : []
          }
          canDelete={sessionExited}
          canReattach={dormantReattach}
          canTerminate={Boolean(session && !sessionExited)}
          connection={connection}
          disabled={composerDisabled}
          rateLimitRefreshBusy={rateLimitRefreshBusy}
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
          catalog={catalog}
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
          onSwitchSession={openSwitcher}
          onSend={onSendWithOptimistic}
          attachmentsEnabled={
            session ? supportsAttachments(session.backend, catalog) : false
          }
          onTerminate={terminate}
          onError={setError}
          assistant={assistant}
          assistantControls={assistantControls}
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
  canReattach: boolean;
  canTerminate: boolean;
  connection: ConnectionState;
  disabled: boolean;
  rateLimitRefreshBusy: boolean;
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
  catalog: BackendCatalog;
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
  onSwitchSession: () => void;
  onSend: (
    text: string,
    command?: SessionCommandInvocation,
    attachments?: string[],
  ) => Promise<boolean>;
  attachmentsEnabled: boolean;
  onTerminate: () => void | Promise<void>;
  onError: (message: string) => void;
  assistant: boolean;
  assistantControls: AssistantControls | null;
}

const ReplyComposer = memo(function ReplyComposer({
  host,
  token,
  sessionId,
  session,
  agentBusy,
  permissionModeOptions,
  canDelete,
  canReattach,
  canTerminate,
  connection,
  disabled,
  rateLimitRefreshBusy,
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
  catalog,
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
  onSwitchSession,
  onSend,
  attachmentsEnabled,
  onTerminate,
  onError,
  assistant,
  assistantControls,
}: ReplyComposerProps) {
  const [draft, setDraft] = useState("");
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const attachments = useAttachments({ host, token, sessionId, onError });
  const [filesOpen, setFilesOpen] = useState(false);
  const [sending, setSending] = useState(false);
  const [overflowOpen, setOverflowOpen] = useState(false);
  const [tuneOpen, setTuneOpen] = useState(false);
  const [reattaching, setReattaching] = useState(false);
  // Assistant lifecycle UI: an in-flight action and which context-discarding
  // action (backend switch / attach thread / clear) is awaiting an inline
  // confirm.
  const [assistantBusy, setAssistantBusy] = useState(false);
  // Confirm state for the dropdown-driven actions in the settings popover
  // (switch backend / attach thread). Clear context and terminate / reattach
  // live in the ⋯ overflow menu and confirm via window.confirm instead.
  const [assistantConfirm, setAssistantConfirm] = useState<
    "switch" | "attach" | null
  >(null);
  const [pendingBackend, setPendingBackend] = useState<Backend | null>(null);
  // Staged transport for the switch confirm; `null` keeps the live one (or the
  // newly-picked agent's default once a different agent is staged).
  const [pendingTransport, setPendingTransport] =
    useState<SessionTransport | null>(null);
  const [selectedThreadId, setSelectedThreadId] = useState("");
  const [threadOptions, setThreadOptions] = useState<AssistantThreadOption[]>([]);
  // Pending effort for backends that need a session restart to apply (Claude)
  // — staged here until the user confirms via the Apply button. `null` means
  // no pending change.
  const [pendingEffort, setPendingEffort] = useState<string | null>(null);
  // iMessage-style leading actions: ⊕ + 📎 collapse to a single ›-chevron via
  // CSS (:focus-within) so focusing the field never triggers a React re-render
  // that could drop focus mid-gesture. The chevron re-opens them by forcing
  // `lead-forced`, which overrides the focus-within collapse; typing or
  // blurring clears it.
  const [leadForced, setLeadForced] = useState(false);

  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const composerRef = useRef<HTMLElement | null>(null);
  const overflowRef = useRef<HTMLDivElement | null>(null);
  const tuneRef = useRef<HTMLDivElement | null>(null);

  // Auto-grow the field to fit its content: a single line by default (so the
  // pill matches the flanking buttons), growing as the draft wraps up to the
  // CSS max-height, then it scrolls.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) {
      return;
    }
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [draft]);

  // Built-in slash commands are intercepted on the backend only for
  // structured transports (see plugin.maybe_handle_input); skip
  // suggestions on tmux. While the session is still loading
  // (transport=null), default to off.
  const supportsSlash =
    transport !== null && fidelityFor(transport, catalog) === "structured";

  const {
    suggestions,
    suggestionsOpen,
    activeIndex,
    setActiveIndex,
    listRef: suggestionsRef,
    itemRefs: suggestionItemRefs,
    applySuggestion,
    selectedCommandInvocation,
    handleSuggestionKey,
    reset: resetCompletions,
  } = useCommandCompletions({
    host,
    token,
    sessionId,
    draft,
    setDraft,
    enabled: supportsSlash,
    textareaRef,
  });

  const mentions = useFileMentions({
    host,
    token,
    sessionId,
    draft,
    setDraft,
    enabled: attachmentsEnabled,
    onReference: attachments.referenceExisting,
    textareaRef,
  });

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

  async function handleSend() {
    const text = draft.trim();
    const attachmentIds = attachments.readyIds;
    // Allow an attachment-only turn, but never send while an upload is
    // still in flight (its id wouldn't be in readyIds yet).
    if ((!text && attachmentIds.length === 0) || attachments.uploading) {
      return;
    }
    setSending(true);
    setDraft("");
    const invocation = selectedCommandInvocation(text);
    resetCompletions();
    try {
      const sent = await onSend(
        text,
        invocation,
        attachmentIds.length ? attachmentIds : undefined,
      );
      if (sent) {
        attachments.clear();
      } else {
        setDraft(text);
      }
    } finally {
      setSending(false);
    }
  }

  const addFilesFromInput = (event: ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files ? Array.from(event.target.files) : [];
    if (files.length) attachments.addFiles(files);
    event.target.value = "";
  };

  const handlePaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    if (!attachmentsEnabled) return;
    const files = filesFromDataTransfer(event.clipboardData);
    if (files.length === 0) return;
    // Pasted files (e.g. a screenshot) become attachments; let plain-text
    // pastes fall through to the textarea.
    event.preventDefault();
    attachments.addFiles(files);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    if (!attachmentsEnabled) return;
    event.preventDefault();
    setDragActive(false);
    const files = filesFromDataTransfer(event.dataTransfer);
    if (files.length) attachments.addFiles(files);
  };

  const handleDragOver = (event: DragEvent<HTMLDivElement>) => {
    if (!attachmentsEnabled) return;
    if (!Array.from(event.dataTransfer.types).includes("Files")) return;
    event.preventDefault();
    setDragActive(true);
  };

  const handleDragLeave = (event: DragEvent<HTMLDivElement>) => {
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
    setDragActive(false);
  };

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
    if (mentions.handleKey(event)) {
      return;
    }
    if (handleSuggestionKey(event)) {
      return;
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

  const assistantOps = assistant ? assistantControls : null;
  const tuneVisible =
    modeOptions.length > 0 ||
    hasModelPicker ||
    hasEffortPicker ||
    assistantOps !== null;
  // Backend the assistant controls target — the picked one, or the current.
  const assistantTargetBackend = pendingBackend ?? session?.backend ?? null;
  const assistantTargetCaps =
    assistantOps && assistantTargetBackend
      ? assistantOps.backends.find((b) => b.id === assistantTargetBackend)
          ?.capabilities
      : undefined;
  const assistantCanAttach = Boolean(
    assistantTargetCaps?.supports_thread_discovery &&
      assistantTargetCaps?.supports_thread_import,
  );
  // Agent-primary list, folding out the tmux/tty wrappers and the managed
  // fallback, plus the transports the staged agent can run over.
  const assistantAgentOptions =
    assistantOps && session
      ? launchableAgents(
          assistantOps.backends.map((b) => b.id),
          catalog,
        )
      : [];
  const assistantTransportOptions = assistantTargetBackend
    ? agentTransports(assistantTargetBackend, catalog)
    : [];
  // The transport the switch will launch over: an explicit pick, else the new
  // agent's default when the agent changed, else the live transport.
  const assistantTargetTransport: SessionTransport | null =
    pendingTransport ??
    (pendingBackend
      ? defaultTransportFor(pendingBackend, catalog)
      : session?.transport ?? null);
  const assistantSwitchDiffers = Boolean(
    session &&
      assistantTargetBackend &&
      assistantTargetTransport &&
      (assistantTargetBackend !== session.backend ||
        assistantTargetTransport !== session.transport),
  );
  // Load resumable threads for the target backend when the popover is open.
  useEffect(() => {
    if (
      !tuneOpen ||
      !assistantOps ||
      !assistantCanAttach ||
      !assistantTargetBackend
    ) {
      setThreadOptions([]);
      return;
    }
    let cancelled = false;
    void assistantOps.listThreads(assistantTargetBackend).then((threads) => {
      if (!cancelled) setThreadOptions(threads);
    });
    return () => {
      cancelled = true;
    };
  }, [tuneOpen, assistantOps, assistantCanAttach, assistantTargetBackend]);
  const runAssistantAction = async (action: () => Promise<void> | void) => {
    if (assistantBusy) return;
    setAssistantBusy(true);
    onError("");
    try {
      await action();
      // Only dismiss the confirm on success; on failure keep the selection so
      // the user can retry or cancel.
      setAssistantConfirm(null);
      setPendingBackend(null);
      setPendingTransport(null);
      setSelectedThreadId("");
    } catch (err) {
      onError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setAssistantBusy(false);
    }
  };
  const assistantExited = Boolean(
    session && (session.status === "exited" || session.status === "error"),
  );
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
                {assistantOps && session ? (
                  <label className="composer-tune-field">
                    <span>Agent</span>
                    <select
                      value={pendingBackend ?? session.backend}
                      onChange={(event) => {
                        const next = event.target.value;
                        onError("");
                        // Threads and transports are per-agent; drop any thread
                        // selection and let the transport fall to the new
                        // agent's default.
                        setSelectedThreadId("");
                        setPendingTransport(null);
                        if (next === session.backend) {
                          setPendingBackend(null);
                          setAssistantConfirm(null);
                          return;
                        }
                        setPendingBackend(next);
                        // Stage the confirm only once the new agent's default
                        // interface resolves; until the catalog loads it would
                        // be set but not renderable.
                        setAssistantConfirm(
                          defaultTransportFor(next, catalog) ? "switch" : null,
                        );
                      }}
                      disabled={assistantBusy}
                    >
                      {assistantAgentOptions.map((backend) => (
                        <option key={backend} value={backend}>
                          {humaniseBackend(backend, catalog)}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : null}
                {assistantOps && session && assistantTransportOptions.length > 1 ? (
                  <label className="composer-tune-field">
                    <span>Interface</span>
                    <select
                      value={assistantTargetTransport ?? ""}
                      onChange={(event) => {
                        const next = event.target.value as SessionTransport;
                        onError("");
                        // Picking a thread takes priority; a fresh transport
                        // pick rebuilds the thread like an agent switch.
                        setSelectedThreadId("");
                        setPendingTransport(next);
                        const stagedBackend = pendingBackend ?? session.backend;
                        const differs =
                          stagedBackend !== session.backend ||
                          next !== session.transport;
                        setAssistantConfirm(differs ? "switch" : null);
                      }}
                      disabled={assistantBusy}
                    >
                      {assistantTransportOptions.map((transportId) => (
                        <option key={transportId} value={transportId}>
                          {transportPresentation(transportId, catalog).name}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : null}
                {assistantOps &&
                session &&
                assistantCanAttach &&
                threadOptions.length > 0 ? (
                  <label className="composer-tune-field">
                    <span>Resume thread</span>
                    <select
                      value={selectedThreadId}
                      onChange={(event) => {
                        const id = event.target.value;
                        onError("");
                        setSelectedThreadId(id);
                        if (id) {
                          setAssistantConfirm("attach");
                        } else {
                          setAssistantConfirm(
                            assistantSwitchDiffers ? "switch" : null,
                          );
                        }
                      }}
                      disabled={assistantBusy}
                    >
                      <option value="">— Start fresh —</option>
                      {threadOptions.map((thread) => {
                        const when = thread.updatedAt
                          ? formatRelativeTime(thread.updatedAt)
                          : "";
                        const parts = [thread.title || thread.id];
                        if (when && when !== "Unknown") parts.push(when);
                        if (thread.preview) parts.push(thread.preview);
                        return (
                          <option key={thread.id} value={thread.id}>
                            {parts.join(" · ")}
                          </option>
                        );
                      })}
                    </select>
                  </label>
                ) : null}
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
                {assistantOps &&
                assistantConfirm === "switch" &&
                assistantSwitchDiffers &&
                assistantTargetBackend &&
                assistantTargetTransport ? (
                  <div className="composer-tune-lifecycle">
                    <div className="composer-tune-confirm">
                      <p>
                        Switch to{" "}
                        <strong>
                          {humaniseBackend(assistantTargetBackend, catalog)} ·{" "}
                          {
                            transportPresentation(
                              assistantTargetTransport,
                              catalog,
                            ).name
                          }
                        </strong>
                        ? This starts a new conversation; the current one is kept
                        as a stopped session.
                      </p>
                      <div className="composer-tune-confirm-actions">
                        <button
                          type="button"
                          className="composer-tune-confirm-cancel"
                          onClick={() => {
                            setPendingBackend(null);
                            setPendingTransport(null);
                            setAssistantConfirm(null);
                            onError("");
                          }}
                          disabled={assistantBusy}
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          className="composer-tune-confirm-apply"
                          onClick={() =>
                            void runAssistantAction(() =>
                              assistantOps.onSwitchBackend(
                                assistantTargetBackend,
                                assistantTargetTransport,
                              ),
                            )
                          }
                          disabled={assistantBusy}
                        >
                          {assistantBusy ? "Switching…" : "Switch"}
                        </button>
                      </div>
                    </div>
                  </div>
                ) : assistantOps &&
                  assistantConfirm === "attach" &&
                  selectedThreadId &&
                  assistantTargetBackend ? (
                  <div className="composer-tune-lifecycle">
                    <div className="composer-tune-confirm">
                      <p>
                        Attach to{" "}
                        <strong>
                          {threadOptions.find((t) => t.id === selectedThreadId)
                            ?.title || selectedThreadId}
                        </strong>
                        ? This replaces the current conversation, which is kept as
                        a stopped session.
                      </p>
                      <div className="composer-tune-confirm-actions">
                        <button
                          type="button"
                          className="composer-tune-confirm-cancel"
                          onClick={() => {
                            setSelectedThreadId("");
                            setAssistantConfirm(
                              assistantSwitchDiffers ? "switch" : null,
                            );
                            onError("");
                          }}
                          disabled={assistantBusy}
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          className="composer-tune-confirm-apply"
                          onClick={() =>
                            void runAssistantAction(() =>
                              assistantOps.onAttachThread(
                                assistantTargetBackend,
                                selectedThreadId,
                              ),
                            )
                          }
                          disabled={assistantBusy}
                        >
                          {assistantBusy ? "Attaching…" : "Attach thread"}
                        </button>
                      </div>
                    </div>
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
          <SessionUsagePill
            session={session}
            connection={connection}
            catalog={catalog}
            onRateLimitRefresh={onRateLimitRefresh}
            rateLimitRefreshBusy={rateLimitRefreshBusy}
          />
        </div>
      </div>
      <div className={`composer-bar${leadForced ? " lead-forced" : ""}`}>
      <button
        type="button"
        className="composer-lead-expand"
        onClick={() => {
          setLeadForced(true);
          textareaRef.current?.focus();
        }}
        aria-label="Show actions"
        title="Show actions"
      >
        ›
      </button>
      <button
        type="button"
        className="composer-interrupt-btn"
        onClick={() => void onInterrupt()}
        disabled={disabled}
        aria-label="Interrupt the agent"
        title="Interrupt the agent"
      >
        <span className="glyph" aria-hidden>
          ■
        </span>
      </button>
      <div
        className={`reply-textarea-wrap${dragActive ? " is-drag-active" : ""}`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {attachmentsEnabled ? (
          <AttachmentTray
            items={attachments.items}
            onRemove={attachments.remove}
            onRetry={attachments.retry}
            onClear={attachments.discardAll}
          />
        ) : null}
        <textarea
          ref={textareaRef}
          className="composer-textarea"
          rows={1}
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value);
            setLeadForced(false);
          }}
          onBlur={() => setLeadForced(false)}
          onKeyDown={handleDraftKeyDown}
          onPaste={handlePaste}
          disabled={disabled}
          placeholder={placeholder}
          aria-label="Reply"
        />
        {dragActive ? (
          <div className="composer-drop-hint" aria-hidden="true">
            <span className="composer-drop-glyph">⤓</span>
            <span>Drop to attach</span>
          </div>
        ) : null}
        <button
          type="button"
          className="composer-send"
          onClick={() => void handleSend()}
          disabled={
            disabled ||
            sending ||
            attachments.uploading ||
            (!draft.trim() && attachments.readyIds.length === 0)
          }
          aria-label="Send"
          title="Send (⌘/Ctrl + ↵)"
        >
          <span className="composer-send-glyph" aria-hidden>
            {sending ? "…" : "↑"}
          </span>
        </button>
        {suggestionsOpen ? (
          <CommandSuggestions
            ref={suggestionsRef}
            suggestions={suggestions}
            activeIndex={activeIndex}
            itemRefs={suggestionItemRefs}
            onApply={applySuggestion}
            onHover={setActiveIndex}
          />
        ) : null}
        {mentions.open ? (
          <FileMentions
            host={host}
            token={token}
            sessionId={sessionId}
            mentions={mentions.mentions}
            activeIndex={mentions.activeIndex}
            itemRefs={mentions.itemRefs}
            onApply={mentions.apply}
            onHover={mentions.setActiveIndex}
          />
        ) : null}
      </div>
        {attachmentsEnabled ? (
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="composer-file-input"
            onChange={addFilesFromInput}
            aria-hidden="true"
            tabIndex={-1}
          />
        ) : null}
        {attachmentsEnabled ? (
          <button
            type="button"
            className="composer-attach-btn"
            onClick={() => fileInputRef.current?.click()}
            disabled={disabled}
            aria-label="Attach files"
            title="Attach files"
          >
            <span className="glyph" aria-hidden>
              <PaperclipIcon />
            </span>
          </button>
        ) : null}
        {hasOverflow ? (
          <div className="composer-overflow composer-plus" ref={overflowRef}>
            <button
              type="button"
              className={`composer-plus-trigger ${overflowOpen ? "open" : ""}`}
              aria-haspopup="menu"
              aria-expanded={overflowOpen}
              aria-label="Actions"
              onClick={() => setOverflowOpen((open) => !open)}
            >
              ＋
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
                  {!assistant && canReattach ? (
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
                  {attachmentsEnabled ? (
                    <button
                      type="button"
                      role="menuitem"
                      className="composer-overflow-item"
                      onClick={() => {
                        setOverflowOpen(false);
                        setFilesOpen(true);
                      }}
                    >
                      <span className="glyph">▤</span>
                      Files…
                    </button>
                  ) : null}
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
                  {/* The assistant is a protected singleton — no terminate or
                      delete (the backend rejects both anyway). */}
                  {!assistant && (canTerminate || canDelete) ? (
                    <div className="composer-overflow-separator" />
                  ) : null}
                  {!assistant && canTerminate ? (
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
                  {!assistant && canDelete ? (
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
                  {/* The assistant is a protected singleton, so instead of
                      terminate/delete it gets its own lifecycle actions here:
                      clear context (fresh thread) and a stop/revive toggle. */}
                  {assistant && assistantOps ? (
                    <>
                      <div className="composer-overflow-separator" />
                      <button
                        type="button"
                        role="menuitem"
                        className="composer-overflow-item warn"
                        disabled={assistantBusy}
                        onClick={() => {
                          setOverflowOpen(false);
                          if (
                            window.confirm(
                              "Clear the assistant's context? The current thread is kept as a stopped session.",
                            )
                          ) {
                            void runAssistantAction(assistantOps.onClearContext);
                          }
                        }}
                      >
                        <span className="glyph">✦</span>
                        Clear context
                      </button>
                      {assistantExited ? (
                        assistantOps.supportsReattach ? (
                          <button
                            type="button"
                            role="menuitem"
                            className="composer-overflow-item"
                            disabled={assistantBusy}
                            onClick={() => {
                              setOverflowOpen(false);
                              void runAssistantAction(assistantOps.onReattach);
                            }}
                          >
                            <span className="glyph">↺</span>
                            Reattach assistant
                          </button>
                        ) : null
                      ) : (
                        <button
                          type="button"
                          role="menuitem"
                          className="composer-overflow-item danger"
                          disabled={assistantBusy}
                          onClick={() => {
                            setOverflowOpen(false);
                            if (
                              window.confirm(
                                "Terminate the assistant? You can reattach it later to resume this conversation.",
                              )
                            ) {
                              void runAssistantAction(assistantOps.onTerminate);
                            }
                          }}
                        >
                          <span className="glyph">⏻</span>
                          Terminate assistant
                        </button>
                      )}
                    </>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      {attachmentsEnabled ? (
        <SessionFilesPanel
          host={host}
          token={token}
          sessionId={sessionId}
          open={filesOpen}
          onClose={() => setFilesOpen(false)}
          onReference={attachments.referenceExisting}
          referencedIds={attachments.attachedIds}
        />
      ) : null}
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
  // "content"  → user messages, agent text, approvals, ask-questions:
  //              always breaks (and terminates) the current tool run.
  // "tool"     → ordinary tool call/result pairs/singles, including todos:
  //              join the run. The live task list lives in the dock now, so
  //              the transcript todo marker is just a historical tool record
  //              and folds into the run instead of standing alone.
  // "absorbed" → system_note / status_update lifecycle noise: silently join
  //              the active run (rendered as quiet separators when expanded),
  //              or fall through as standalone if no run is active yet.
  function classifyItem(item: Extract<TranscriptItem, { kind: "single" | "pair" }>): "content" | "tool" | "absorbed" {
    if (item.kind === "pair") {
      const { call, result } = item.pair;
      const isSpecial = (e: EventRecord | null) =>
        e !== null && readToolName(e) === "AskUserQuestion";
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
        return readToolName(event) === "AskUserQuestion" ? "content" : "tool";
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
  catalog,
  onSetTitle,
  onSetPinned,
  assistant = false,
}: {
  session: SessionRecord;
  connection: ConnectionState;
  modelOptions: BackendModelOption[];
  catalog: BackendCatalog;
  onSetTitle?: (title: string) => void | Promise<void>;
  onSetPinned?: (pinned: boolean) => void | Promise<void>;
  assistant?: boolean;
}) {
  const cwdSegments = formatCwdSegments(session.cwd);
  const target = session.launch_target_id ?? null;
  // Match the session card: fold a legacy transport-as-backend row (e.g.
  // backend=claude_tty) to its owning agent, and show the transport chip only
  // when it differs from that agent's default.
  const headerAgent = displayAgentFor(session.backend, session.transport, catalog);
  const headerAgentDefault = defaultTransportFor(headerAgent, catalog);
  const showHeaderTransport =
    headerAgentDefault !== null && session.transport !== headerAgentDefault;
  const sourceLabel = session.source === "managed" ? "Managed" : "Attached";
  const pinned = Boolean(session.pinned_at);
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
    <header className={`session-header${pinned ? " pinned" : ""}`}>
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
              {onSetTitle && !assistant ? (
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
              {onSetPinned && !assistant ? (
                <button
                  className={`link-button session-header-pin${pinned ? " active" : ""}`}
                  type="button"
                  onClick={() => void onSetPinned(!pinned)}
                  title={pinned ? "Unpin session" : "Pin session"}
                  aria-label={pinned ? "Unpin session" : "Pin session"}
                  aria-pressed={pinned}
                >
                  {pinned ? "★" : "☆"}
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
      {/* cwd / transport / source frame a task session; for the persistent
          assistant they're noise — keep just which backend + model answers. */}
      {!assistant ? (
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
      ) : null}
      <div className="session-header-tags">
        <span className={`badge ${headerAgent}`}>
          {humaniseBackend(headerAgent, catalog)}
        </span>
        {!assistant && showHeaderTransport ? (
          <span className={`badge transport ${session.transport}`}>
            {transportLabel(session.transport, catalog)}
          </span>
        ) : null}
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
        {!assistant ? (
          <span className="session-header-meta">
            {sourceLabel}
            {typeof session.transport_state?.thread_id === "string"
              ? ` · ${session.transport_state.thread_id}`
              : null}
            {" · "}
            <SessionIdCopy id={session.id} />
          </span>
        ) : null}
      </div>
    </header>
  );
}

// The assistant's two value props — host Q&A and session management — are
// invisible in a blank chat, so the empty thread leads with tappable example
// prompts that send on tap.
const ASSISTANT_EXAMPLE_PROMPTS = [
  "List my Waypoint sessions and their status",
  "What's using CPU and memory on this host right now?",
  "Summarize what my running agents are working on",
  "Which sessions are idle or exited?",
];

function AssistantWelcome({
  onPick,
}: {
  onPick: (text: string) => void | Promise<unknown>;
}) {
  return (
    <div className="assistant-welcome">
      <span className="assistant-welcome-glyph" aria-hidden="true">
        ✦
      </span>
      <p className="assistant-welcome-title">Ask your assistant</p>
      <p className="assistant-welcome-sub">
        It can answer questions about this host and inspect or manage your
        Waypoint sessions. Try one:
      </p>
      <div className="assistant-welcome-prompts">
        {ASSISTANT_EXAMPLE_PROMPTS.map((prompt) => (
          <button
            key={prompt}
            type="button"
            className="assistant-welcome-prompt"
            onClick={() => void onPick(prompt)}
          >
            {prompt}
          </button>
        ))}
      </div>
    </div>
  );
}

// Match a Waypoint control command (e.g. ``/new``) and return its trailing
// argument string, or null when the text isn't that command. The trailing
// space requirement keeps ``/news`` from being read as ``/new``.
function matchControlCommand(text: string, name: string): string | null {
  const command = `/${name}`;
  if (text === command) return "";
  if (text.startsWith(`${command} `)) return text.slice(command.length + 1).trim();
  return null;
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
