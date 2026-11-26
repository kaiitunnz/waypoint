"use client";

import { useRouter } from "next/navigation";
import {
  ChangeEvent,
  ClipboardEvent,
  KeyboardEvent,
  memo,
  startTransition,
  useCallback,
  useDeferredValue,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  approvePlan,
  approveSession,
  cancelHeldMessages,
  connectSessionSocket,
  connectTerminalSocket,
  createMessageSchedule,
  deleteSession as deleteSessionRequest,
  fetchEvents,
  fetchSession,
  isAuthError,
  postAction,
  refreshSessionRateLimitUsage,
  releaseHeldMessages,
  resolveWorkspacePath,
  sendInput,
  setSessionFocus,
  setTaskDockDismissed,
  setSessionPinned,
  setSessionTitle,
} from "@waypoint/web-core/api";
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
  supportsPlanApproval,
  supportsStructuredApproval,
  terminalInteractive,
  terminalKeyInjection,
  terminalResizable,
  transportLabel,
  transportPresentation,
  useBackendCatalog,
} from "@waypoint/web-core/backends";
import { copyTextSync } from "@waypoint/web-core/clipboard";
import { parseHeldMessagesFrame } from "@waypoint/web-core/session/messageQueue";
import { modelNoticeFromEvent, type ModelNotice } from "@waypoint/web-core/session/modelNotice";
import {
  applySideQuestionFrame,
  EMPTY_SIDE_QUESTIONS,
  type SideQuestionFrame,
  type SideQuestionState,
} from "@waypoint/web-core/session/sideQuestions";
import { clearToken } from "@waypoint/web-core/store";
import { type TerminalSubmitResult } from "@/lib/composer";
import { isModifiedEnterShortcut } from "@waypoint/web-core/keyboard";
import { useCommandCompletions } from "@waypoint/web-core/composer-completions";
import { useHoldMenu } from "@waypoint/web-core/hooks/useHoldMenu";
import { useSessionModels } from "@waypoint/web-core/hooks/useSessionModels";
import { useSessionTuning } from "@waypoint/web-core/hooks/useSessionTuning";
import { useTuningDraft } from "@waypoint/web-core/hooks/useTuningDraft";
import { canFork, composerAvailability, isExitedSession } from "@waypoint/web-core/session/send";
import { deliverInput, runControlCommand } from "@waypoint/web-core/sessionDelivery";
import { SEND_MODES, type SendMode } from "@waypoint/web-core/session/sendModes";
import {
  interruptPrompt,
  nextModeId,
  pendingRestartLabel,
  restartFields,
  type RestartFields,
  tuningOptions,
  tuningSummary,
} from "@waypoint/web-core/session/tuning";
import { useFileMentions } from "@waypoint/web-core/hooks/use-file-mentions";
import { useCopied } from "@waypoint/web-core/hooks/use-copied";
import {
  isTaskNotificationEvent,
  parseTaskNotification,
  planForEvent,
  type PlanDecision,
  type PlanViewModel,
  type TaskNotificationKind,
} from "@waypoint/web-core/events";
import { approvalPager, findPendingApprovals, stepApprovalPage } from "@waypoint/web-core/session/approvals";
import {
  connectionLabel,
  connectionTitle,
  isAgentBusy,
  reconnectDelay,
  type ConnectionState,
} from "@waypoint/web-core/session/connection";
import { isImportantEvent } from "@waypoint/web-core/session/importance";
import { toolNamesOfRun } from "@waypoint/web-core/session/toolRun";
import { wrapBracketedPaste } from "@waypoint/web-core/session/terminalText";
import { formatCwdSegments } from "@waypoint/web-core/workspacePaths";
import {
  buildTranscriptItems,
  collapseSupersededPlanEvents,
  foldOlderEvents,
  latestActionablePlanEvent,
  mergeEvents,
  minRawSequence,
  removeConfirmedOptimisticMessages,
  sanitizeEvent,
} from "@waypoint/web-core/session/transcript";
import { useSessionScheduledMessages } from "@waypoint/web-core/hooks/useSessionScheduledMessages";
import { useSessionPresence } from "@waypoint/web-core/hooks/useSessionPresence";
import { formatRelativeTime } from "@waypoint/web-core/usage";
import { AttachmentContextProvider, AttachmentTray, PaperclipIcon } from "@/components/AttachmentTray";
import { filesFromDataTransfer } from "@waypoint/web-core/fileDrop";
import { useAttachments } from "@waypoint/web-core/hooks/useAttachments";
import { FileDropZone, useFileDropTarget } from "@/components/FileDrop";
import { AccountProfilePicker } from "@/components/AccountProfilePicker";
import { ScheduleMessageModal } from "@/components/ScheduleMessageModal";
import { FocusPill } from "@/components/FocusPill";
import { HeldMessagesDock } from "@/components/HeldMessagesDock";
import { ScheduledMessagesDock } from "@/components/ScheduledMessagesDock";
import { SessionFilesPanel } from "@/components/SessionFilesPanel";
import { SessionSettingsModal } from "@/components/SessionSettingsModal";
import { WorkspaceFilesPanel } from "@/components/WorkspaceFilesPanel";
import {
  WorkspaceFileLinkProvider,
  type WorkspaceLinkHandler,
} from "@/components/WorkspaceFileLinkContext";
import { SessionFilesLinkProvider } from "@/components/SessionFilesLinkContext";
import { SessionTerminalView } from "@/components/SessionTerminalView";
import { SessionUsagePill } from "@/components/SessionUsagePill";
import { CommandSuggestions } from "@/components/CommandSuggestions";
import { FileMentions } from "@/components/FileMentions";
import { type XTerminalHandle } from "@/components/XTerminal";
import { ApprovalRequestCard, PlanApprovalCard } from "@/components/ApprovalCard";
import { AssistantMark } from "@/components/AssistantMark";
import {
  PendingUserInputCard,
  TranscriptCard,
  ToolCallRunGroup,
  TaskNotificationRunGroup,
} from "@/components/TranscriptCard";
import {
  AskQuestionContext,
  usePendingQuestionSnapshot,
} from "@/components/AskQuestion";
import { useAskQuestionState } from "@waypoint/web-core/hooks/useAskQuestionState";
import { PendingQuestionDock } from "@/components/PendingQuestionDock";
import { TaskProgressDock } from "@/components/TaskProgressDock";
import { SideQuestionDock } from "@/components/SideQuestionDock";
import {
  currentTodoEvent,
  dismissedTaskSequence,
  isTaskProgressDismissed,
  readTodoEntries,
  summarizeTodos,
  type PendingTaskDismissal,
} from "@waypoint/web-core/todos";
import {
  effortLabel,
  formatResolvedModelLabel,
  modelLabelFor,
} from "@waypoint/web-core/modelDisplay";
import { useSwitcher } from "@/components/SwitcherProvider";
import {
  AccountProfile,
  Backend,
  BackendDescriptor,
  BackendModelOption,
  BackendPermissionMode,
  EventRecord,
  HeldMessage,
  PendingQuestion,
  PendingQuestionsSnapshot,
  SessionCommandInvocation,
  SessionEnvelope,
  SessionRecord,
  SessionTransport,
} from "@waypoint/web-core/types";

// The session id is otherwise only readable from the URL — invisible in the
// installed PWA where the address bar is hidden. This puts a quiet,
// click-to-copy copy of it in the header meta row for the human operator (who
// occasionally needs the raw id for a `waypoint sessions …` call or to hand to
// an agent). Copies the full id even though the display truncates it.
function SessionIdCopy({ id }: { id: string }) {
  const { copied, markCopied } = useCopied();
  return (
    <button
      type="button"
      className={`session-id-copy${copied ? " copied" : ""}`}
      title={copied ? "Copied" : `Copy session id: ${id}`}
      aria-label={copied ? "Session id copied" : `Copy session id ${id}`}
      onClick={(event) => {
        event.stopPropagation();
        if (copyTextSync(id)) markCopied();
      }}
    >
      <span className="session-id-copy-key">id</span>
      <span className="session-id-copy-value">{id}</span>
      <span aria-hidden>{copied ? "✓" : "⎘"}</span>
    </button>
  );
}

function SessionCwdCopy({ cwd }: { cwd: string }) {
  const { copied, markCopied } = useCopied();
  return (
    <button
      type="button"
      className={`session-cwd-copy${copied ? " copied" : ""}`}
      title={copied ? "Copied" : `Copy working directory: ${cwd}`}
      aria-label={copied ? "Working directory copied" : `Copy working directory ${cwd}`}
      onClick={(event) => {
        event.stopPropagation();
        if (copyTextSync(cwd)) markCopied();
      }}
    >
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
  accountProfilesFor: (backend: Backend) => AccountProfile[];
  supportsReattach: boolean;
  // Rebuild the assistant on the chosen agent and transport. Changing either
  // starts a fresh thread, since the transport is fixed at launch.
  onSwitchBackend: (
    backend: Backend,
    transport: SessionTransport,
    accountProfileId: string | null,
  ) => Promise<void> | void;
  onAttachThread: (
    backend: Backend,
    threadId: string,
    accountProfileId: string | null,
  ) => Promise<void> | void;
  onClearContext: () => Promise<void> | void;
  onTerminate: () => Promise<void> | void;
  onReattach: () => Promise<void> | void;
  // Lists importable threads for a backend; the caller surfaces discovery
  // failures in the composer settings popover.
  listThreads: (
    backend: Backend,
    accountProfileId: string | null,
  ) => Promise<AssistantThreadOption[]>;
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

// The composer sticks to the viewport bottom; floating scroll affordances
// read `--composer-height` to sit just above it. The fallback keeps things
// sensible for the very first paint before the observer fires.
const COMPOSER_HEIGHT_FALLBACK = 220;

export function SessionDetail({ host, token, sessionId, onAuthFailure, assistant = false, assistantControls = null }: SessionDetailProps) {
  const router = useRouter();
  const catalog = useBackendCatalog(host || null, token || null, null);
  const attachmentContext = useMemo(
    () => ({ host, token, sessionId }),
    [host, token, sessionId],
  );
  const [session, setSession] = useState<SessionRecord | null>(null);
  const [events, setEvents] = useState<EventRecord[]>([]);
  // The session's latest todo/task snapshot as of the initial load, carried on
  // the tail events page. It keeps the task dock visible when the most recent
  // todo predates the loaded transcript window; live/newer todos arrive through
  // `events` and win the max-sequence comparison in `currentTaskEvent`.
  const [loadedTodoEvent, setLoadedTodoEvent] = useState<EventRecord | null>(null);
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  // Owned here so a transcript SendUserFile card can open the Files browser the
  // composer renders.
  const [filesOpen, setFilesOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [workspaceInitialPath, setWorkspaceInitialPath] = useState<string | undefined>(undefined);
  const [workspaceInitialDir, setWorkspaceInitialDir] = useState<string | undefined>(undefined);
  // Bumped on every open request so the panel re-reveals even when the target
  // path is unchanged; the ref is the source of truth for discarding the
  // results of out-of-order resolve() calls.
  const [workspaceRevealSeq, setWorkspaceRevealSeq] = useState(0);
  const workspaceRequestRef = useRef(0);
  // Persisted width of the docked workspace panel (px). The dock is a left
  // side-panel that coexists with the session rather than a blocking modal.
  const [dockWidth, setDockWidth] = useState<number>(() => {
    if (typeof window === "undefined") return 400;
    const stored = Number(window.localStorage.getItem("waypoint.workspaceDockWidth"));
    return Number.isFinite(stored) && stored >= 300 ? stored : 400;
  });
  // innerWidth matches the CSS width media queries during a desktop resize.
  const [viewportWidth, setViewportWidth] = useState<number>(() =>
    typeof window === "undefined" ? Infinity : window.innerWidth,
  );
  useEffect(() => {
    const onResize = () => setViewportWidth(window.innerWidth);
    onResize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  // Equality stays side-dock: a dock exactly viewport-wide still contains its
  // right-inset close button.
  const dockSheet = viewportWidth <= 640 || dockWidth > viewportWidth;
  useEffect(() => {
    sideQuestionStateRef.current = EMPTY_SIDE_QUESTIONS;
    setSideQuestions(EMPTY_SIDE_QUESTIONS);
    setSqExpanded(false);
    setHeldMessages([]);
  }, [sessionId]);
  const [view, setView] = useState<ViewMode>("chat");
  const [filterMode, setFilterMode] = useState<FilterMode>("important");
  const [toolRunsExpanded, setToolRunsExpanded] = useState(false);
  const [error, setError] = useState("");
  // Notice from a live model.change event; cleared when resolved_model clears.
  const [modelNotice, setModelNotice] = useState<ModelNotice | null>(null);
  const resolvedModel = session?.resolved_model ?? null;
  useEffect(() => {
    // resolved_model clearing (relaunch or model change) dismisses the notice.
    if (!resolvedModel) {
      setModelNotice(null);
    }
  }, [resolvedModel]);
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
  const [sideQuestions, setSideQuestions] = useState<SideQuestionState>(EMPTY_SIDE_QUESTIONS);
  // The socket handler reads the latest state here, so it applies each frame once.
  const sideQuestionStateRef = useRef<SideQuestionState>(EMPTY_SIDE_QUESTIONS);
  const [heldMessages, setHeldMessages] = useState<HeldMessage[]>([]);
  const { snapshot: pendingQuestions, applySnapshot: applyPendingQuestions } =
    usePendingQuestionSnapshot(sessionId);
  const [revealTarget, setRevealTarget] = useState<{
    toolUseId: string;
    pagesLeft: number;
  } | null>(null);
  useEffect(() => {
    setRevealTarget(null);
  }, [sessionId]);
  const [focusBusy, setFocusBusy] = useState(false);
  // An in-flight dismiss/show wins over the session record until its request
  // settles; otherwise a session_state push read before the write commits
  // would flash the dock back.
  const [pendingTaskDock, setPendingTaskDock] = useState<PendingTaskDismissal | null>(null);
  const taskDockRequestRef = useRef(0);
  // The dock expands when a live (non-hydrated) side-question first arrives.
  const [sqExpanded, setSqExpanded] = useState(false);
  useEffect(() => {
    if (sideQuestions.items.size === 0) setSqExpanded(false);
  }, [sideQuestions.items.size]);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const [showScrollToTop, setShowScrollToTop] = useState(false);
  const [rateLimitRefreshBusy, setRateLimitRefreshBusy] = useState(false);
  const [approvalPageIndex, setApprovalPageIndex] = useState(0);
  const [hasOlderEvents, setHasOlderEvents] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const { openSwitcher, setCurrentSession } = useSwitcher();
  const scheduledMessages = useSessionScheduledMessages(host, token, sessionId);
  // Advertise this visible tab so the backend skips notifications for a session
  // the user is already looking at.
  useSessionPresence(host, token, sessionId);
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
    { tempId: string; text: string; ts: string }[]
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
  const confirmInterrupt = useCallback((field: string) => window.confirm(interruptPrompt(field)), []);
  const {
    busy: tuningBusy,
    changeMode: handlePermissionModeChange,
    changeModel: handleModelChange,
    changeEffort: handleEffortChange,
  } = useSessionTuning({
    host,
    token,
    session,
    catalog,
    onSession: setSession,
    onError: setError,
    onAuthFailure: handleAuthFailure,
    confirmInterrupt,
  });
  const { mode: modeBusy, model: modelBusy, effort: effortBusy } = tuningBusy;
  const { modelOptions, effortLevels, defaultModelId, defaultModelLabel, defaultEffort } = useSessionModels(
    host,
    token,
    session,
    handleAuthFailure,
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
    startTransition(() => {
      if (confirmedUserTexts.length > 0) {
        setOptimisticMessages((prev) =>
          removeConfirmedOptimisticMessages(prev, confirmedUserTexts),
        );
      }
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
        setLoadedTodoEvent(
          loadedPage.latest_todo ? sanitizeEvent(loadedPage.latest_todo) : null,
        );
        if (loadedPage.pending_questions) {
          applyPendingQuestions(loadedPage.pending_questions, false);
        }
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
      let questionsHydrated = false;
      setConnection(attempt === 0 ? "connecting" : "reconnecting");
      socket = connectSessionSocket(
        host,
        token,
        sessionId,
        (message: SessionEnvelope) => {
          if (message.type === "event") {
            const event = sanitizeEvent(message.payload.event as EventRecord);
            // Live only: a replayed model.change must not re-pop the notice.
            const notice = modelNoticeFromEvent(event);
            if (notice) setModelNotice(notice);
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
          if (message.type === "side_question") {
            const { state, expand } = applySideQuestionFrame(
              sideQuestionStateRef.current,
              message.payload as SideQuestionFrame,
            );
            sideQuestionStateRef.current = state;
            setSideQuestions(state);
            if (expand) setSqExpanded(true);
          }
          if (message.type === "pending_questions") {
            applyPendingQuestions(
              message.payload as unknown as PendingQuestionsSnapshot,
              !questionsHydrated,
            );
            questionsHydrated = true;
          }
          if (message.type === "held_messages") {
            const held = parseHeldMessagesFrame(message.payload, sessionId);
            if (held) setHeldMessages(held);
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
      if (flushFrameRef.current !== null) {
        window.cancelAnimationFrame(flushFrameRef.current);
        flushFrameRef.current = null;
      }
      pendingEventsRef.current = [];
      socket?.close();
    };
  }, [
    applyPendingQuestions,
    handleAuthFailure,
    host,
    token,
    sessionId,
    queueIncomingEvent,
  ]);

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

  const onSendWithOptimistic = useCallback(
    async (
      text: string,
      command?: SessionCommandInvocation,
      attachments?: string[],
    ) => {
      if (!text.trim() && !attachments?.length) return false;
      const tempId = Math.random().toString(36).slice(2);
      const ts = new Date().toISOString();
      setOptimisticMessages((prev) => [...prev, { tempId, text, ts }]);
      try {
        // The persistent assistant isn't a launchpad, so `/new` and `/fork`
        // flow to it as text.
        const { openSessionId } = await deliverInput({
          host,
          token,
          sessionId,
          session,
          catalog,
          text,
          command,
          attachments,
          controlCommands: !assistant,
        });
        if (openSessionId) router.push(`/session/${openSessionId}`);
        return true;
      } catch (sendError) {
        if (isAuthError(sendError)) {
          handleAuthFailure();
          return false;
        }
        setError(sendError instanceof Error ? sendError.message : "failed to send input");
        return false;
      } finally {
        setOptimisticMessages((prev) => prev.filter((m) => m.tempId !== tempId));
      }
    },
    [
      session,
      catalog,
      host,
      token,
      sessionId,
      assistant,
      router,
      handleAuthFailure,
    ],
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

  const handleSetFocus = useCallback(
    async (enabled: boolean) => {
      setFocusBusy(true);
      try {
        const updated = await setSessionFocus(host, token, sessionId, enabled);
        setSession(updated);
      } catch (focusError) {
        if (isAuthError(focusError)) {
          handleAuthFailure();
          return;
        }
        setError(focusError instanceof Error ? focusError.message : "failed to update Focus");
      } finally {
        setFocusBusy(false);
      }
    },
    [host, token, sessionId, handleAuthFailure],
  );

  const handleTaskDockDismissal = useCallback(
    async (sequence: number | null) => {
      const request = ++taskDockRequestRef.current;
      setPendingTaskDock({ sessionId, sequence });
      try {
        const updated = await setTaskDockDismissed(host, token, sessionId, sequence);
        if (request === taskDockRequestRef.current) {
          setSession(updated);
        }
      } catch (dockError) {
        if (isAuthError(dockError)) {
          handleAuthFailure();
          return;
        }
        setError(
          dockError instanceof Error ? dockError.message : "failed to update task progress",
        );
      } finally {
        if (request === taskDockRequestRef.current) {
          setPendingTaskDock(null);
        }
      }
    },
    [host, token, sessionId, handleAuthFailure],
  );

  const runHeldAction = useCallback(
    async (action: () => Promise<void>) => {
      try {
        await action();
      } catch (heldError) {
        if (isAuthError(heldError)) {
          handleAuthFailure();
          return;
        }
        setError(heldError instanceof Error ? heldError.message : "held message action failed");
      }
    },
    [handleAuthFailure],
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

  const pendingApprovals = useMemo(
    () =>
      session && supportsStructuredApproval(session.transport, catalog)
        ? findPendingApprovals(events)
        : [],
    [session, catalog, events],
  );
  const {
    count: approvalCount,
    page: safeApprovalPage,
    current: pendingApproval,
  } = approvalPager(pendingApprovals, approvalPageIndex);
  const agentBusy = session ? isAgentBusy(session, connection) : false;
  const displayEvents = useMemo(
    () => collapseSupersededPlanEvents(renderedEvents),
    [renderedEvents],
  );
  const visibleEvents = useMemo(
    () =>
      filterMode === "all"
        ? displayEvents
        : displayEvents.filter(isImportantEvent),
    [filterMode, displayEvents],
  );
  const hiddenEventCount = displayEvents.length - visibleEvents.length;
  const pendingPlanApprovalEvent = useMemo(
    () =>
      session?.permission_mode === "plan" &&
      supportsPlanApproval(session.backend, catalog)
        ? latestActionablePlanEvent(displayEvents)
        : null,
    [session, catalog, displayEvents],
  );
  const pendingPlanApprovalView: PlanViewModel | null = useMemo(
    () => (pendingPlanApprovalEvent ? planForEvent(pendingPlanApprovalEvent) : null),
    [pendingPlanApprovalEvent],
  );
  const transcriptEventsForDisplay = useMemo(
    () =>
      pendingPlanApprovalEvent
        ? visibleEvents.filter((event) => event !== pendingPlanApprovalEvent)
        : visibleEvents,
    [pendingPlanApprovalEvent, visibleEvents],
  );
  const transcriptItems = useMemo(
    () => buildTranscriptItems(transcriptEventsForDisplay),
    [transcriptEventsForDisplay],
  );
  const hasToolRuns = useMemo(
    () =>
      transcriptItems.some(
        (item) => item.kind === "tool_run" || item.kind === "notification_run",
      ),
    [transcriptItems],
  );
  const workspacePreviewEnabled = Boolean(session);
  // Latest task group, read off the raw event stream so the dock reflects the
  // true current state regardless of the transcript's event filter. Fall back
  // to `loadedTodoEvent` (the tail-page snapshot) when the latest todo predates
  // the loaded window; the higher sequence wins so a live update always beats
  // the load-time snapshot.
  const currentTaskEvent = useMemo(
    () => currentTodoEvent(events, loadedTodoEvent),
    [events, loadedTodoEvent],
  );
  const taskProgress = useMemo(
    () => summarizeTodos(readTodoEntries(currentTaskEvent)),
    [currentTaskEvent],
  );
  const sessionExited = isExitedSession(session);
  // The transport renders a live xterm pane (and locks the chat composer into
  // terminal mode) rather than a structured transcript.
  const terminalOnly = Boolean(session && liveTerminal(session.transport, catalog));
  const { disabled: composerDisabled, dormant: dormantReattach } = composerAvailability(session, catalog);

  const reportAskRequestError = useCallback(
    (requestError: unknown, fallback: string) => {
      if (isAuthError(requestError)) {
        handleAuthFailure();
        return;
      }
      setError(requestError instanceof Error ? requestError.message : fallback);
    },
    [handleAuthFailure],
  );
  const reattachForQuestion = useCallback(() => void reattach(), [reattach]);
  const askController = useAskQuestionState({
    host,
    token,
    sessionId,
    snapshot: pendingQuestions,
    events: displayEvents,
    canReattach: dormantReattach,
    reattach: reattachForQuestion,
    onRequestError: reportAskRequestError,
  });

  const revealQuestion = useCallback((question: PendingQuestion) => {
    setView("chat");
    setRevealTarget({ toolUseId: question.tool_use_id, pagesLeft: 50 });
  }, []);

  // Show in transcript: page older messages until the question's card is
  // loaded, then wait for it to render and scroll it into view.
  useEffect(() => {
    if (!revealTarget) return;
    const { toolUseId } = revealTarget;
    const loaded = events.some(
      (event) => event.kind === "tool_call" && event.metadata?.tool_use_id === toolUseId,
    );
    if (!loaded) {
      if (loadingOlder) return;
      if (!hasOlderEvents || revealTarget.pagesLeft <= 0) {
        setRevealTarget(null);
        return;
      }
      setRevealTarget({ toolUseId, pagesLeft: revealTarget.pagesLeft - 1 });
      void loadOlderEvents();
      return;
    }
    let frame = 0;
    let attempts = 0;
    const tryScroll = () => {
      const card = document.querySelector<HTMLElement>(
        `[data-ask-tool-use-id="${CSS.escape(toolUseId)}"]`,
      );
      if (card) {
        // Leave stick-to-bottom so the reflow re-anchor doesn't snap the view
        // back down.
        nearBottomRef.current = false;
        card.scrollIntoView({ block: "center", behavior: "auto" });
        card.classList.remove("ask-question-flash");
        void card.offsetWidth;
        card.classList.add("ask-question-flash");
        window.setTimeout(() => card.classList.remove("ask-question-flash"), 2000);
        setRevealTarget(null);
        return;
      }
      attempts += 1;
      if (attempts > 60) {
        setRevealTarget(null);
        return;
      }
      frame = window.requestAnimationFrame(tryScroll);
    };
    // Two frames: let loadOlderEvents re-anchor its scroll first.
    frame = window.requestAnimationFrame(() => {
      frame = window.requestAnimationFrame(tryScroll);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [revealTarget, events, hasOlderEvents, loadingOlder, loadOlderEvents]);
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
  // Whether the pane accepts key-bar / scroll-wheel injection (true for the
  // fully interactive tmux pane and the claude_tty escape hatch).
  const canInjectKeys = session
    ? terminalKeyInjection(session.transport, catalog)
    : false;
  const [unlockedSessionId, setUnlockedSessionId] = useState<string | null>(null);
  if (unlockedSessionId !== null && unlockedSessionId !== sessionId) {
    setUnlockedSessionId(null);
  }
  const paneUnlocked =
    canInjectKeys && !canTerminalInteract && unlockedSessionId === sessionId;
  const activeView: ViewMode = terminalOnly
    ? "terminal"
    : !canShowTerminal
      ? "chat"
      : view;
  const taskDockDismissed = isTaskProgressDismissed(
    currentTaskEvent,
    dismissedTaskSequence(pendingTaskDock, sessionId, session?.task_dock_dismissed_sequence),
  );
  const showTaskDock =
    activeView === "chat" &&
    taskProgress !== null &&
    !taskDockDismissed;
  const canShowTasks =
    activeView === "chat" && taskProgress !== null && taskDockDismissed;
  const terminalRef = useRef<XTerminalHandle | null>(null);
  const terminalSocketRef = useRef<WebSocket | null>(null);
  // Bumped to reconnect the terminal WS: on EXITED → live transitions and
  // when the pane target (tmux_pane) changes under a running session.
  const [terminalEpoch, setTerminalEpoch] = useState(0);
  // The light/dark surface the connected pane should show, resolved from the
  // agent's own TUI theme by the server. Connection state, not app state:
  // dark until the appearance frame arrives, reset to dark on each reconnect.
  const [terminalAppearance, setTerminalAppearance] = useState<
    "light" | "dark"
  >("dark");
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
      // Baseline the surface to dark before every (re)connect so a prior light
      // session, transport, or profile can never bleed into a new one — the
      // server re-sends the appearance frame if this connection is light.
      terminalRef.current?.setAppearance("dark");
      setTerminalAppearance("dark");
      const resizable = !!paneTransport && terminalResizable(paneTransport, catalog);
      socket = connectTerminalSocket(host, token, sessionId, {
        onOpen: () => {
          attempt = 0;
          // Opt into terminal protocol v2 with a universal hello so the server
          // sends the appearance frame. Resizable panes carry their viewport so
          // tmux resizes the pane to match; fixed-grid panes (claude_tty) own
          // their geometry server-side and send the hello without dimensions.
          // Read the live ref because the term may not have mounted at
          // connect() time.
          if (socket?.readyState !== WebSocket.OPEN) return;
          const term = terminalRef.current;
          const cols = resizable ? term?.cols() : undefined;
          const rows = resizable ? term?.rows() : undefined;
          socket.send(
            JSON.stringify({
              type: "hello",
              terminal_protocol: 2,
              ...(cols && rows ? { cols, rows } : {}),
              ...(paneUnlocked ? { interactive: true } : {}),
            }),
          );
        },
        onChunk: (text) => {
          terminalRef.current?.write(text);
        },
        onAppearance: (appearance) => {
          // Applied imperatively before the seed write (ordering guarantees the
          // seed renders on the right surface), plus stored so re-renders and
          // the stage background stay consistent.
          terminalRef.current?.setAppearance(appearance);
          setTerminalAppearance(appearance);
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
          const delay = reconnectDelay(attempt);
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
  }, [activeView, paneTransport, catalog, host, token, sessionId, handleAuthFailure, terminalEpoch, paneUnlocked]);

  const toggleInputUnlocked = useCallback(() => {
    const next = !paneUnlocked;
    setUnlockedSessionId(next ? sessionId : null);
    const term = terminalRef.current;
    term?.setInputEnabled(next);
    if (next) term?.focus();
    else term?.blur();
  }, [paneUnlocked, sessionId]);

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
    if (session) {
      try {
        const allowFork = canFork(session, catalog);
        const opened = await runControlCommand({ host, token, sessionId, text, allowFork });
        if (opened !== null) {
          router.push(`/session/${opened}`);
          return "ok";
        }
      } catch (e) {
        if (isAuthError(e)) handleAuthFailure();
        else setError(e instanceof Error ? e.message : "failed to create session");
        return "command-error";
      }
    }
    const socket = terminalSocketRef.current;
    if (socket?.readyState !== WebSocket.OPEN) return "socket-closed";
    socket.send(JSON.stringify({ type: "input_submit", text, submit: true }));
    return "ok";
  }, [session, catalog, host, token, sessionId, router, handleAuthFailure]);

  // Attachment-bearing terminal submits go over HTTP, not the terminal WS:
  // handle_input routes them through the tmux transport, which appends the
  // host file paths to the message so the wrapped CLI can read them.
  const handleTerminalSubmitWithAttachments = useCallback(
    async (text: string, attachmentIds: string[]): Promise<TerminalSubmitResult> => {
      try {
        await sendInput(host, token, sessionId, text, undefined, attachmentIds);
        return "ok";
      } catch (e) {
        if (isAuthError(e)) handleAuthFailure();
        else setError(e instanceof Error ? e.message : "failed to send input");
        return "command-error";
      }
    },
    [host, token, sessionId, handleAuthFailure],
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
      // Synthesize a wheel event as terminal input so the inner TUI scrolls
      // its own view (both tmux and the claude_tty escape hatch accept this).
      // SGR mouse encoding (mode 1006). Button 64 = wheel up, 65 = wheel
      // down. Column/row are the cursor position the inner app attributes the
      // wheel event to; centering on the viewport is a safe stand-in for
      // "user's eye" without coupling to xterm's actual mouse position.
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
  const interruptSession = useCallback(() => runAction("interrupt"), [runAction]);
  const resumeSession = useCallback(() => {
    void runAction("resume");
  }, [runAction]);
  const handleOpenWorkspaceFile = useCallback((path: string) => {
    const seq = ++workspaceRequestRef.current;
    setWorkspaceInitialDir(undefined);
    setWorkspaceInitialPath(path);
    setWorkspaceRevealSeq(seq);
    setWorkspaceOpen(true);
  }, []);
  // Opens the workspace dock at the root.
  const openWorkspaceRoot = useCallback(() => {
    setWorkspaceInitialPath(undefined);
    setWorkspaceInitialDir(undefined);
    setWorkspaceRevealSeq(++workspaceRequestRef.current);
    setWorkspaceOpen(true);
  }, []);
  // Route a clicked transcript path (absolute or ~/-relative) through the
  // backend resolver: files open the preview, directories reveal the tree. If
  // it resolves to nothing in the workspace, explicit links degrade to opening
  // in a new tab while synthesized bare-path links stay inert.
  const openWorkspacePath = useCallback(
    async (href: string, opts?: { fromBareText?: boolean }) => {
      const seq = ++workspaceRequestRef.current;
      try {
        const resolved = await resolveWorkspacePath(host, token, sessionId, href);
        if (workspaceRequestRef.current !== seq) return; // superseded by a newer click
        if (resolved.kind === "dir") {
          setWorkspaceInitialPath(undefined);
          setWorkspaceInitialDir(resolved.path);
        } else {
          setWorkspaceInitialDir(undefined);
          setWorkspaceInitialPath(resolved.path);
        }
        setWorkspaceRevealSeq(seq);
        setWorkspaceOpen(true);
      } catch (e) {
        if (workspaceRequestRef.current !== seq) return;
        if (isAuthError(e)) {
          handleAuthFailure();
          return;
        }
        if (!opts?.fromBareText) {
          window.open(href, "_blank", "noopener,noreferrer");
        }
      }
    },
    [host, token, sessionId, handleAuthFailure],
  );
  const workspaceLink = useMemo<WorkspaceLinkHandler | null>(
    () => (workspacePreviewEnabled ? { openWorkspacePath } : null),
    [workspacePreviewEnabled, openWorkspacePath],
  );
  const filesLink = useMemo(
    () => ({ openFilesBrowser: () => setFilesOpen(true) }),
    [],
  );

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem("waypoint.workspaceDockWidth", String(dockWidth));
  }, [dockWidth]);

  // Drive the layout from the document root: when the dock is open on a wide
  // viewport, `.page-shell` pads left by --wp-dock-width so the session column
  // slides clear of the dock instead of sitting under it. Sheet mode excludes
  // that push via data-wp-dock-mode; useLayoutEffect applies it before paint.
  useLayoutEffect(() => {
    const root = document.documentElement;
    if (workspacePreviewEnabled && workspaceOpen) {
      root.style.setProperty("--wp-dock-width", `${dockWidth}px`);
      root.setAttribute("data-wp-dock", "open");
      if (dockSheet) {
        root.setAttribute("data-wp-dock-mode", "sheet");
      } else {
        root.removeAttribute("data-wp-dock-mode");
      }
    } else {
      root.removeAttribute("data-wp-dock");
      root.removeAttribute("data-wp-dock-mode");
    }
    return () => {
      root.removeAttribute("data-wp-dock");
      root.removeAttribute("data-wp-dock-mode");
    };
  }, [workspacePreviewEnabled, workspaceOpen, dockWidth, dockSheet]);

  const showTerminalPane = Boolean(session) && activeView === "terminal";
  const notices = (
    <>
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
      {modelNotice ? (
        <div className="session-notice-toast" role="status">
          <span className="session-notice-lamp" aria-hidden="true" />
          <span className="session-notice-body">
            {modelNotice.reason === "switch" ? (
              <>
                <span className="session-notice-label">Model changed</span>
                <span className="model-transition">
                  {modelNotice.previous ? (
                    <span className="model-chip prev">
                      {modelLabelFor(modelNotice.previous, modelOptions)}
                    </span>
                  ) : null}
                  <span className="model-transition-arrow" aria-hidden="true">
                    →
                  </span>
                  <span className="model-chip current">
                    {modelLabelFor(modelNotice.current, modelOptions)}
                  </span>
                </span>
              </>
            ) : (
              <>
                <span className="session-notice-label">Running</span>
                <span className="model-transition">
                  <span className="model-chip current">
                    {modelLabelFor(modelNotice.current, modelOptions)}
                  </span>
                  {modelNotice.selection ? (
                    <span className="model-transition-note">
                      selected {modelLabelFor(modelNotice.selection, modelOptions)}
                    </span>
                  ) : null}
                </span>
              </>
            )}
          </span>
          <button
            type="button"
            className="session-notice-dismiss"
            onClick={() => setModelNotice(null)}
            aria-label="Dismiss notice"
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
      {pendingQuestions && pendingQuestions.questions.length > 0 ? (
        <PendingQuestionDock
          questions={pendingQuestions.questions}
          onReveal={revealQuestion}
        />
      ) : null}
      {sideQuestions.items.size > 0 ? (
        <SideQuestionDock
          questions={[...sideQuestions.items.values()]}
          host={host}
          token={token}
          sessionId={sessionId}
          expanded={sqExpanded}
          onExpandedChange={setSqExpanded}
        />
      ) : null}
      {showTaskDock && taskProgress ? (
        <TaskProgressDock
          progress={taskProgress}
          onDismiss={() => {
            if (currentTaskEvent) {
              void handleTaskDockDismissal(currentTaskEvent.sequence);
            }
          }}
        />
      ) : null}
      {heldMessages.length ? (
        <HeldMessagesDock
          focus={Boolean(session?.focus)}
          messages={heldMessages}
          onRelease={(heldId) =>
            runHeldAction(() => releaseHeldMessages(host, token, { heldId }))
          }
          onCancel={(heldId) =>
            runHeldAction(() => cancelHeldMessages(host, token, { heldId }))
          }
          onReleaseAll={() =>
            runHeldAction(() => releaseHeldMessages(host, token, { sessionId }))
          }
          onCancelAll={() =>
            runHeldAction(() => cancelHeldMessages(host, token, { sessionId }))
          }
        />
      ) : null}
      {session ? (
        <ScheduledMessagesDock
          messages={scheduledMessages.messages}
          onCancel={scheduledMessages.cancel}
        />
      ) : null}
    </>
  );

  return (
    <AskQuestionContext.Provider value={askController}>
    <WorkspaceFileLinkProvider value={workspaceLink}>
    <SessionFilesLinkProvider value={filesLink}>
    <FileDropZone containerRef={sectionRef} title={session?.title}>
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
                  const toolNames = toolNamesOfRun(item.items);
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
                            onOpenWorkspaceFile={workspacePreviewEnabled ? handleOpenWorkspaceFile : undefined}
                            key={`pair-${child.pair.itemId}`}
                          />
                        ) : (
                          <TranscriptCard
                            event={child.event}
                            transport={session.transport}
                            catalog={catalog}
                            modelOptions={modelOptions}
                            onOpenWorkspaceFile={workspacePreviewEnabled ? handleOpenWorkspaceFile : undefined}
                            key={`${child.event.sequence}-${child.event.id ?? "local"}`}
                          />
                        ),
                      )}
                    </ToolCallRunGroup>
                  );
                }
                if (item.kind === "notification_run") {
                  const kinds = item.items
                    .map((child): TaskNotificationKind | null => {
                      const event = child.kind === "pair" ? child.pair.call ?? child.pair.result : child.event;
                      // Count only notification cards, not absorbed lifecycle
                      // noise; a malformed one still counts as "unknown".
                      if (!event || !isTaskNotificationEvent(event)) return null;
                      return parseTaskNotification(event)?.kind ?? "unknown";
                    })
                    .filter((kind): kind is TaskNotificationKind => kind !== null);
                  return (
                    <TaskNotificationRunGroup
                      key={`notification-run-${index}`}
                      kinds={kinds}
                      initiallyOpen={toolRunsExpanded || filterMode === "all"}
                    >
                      {item.items.map((child) =>
                        child.kind === "pair" ? (
                          <TranscriptCard
                            event={child.pair.call ?? child.pair.result ?? child.event}
                            pair={child.pair}
                            transport={session.transport}
                            catalog={catalog}
                            onOpenWorkspaceFile={workspacePreviewEnabled ? handleOpenWorkspaceFile : undefined}
                            key={`pair-${child.pair.itemId}`}
                          />
                        ) : (
                          <TranscriptCard
                            event={child.event}
                            transport={session.transport}
                            catalog={catalog}
                            modelOptions={modelOptions}
                            onOpenWorkspaceFile={workspacePreviewEnabled ? handleOpenWorkspaceFile : undefined}
                            key={`${child.event.sequence}-${child.event.id ?? "local"}`}
                          />
                        ),
                      )}
                    </TaskNotificationRunGroup>
                  );
                }
                return item.kind === "pair" ? (
                  <TranscriptCard
                    event={item.pair.call ?? item.pair.result ?? item.event}
                    pair={item.pair}
                    transport={session.transport}
                    catalog={catalog}
                    onOpenWorkspaceFile={workspacePreviewEnabled ? handleOpenWorkspaceFile : undefined}
                    key={`pair-${item.pair.itemId}`}
                  />
                ) : (
                  <TranscriptCard
                    event={item.event}
                    transport={session.transport}
                    catalog={catalog}
                    modelOptions={modelOptions}
                    onOpenWorkspaceFile={workspacePreviewEnabled ? handleOpenWorkspaceFile : undefined}
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
                />
              ))
            : null}
        </section>
        </AttachmentContextProvider>
      ) : null}
      {showTerminalPane ? (
        <SessionTerminalView
          host={host}
          token={token}
          sessionId={sessionId}
          session={session}
          interactive={canTerminalInteract}
          keyInjection={canInjectKeys}
          inputUnlocked={paneUnlocked}
          onToggleInputUnlocked={toggleInputUnlocked}
          focus={Boolean(session?.focus)}
          focusBusy={focusBusy}
          onFocusChange={handleSetFocus}
          terminalRef={terminalRef}
          terminalDims={terminalDims}
          terminalAppearance={terminalAppearance}
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
          workspacePreviewEnabled={workspacePreviewEnabled}
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
          onOpenSettings={() => setSettingsOpen(true)}
          onBrowseWorkspace={openWorkspaceRoot}
          onScheduled={scheduledMessages.refresh}
          onError={setError}
          notices={notices}
        />
      ) : null}
      {!terminalOnly && pendingApproval ? (
        <>
          {approvalCount > 1 ? (
            <div className="approval-pager">
              <button
                type="button"
                className="approval-pager-btn"
                onClick={() => setApprovalPageIndex((i) => stepApprovalPage(i, "prev", approvalCount))}
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
                onClick={() => setApprovalPageIndex((i) => stepApprovalPage(i, "next", approvalCount))}
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
      {showTerminalPane ? null : notices}
      {session && !terminalOnly ? (
        <ReplyComposer
          host={host}
          token={token}
          sessionId={sessionId}
          session={session}
          onScheduled={scheduledMessages.refresh}
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
          effortLevels={effortLevels}
          defaultModelId={defaultModelId}
          defaultModelLabel={defaultModelLabel}
          defaultEffort={defaultEffort}
          currentModel={session?.model ?? null}
          currentEffort={session?.effort ?? null}
          effortBusy={effortBusy}
          focus={Boolean(session?.focus)}
          focusBusy={focusBusy}
          onFocusChange={handleSetFocus}
          permissionMode={session?.permission_mode ?? null}
          transport={session?.transport ?? null}
          catalog={catalog}
          restart={restartFields(session, catalog)}
          hasToolRuns={hasToolRuns}
          toolRunsExpanded={toolRunsExpanded}
          onToggleToolRuns={() => {
            const next = !toolRunsExpanded;
            document.querySelectorAll<HTMLDetailsElement>("details.tool-call-run").forEach((el) => { el.open = next; });
            setToolRunsExpanded(next);
          }}
          canShowTasks={canShowTasks}
          onShowTasks={() => void handleTaskDockDismissal(null)}
          onDelete={removeFromList}
          onInterrupt={interruptSession}
          onModeChange={handlePermissionModeChange}
          onModelChange={handleModelChange}
          onEffortChange={handleEffortChange}
          onRefresh={refresh}
          onRateLimitRefresh={handleRateLimitRefresh}
          onReattach={reattach}
          onSwitchSession={openSwitcher}
          onOpenSettings={() => setSettingsOpen(true)}
          onSend={onSendWithOptimistic}
          attachmentsEnabled={
            session ? supportsAttachments(session.backend, catalog) : false
          }
          onTerminate={terminate}
          onError={setError}
          assistant={assistant}
          assistantControls={assistantControls}
          workspacePreviewEnabled={workspacePreviewEnabled}
          onBrowseWorkspace={openWorkspaceRoot}
          filesOpen={filesOpen}
          onFilesOpenChange={setFilesOpen}
        />
      ) : null}
      {workspacePreviewEnabled ? (
        <WorkspaceFilesPanel
          host={host}
          token={token}
          sessionId={sessionId}
          open={workspaceOpen}
          initialPath={workspaceInitialPath}
          initialDir={workspaceInitialDir}
          revealSeq={workspaceRevealSeq}
          width={dockWidth}
          sheet={dockSheet}
          onResize={setDockWidth}
          onClose={() => setWorkspaceOpen(false)}
        />
      ) : null}
      {settingsOpen && session ? (
        <SessionSettingsModal
          host={host}
          token={token}
          session={session}
          onClose={() => setSettingsOpen(false)}
          onApplied={setSession}
          onAuthFailure={onAuthFailure}
          isAssistant={assistant}
          assistant={assistantControls ?? undefined}
        />
      ) : null}
    </section>
    </FileDropZone>
    </SessionFilesLinkProvider>
    </WorkspaceFileLinkProvider>
    </AskQuestionContext.Provider>
  );
}

interface ReplyComposerProps {
  host: string;
  token: string;
  sessionId: string;
  session: SessionRecord | null;
  onScheduled?: () => void;
  agentBusy: boolean;
  permissionModeOptions: readonly BackendPermissionMode[];
  hasToolRuns: boolean;
  toolRunsExpanded: boolean;
  onToggleToolRuns: () => void;
  canShowTasks: boolean;
  onShowTasks: () => void;
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
  effortLevels: string[];
  defaultModelId: string | null;
  defaultModelLabel: string | null;
  defaultEffort: string | null;
  currentModel: string | null;
  currentEffort: string | null;
  effortBusy: boolean;
  focus: boolean;
  focusBusy: boolean;
  onFocusChange: (enabled: boolean) => void | Promise<void>;
  permissionMode: string | null;
  transport: SessionTransport | null;
  catalog: BackendCatalog;
  // Which changes the session applies by restarting; those picks stage behind
  // the restart pill.
  restart: RestartFields;
  onDelete: () => void | Promise<void>;
  onInterrupt: () => void | Promise<void>;
  onModeChange: (mode: string, confirmed?: boolean) => void | Promise<void>;
  onModelChange: (model: string, confirmed?: boolean) => void | Promise<void>;
  onEffortChange: (effort: string, confirmed?: boolean) => void | Promise<void>;
  onRefresh: () => void;
  onRateLimitRefresh: () => void | Promise<void>;
  onReattach: () => void | Promise<void>;
  onSwitchSession: () => void;
  onOpenSettings: () => void;
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
  workspacePreviewEnabled: boolean;
  onBrowseWorkspace: () => void;
  // Files browser open state, owned by SessionDetail (see filesOpen there).
  filesOpen: boolean;
  onFilesOpenChange: (open: boolean) => void;
}

const ReplyComposer = memo(function ReplyComposer({
  host,
  token,
  sessionId,
  session,
  onScheduled,
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
  effortLevels,
  defaultModelId,
  defaultModelLabel,
  defaultEffort,
  currentModel,
  currentEffort,
  effortBusy,
  focus,
  focusBusy,
  onFocusChange,
  permissionMode,
  transport,
  catalog,
  restart,
  hasToolRuns,
  toolRunsExpanded,
  onToggleToolRuns,
  canShowTasks,
  onShowTasks,
  onDelete,
  onInterrupt,
  onModeChange,
  onModelChange,
  onEffortChange,
  onRefresh,
  onRateLimitRefresh,
  onReattach,
  onSwitchSession,
  onOpenSettings,
  onSend,
  attachmentsEnabled,
  onTerminate,
  onError,
  assistant,
  assistantControls,
  workspacePreviewEnabled,
  onBrowseWorkspace,
  filesOpen,
  onFilesOpenChange,
}: ReplyComposerProps) {
  const [draft, setDraft] = useState("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const attachments = useAttachments({ host, token, sessionId, onError });
  useFileDropTarget(
    !attachmentsEnabled ? "unsupported" : disabled ? "exited" : null,
    "your reply",
    attachments.addFiles,
  );
  const [scheduleMsgOpen, setScheduleMsgOpen] = useState(false);
  const [sending, setSending] = useState(false);
  // The transcript records nothing until the interrupt request returns.
  const [stopping, setStopping] = useState(false);
  const [idleSending, setIdleSending] = useState(false);
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
  // Undefined means derive from the current assistant (or the default account
  // for a staged backend change). Null is an explicit Default account choice.
  const [pendingAssistantProfileId, setPendingAssistantProfileId] = useState<
    string | null | undefined
  >(undefined);
  const [selectedThreadId, setSelectedThreadId] = useState("");
  const [threadOptions, setThreadOptions] = useState<AssistantThreadOption[]>([]);
  // The leading actions collapse in CSS, since a re-render on focus can drop focus
  // mid-gesture. `lead-forced` overrides the collapse until typing or blur.
  const [leadForced, setLeadForced] = useState(false);

  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const composerRef = useRef<HTMLElement | null>(null);
  const overflowRef = useRef<HTMLDivElement | null>(null);
  const tuneRef = useRef<HTMLDivElement | null>(null);
  const tuneTriggerRef = useRef<HTMLButtonElement | null>(null);

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
    pathEnabled: !disabled,
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
    closeSendMenu();
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

  // Queue the current draft as an idle-triggered message. Mirrors handleSend's
  // command + ready-attachment resolution, but the composer only clears on a
  // successful create so a failed queue keeps the draft intact.
  async function handleIdleSend(mode: "with_previous" | "next_cycle") {
    const text = draft.trim();
    const attachmentIds = attachments.readyIds;
    if ((!text && attachmentIds.length === 0) || attachments.uploading) {
      return;
    }
    closeSendMenu();
    setIdleSending(true);
    const invocation = selectedCommandInvocation(text);
    try {
      await createMessageSchedule(host, token, sessionId, text, {
        submit: true,
        trigger: "idle",
        idleBatchMode: mode,
        command: invocation ?? null,
        attachments: attachmentIds,
      });
      setDraft("");
      resetCompletions();
      attachments.clear();
      onScheduled?.();
    } catch (err) {
      onError(
        err instanceof Error ? err.message : "failed to queue idle message",
      );
    } finally {
      setIdleSending(false);
      textareaRef.current?.focus();
    }
  }

  function runSendMode(mode: SendMode) {
    if (mode === "now") void handleSend();
    else void handleIdleSend(mode);
  }

  const sendDisabled =
    disabled ||
    sending ||
    idleSending ||
    attachments.uploading ||
    (!draft.trim() && attachments.readyIds.length === 0);

  const {
    open: sendMenuOpen,
    close: closeSendMenu,
    triggerRef: sendButtonRef,
    itemRefs: sendMenuItemRefs,
    wrapRef: sendWrapRef,
    wrapProps: sendWrapProps,
    triggerProps: sendTriggerProps,
    menuProps: sendMenuProps,
  } = useHoldMenu(sendDisabled, () => void handleSend());

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

  function handleDraftKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.nativeEvent.isComposing) {
      return;
    }
    if (event.key === "Tab" && event.shiftKey) {
      event.preventDefault();
      const next = modeBusy ? null : nextModeId(permissionModeOptions, tuning.shown.mode);
      if (next !== null) {
        tuning.select("mode", next);
        if (restart.mode) setTuneOpen(true);
      }
      return;
    }
    if (mentions.handleKey(event)) {
      return;
    }
    if (handleSuggestionKey(event)) {
      return;
    }
    if (!isModifiedEnterShortcut(event)) {
      return;
    }
    event.preventDefault();
    void handleSend();
  }

  const modeOptions = permissionModeOptions;
  // Refresh is always available, so the overflow menu is always present.
  const hasOverflow = true;
  const tuningInput = {
    modeOptions,
    modelOptions,
    effortLevels,
    defaultModelId,
    defaultModelLabel,
    currentModel,
    currentEffort,
    permissionMode,
  };
  const options = tuningOptions(tuningInput);
  const { modelEntries, effortOptions, customEffort, hasModelPicker, hasEffortPicker } = options;
  const tuning = useTuningDraft(
    tuningInput,
    restart,
    { mode: onModeChange, model: onModelChange, effort: onEffortChange },
  );
  const restartLabel = pendingRestartLabel(tuning, modeOptions, modelEntries);
  const restartBusy = modeBusy || modelBusy || effortBusy;
  const applyPendingRestart = async () => {
    setTuneOpen(false);
    await tuning.applyRestart();
  };

  const assistantOps = assistant ? assistantControls : null;
  // Backend the assistant controls target — the picked one, or the current.
  const assistantTargetBackend = pendingBackend ?? session?.backend ?? null;
  const assistantTargetProfiles =
    assistantOps && assistantTargetBackend
      ? assistantOps.accountProfilesFor(assistantTargetBackend)
      : [];
  const assistantTargetProfileId =
    pendingAssistantProfileId !== undefined
      ? pendingAssistantProfileId
      : pendingBackend
        ? null
        : (session?.account_profile_id ?? null);
  const assistantTargetProfileLabel =
    assistantTargetProfiles.find(
      (profile) => profile.id === assistantTargetProfileId,
    )?.label ?? "Default account";
  const showAssistantTargetProfilePicker =
    Boolean(assistantOps && session) && assistantTargetProfiles.length > 0;
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
        assistantTargetTransport !== session.transport ||
        assistantTargetProfileId !== (session.account_profile_id ?? null)),
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
    void assistantOps
      .listThreads(assistantTargetBackend, assistantTargetProfileId)
      .then((threads) => {
        if (!cancelled) setThreadOptions(threads);
      })
      .catch((error) => {
        if (!cancelled) {
          onError(
            error instanceof Error
              ? error.message
              : "Failed to load resumable threads",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [
    tuneOpen,
    assistantOps,
    assistantCanAttach,
    assistantTargetBackend,
    assistantTargetProfileId,
    onError,
  ]);
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
      setPendingAssistantProfileId(undefined);
      setSelectedThreadId("");
    } catch (err) {
      onError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setAssistantBusy(false);
    }
  };
  const assistantExited = isExitedSession(session);
  const tuneSummary = tuningSummary(tuningInput, options).join(" · ") || "Settings";

  return (
    <section className="composer" ref={composerRef}>
      <div className="composer-toprow">
        <div className="composer-tune" ref={tuneRef}>
          <button
            ref={tuneTriggerRef}
            type="button"
            className={`composer-tune-trigger ${tuneOpen ? "open" : ""}`}
            aria-haspopup="dialog"
            aria-expanded={tuneOpen}
            onClick={() => setTuneOpen((open) => !open)}
          >
            <span className="composer-tune-glyph" aria-hidden>
              {"⚙︎"}
            </span>
            <span className="composer-tune-summary">{tuneSummary}</span>
            {tuning.hasPending ? (
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
                        setPendingAssistantProfileId(undefined);
                        setAssistantConfirm(null);
                        return;
                      }
                      setPendingBackend(next);
                      setPendingAssistantProfileId(null);
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
              {showAssistantTargetProfilePicker ? (
                <AccountProfilePicker
                  profiles={assistantTargetProfiles}
                  value={assistantTargetProfileId ?? ""}
                  onChange={(id) => {
                    const nextProfileId = id || null;
                    setSelectedThreadId("");
                    setPendingAssistantProfileId(nextProfileId);
                    setAssistantConfirm(
                      assistantSwitchDiffers ||
                        nextProfileId !== (session?.account_profile_id ?? null)
                        ? "switch"
                        : null,
                    );
                    onError("");
                  }}
                  disabled={assistantBusy}
                  defaultLabel="Default account"
                  label="New conversation account"
                  fieldClassName="composer-tune-field"
                />
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
                    value={tuning.shown.mode}
                    onChange={(event) => tuning.select("mode", event.target.value)}
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
                    value={tuning.shown.model}
                    onChange={(event) => tuning.select("model", event.target.value)}
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
                    value={tuning.shown.effort}
                    onChange={(event) => tuning.select("effort", event.target.value)}
                    disabled={effortBusy || disabled}
                  >
                    <option value="">
                      {defaultEffort ? `Default (${effortLabel(defaultEffort)})` : "Default"}
                    </option>
                    {effortOptions.map((option) => (
                      <option key={option} value={option}>
                        {effortLabel(option)}
                      </option>
                    ))}
                    {customEffort ? <option value={customEffort}>{customEffort}</option> : null}
                  </select>
                </label>
              ) : null}
              <div className="composer-tune-toggle">
                <span className="composer-tune-toggle-copy">
                  <span id="composer-focus-label">Focus</span>
                  <span className="composer-tune-toggle-hint">
                    Hold messages from agents, schedules, and wake-ups
                  </span>
                </span>
                <button
                  type="button"
                  role="switch"
                  className="switch"
                  aria-checked={focus}
                  aria-labelledby="composer-focus-label"
                  disabled={focusBusy}
                  onClick={() => void onFocusChange(!focus)}
                >
                  <span className="switch-thumb" />
                </button>
              </div>
              {tuning.hasPending ? (
                <div className="composer-tune-restart">
                  <p>
                    Restart Claude with <strong>{restartLabel}</strong>?
                    The current turn is interrupted and the session resumes with
                    the new settings.
                  </p>
                  <button
                    type="button"
                    className="composer-tune-restart-apply"
                    onClick={() => void applyPendingRestart()}
                    disabled={restartBusy}
                  >
                    {restartBusy ? "Restarting…" : "Apply restart"}
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
                        {" · "}
                        {assistantTargetProfileLabel}
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
                          setPendingAssistantProfileId(undefined);
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
                              assistantTargetProfileId,
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
                              assistantTargetProfileId,
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
          {focus ? (
            <FocusPill
              onTurnOff={() => void onFocusChange(false)}
              busy={focusBusy}
              returnFocusRef={tuneTriggerRef}
            />
          ) : null}
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
          className={`composer-interrupt-btn${stopping ? " is-stopping" : ""}`}
          onClick={async () => {
            if (stopping) return;
            setStopping(true);
            try {
              await onInterrupt();
            } finally {
              setStopping(false);
            }
          }}
          disabled={disabled}
          aria-busy={stopping || undefined}
          aria-label={stopping ? "Stopping…" : "Interrupt the agent"}
          title={stopping ? "Stopping…" : "Interrupt the agent"}
        >
          <span className="glyph" aria-hidden>
            ■
          </span>
        </button>
        <div className="composer-entry">
          <button
            type="button"
            className="composer-lead-expand"
            // Keeps the field focused where a press doesn't focus the button itself.
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => {
              setLeadForced(true);
              textareaRef.current?.focus();
            }}
            aria-label="Show actions"
            title="Show actions"
          >
            ›
          </button>
          <div className="reply-textarea-wrap">
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
            <div className="composer-send-wrap" ref={sendWrapRef} {...sendWrapProps}>
              <button
                ref={sendButtonRef}
                type="button"
                className="composer-send"
                {...sendTriggerProps}
                disabled={sendDisabled}
                aria-label="Send"
                aria-haspopup="menu"
                aria-expanded={sendMenuOpen}
                title="Send (⌘/Ctrl + ↵) — hold for delivery options"
              >
                <span className="composer-send-glyph" aria-hidden>
                  {sending || idleSending ? "…" : "↑"}
                </span>
              </button>
              {sendMenuOpen ? (
                <div
                  className="composer-send-menu"
                  role="menu"
                  aria-label="Send options"
                  {...sendMenuProps}
                >
                  {SEND_MODES.map((mode, index) => (
                    <button
                      key={mode.value}
                      ref={(el) => {
                        sendMenuItemRefs.current[index] = el;
                      }}
                      type="button"
                      role="menuitem"
                      className="composer-send-menu-item"
                      onClick={() => runSendMode(mode.value)}
                      disabled={sendDisabled}
                      title={mode.hint}
                    >
                      <span className="composer-send-menu-icon" aria-hidden>
                        {mode.value === "now" ? "↑" : "◷"}
                      </span>
                      <span className="composer-send-menu-label">{mode.label}</span>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
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
                  <button
                    type="button"
                    role="menuitem"
                    className="composer-overflow-item"
                    onClick={() => {
                      setOverflowOpen(false);
                      onOpenSettings();
                    }}
                  >
                    <span className="glyph">{"⚙︎"}</span>
                    Session settings…
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    className="composer-overflow-item"
                    onClick={() => {
                      setOverflowOpen(false);
                      setScheduleMsgOpen(true);
                    }}
                  >
                    <span className="glyph">◷</span>
                    Schedule message…
                  </button>
                  {workspacePreviewEnabled ? (
                    <button
                      type="button"
                      role="menuitem"
                      className="composer-overflow-item"
                      onClick={() => {
                        setOverflowOpen(false);
                        onBrowseWorkspace();
                      }}
                    >
                      <span className="glyph">◫</span>
                      Browse workspace…
                    </button>
                  ) : null}
                  {attachmentsEnabled ? (
                    <button
                      type="button"
                      role="menuitem"
                      className="composer-overflow-item"
                      onClick={() => {
                        setOverflowOpen(false);
                        onFilesOpenChange(true);
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
                  {canShowTasks ? (
                    <button
                      type="button"
                      role="menuitem"
                      className="composer-overflow-item"
                      onClick={() => { onShowTasks(); setOverflowOpen(false); }}
                    >
                      <span className="glyph">☑</span>
                      Show tasks
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
                        <span className="glyph">⌫</span>
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
          onClose={() => onFilesOpenChange(false)}
          onReference={attachments.referenceExisting}
          referencedIds={attachments.attachedIds}
        />
      ) : null}
      {scheduleMsgOpen ? (
        <ScheduleMessageModal
          host={host}
          token={token}
          sessionId={sessionId}
          initialDraft={draft}
          onClose={() => setScheduleMsgOpen(false)}
          onScheduled={onScheduled}
          onError={onError}
        />
      ) : null}
    </section>
  );
});

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
  // Prefer the concrete model the backend actually ran (session.resolved_model)
  // for display, so the badge doesn't drift when a catalogue update changes
  // what the user's selection (session.model) currently resolves to. Relaunch
  // / set-model / the picker all stay keyed off session.model, unchanged.
  const resolvedModelLabel = formatResolvedModelLabel(
    session.resolved_model,
    session.model,
    modelOptions,
  );
  const selectionModelLabel =
    modelOptions.find((opt) => opt.id === session.model)?.label ?? session.model;
  const modelBadgeLabel = resolvedModelLabel ?? selectionModelLabel;
  // Badge shows the selection provisionally (dimmed) until a reply confirms the
  // running model.
  const modelConfirmed = Boolean(session.resolved_model);
  const modelBadgeTitle = modelConfirmed
    ? `Model: ${session.resolved_model}`
    : `Selected model: ${session.model} — awaiting the next reply`;
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
        <div className="session-header-cwd-row">
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
          <SessionCwdCopy cwd={session.cwd} />
        </div>
      ) : null}
      <div className="session-header-tags">
        <span className={`badge ${headerAgent}`}>
          {humaniseBackend(headerAgent, catalog)}
        </span>
        {session.account_profile_label ? (
          <span
            className="badge account-profile"
            title={`Account profile: ${session.account_profile_label}`}
          >
            {session.account_profile_label}
          </span>
        ) : null}
        {!assistant && showHeaderTransport ? (
          <span className={`badge transport ${session.transport}`}>
            {transportLabel(session.transport, catalog)}
          </span>
        ) : null}
        {session.model ? (
          <span
            className={`badge model${modelConfirmed ? "" : " unconfirmed"}`}
            title={modelBadgeTitle}
          >
            {modelBadgeLabel}
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
        <AssistantMark />
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
