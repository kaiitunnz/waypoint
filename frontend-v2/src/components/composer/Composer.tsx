"use client";

import { useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type ClipboardEvent,
  type KeyboardEvent,
} from "react";
import { createPortal } from "react-dom";

import { isAuthError, refreshSessionRateLimitUsage, setSessionFocus } from "@waypoint/web-core/api";
import {
  fidelityFor,
  permissionModesFor,
  supportsAttachments,
  type BackendCatalog,
} from "@waypoint/web-core/backends";
import { insertTrigger, useCommandCompletions } from "@waypoint/web-core/composer-completions";
import { filesFromDataTransfer } from "@waypoint/web-core/fileDrop";
import { useFileMentions } from "@waypoint/web-core/hooks/use-file-mentions";
import { useAttachments } from "@waypoint/web-core/hooks/useAttachments";
import { useFileDragVisible } from "@waypoint/web-core/hooks/useFileDragVisible";
import type { OptimisticSends } from "@waypoint/web-core/hooks/useOptimisticSends";
import { useSessionModels } from "@waypoint/web-core/hooks/useSessionModels";
import { interruptPrompt, useSessionTuning } from "@waypoint/web-core/hooks/useSessionTuning";
import { useTuningDraft } from "@waypoint/web-core/hooks/useTuningDraft";
import { isModifiedEnterShortcut } from "@waypoint/web-core/keyboard";
import { canInterrupt, composerAvailability } from "@waypoint/web-core/session/send";
import {
  nextModeId,
  pendingRestartLabel,
  restartFields,
  tuningOptions,
  tuningSummary,
} from "@waypoint/web-core/session/tuning";
import { currentTodoEvent, readTodoEntries, summarizeTodos } from "@waypoint/web-core/todos";
import type { EventRecord, SessionRecord } from "@waypoint/web-core/types";

import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useToast } from "@/components/ui/Toast";
import { handleAuthFailure } from "@/lib/auth";
import { usePointerUI } from "@/lib/layout";
import { useShellData } from "@/lib/shell-data";

import { AttachmentTray } from "./AttachmentTray";
import styles from "./Composer.module.css";
import { DropOverlay } from "./DropOverlay";
import { FocusPill } from "./FocusPill";
import { PlusMenu, type PlusAction } from "./PlusMenu";
import { ScheduleMessageDialog } from "./ScheduleMessageDialog";
import { SendControl, StopButton } from "./SendControl";
import { SendError } from "./SendError";
import { SessionFilesDialog } from "./SessionFilesDialog";
import { CommandMenu, MentionMenu } from "./SuggestionMenu";
import { TuneControl } from "./TuneControl";
import { UsageControl } from "./UsageControl";
import { useComposerSend } from "./useComposerSend";

interface ComposerProps {
  sessionId: string;
  // Null while the session loads; the field is disabled until then.
  session: SessionRecord | null;
  catalog: BackendCatalog;
  agentName: string;
  events: EventRecord[];
  latestTodo: EventRecord | null;
  onSession: (session: SessionRecord) => void;
  // The conversation, where a file dropped anywhere attaches.
  dropRoot: HTMLElement | null;
  filesOpen: boolean;
  onFilesOpenChange: (open: boolean) => void;
  onPending: OptimisticSends["add"];
}

export function Composer({
  sessionId,
  session,
  catalog,
  agentName,
  events,
  latestTodo,
  onSession,
  dropRoot,
  filesOpen,
  onFilesOpenChange,
  onPending,
}: ComposerProps) {
  const { host, token } = useShellData();
  const { toast } = useToast();
  const router = useRouter();
  const pointer = usePointerUI();
  const [draft, setDraft] = useState("");
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [tuneOpen, setTuneOpen] = useState(false);
  // Opened by Shift+Tab, which keeps cycling from the field.
  const [tuneFromKey, setTuneFromKey] = useState(false);
  const [focusBusy, setFocusBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  // The setting waiting on a yes to cut off the running turn.
  const [interrupting, setInterrupting] = useState<{ field: string; answer: (yes: boolean) => void } | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const tuneTrigger = useRef<HTMLButtonElement>(null);

  const reportError = useCallback(
    (message: string) => {
      if (message) toast({ message, tone: "error" });
    },
    [toast],
  );
  const fail = useCallback(
    (error: unknown, fallback: string) => {
      if (isAuthError(error)) handleAuthFailure();
      else reportError(error instanceof Error ? error.message : fallback);
    },
    [reportError],
  );

  const { disabled } = composerAvailability(session, catalog);
  const structured = session !== null && fidelityFor(session.transport, catalog) === "structured";
  const attachmentsEnabled = session !== null && supportsAttachments(session.backend, catalog);

  const attachments = useAttachments({ host, token, sessionId, onError: reportError });
  const {
    suggestions,
    suggestionsOpen,
    activeIndex,
    setActiveIndex,
    listRef,
    itemRefs,
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
    enabled: structured,
    pathEnabled: !disabled,
    textareaRef: field,
  });
  const {
    mentions,
    open: mentionsOpen,
    activeIndex: mentionIndex,
    setActiveIndex: setMentionIndex,
    itemRefs: mentionRefs,
    apply: applyMention,
    handleKey: handleMentionKey,
  } = useFileMentions({
    host,
    token,
    sessionId,
    draft,
    setDraft,
    enabled: attachmentsEnabled,
    onReference: attachments.referenceExisting,
    textareaRef: field,
  });

  const openSession = useCallback((id: string) => router.push(`/session/${id}`), [router]);
  const focusField = useCallback(() => field.current?.focus(), []);
  const { canSend, sending, stopping, failure, send, retry, dismiss, stop } = useComposerSend({
    host,
    token,
    sessionId,
    session,
    catalog,
    draft,
    setDraft,
    attachments,
    commandInvocation: selectedCommandInvocation,
    resetCompletions,
    onPending,
    onOpenSession: openSession,
    onQueued: focusField,
  });

  const models = useSessionModels(host, token, session, handleAuthFailure);
  const confirmInterrupt = useCallback(
    (name: string) => new Promise<boolean>((answer) => setInterrupting({ field: name, answer })),
    [],
  );
  const tuningActions = useSessionTuning({
    host,
    token,
    session,
    catalog,
    onSession,
    onError: reportError,
    onAuthFailure: handleAuthFailure,
    confirmInterrupt,
  });
  const modeOptions = session ? permissionModesFor(session.backend, catalog) : [];
  const tuningInput = {
    modeOptions,
    modelOptions: models.modelOptions,
    effortLevels: models.effortLevels,
    defaultModelId: models.defaultModelId,
    defaultModelLabel: models.defaultModelLabel,
    currentModel: session?.model ?? null,
    currentEffort: session?.effort ?? null,
    permissionMode: session?.permission_mode ?? null,
  };
  const options = tuningOptions(tuningInput);
  const restart = restartFields(session, catalog);
  const tuning = useTuningDraft(tuningInput, restart, {
    mode: tuningActions.changeMode,
    model: tuningActions.changeModel,
    effort: tuningActions.changeEffort,
  });

  const todo = useMemo(() => currentTodoEvent(events, latestTodo), [events, latestTodo]);
  const tasks = useMemo(() => summarizeTodos(readTodoEntries(todo)), [todo]);

  const setFocus = useCallback(
    async (enabled: boolean) => {
      setFocusBusy(true);
      try {
        onSession(await setSessionFocus(host, token, sessionId, enabled));
      } catch (error) {
        fail(error, enabled ? "failed to turn on Focus" : "failed to turn off Focus");
      } finally {
        setFocusBusy(false);
      }
    },
    [host, token, sessionId, onSession, fail],
  );

  async function refreshUsage() {
    if (refreshing) return;
    setRefreshing(true);
    try {
      onSession(await refreshSessionRateLimitUsage(host, token, sessionId));
    } catch (error) {
      fail(error, "failed to refresh rate-limit usage");
    } finally {
      setRefreshing(false);
    }
  }

  // The field grows with its text, up to the stylesheet's maximum.
  const grow = useCallback(() => {
    const node = field.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${node.scrollHeight + node.offsetHeight - node.clientHeight}px`;
  }, []);
  useLayoutEffect(grow, [grow, draft, pointer]);

  // Toasts sit above the composer, and a change of width rewraps the text.
  useEffect(() => {
    const node = root.current;
    if (!node) return;
    const page = document.documentElement;
    let width = node.offsetWidth;
    const observer = new ResizeObserver(() => {
      if (node.offsetWidth !== width) {
        width = node.offsetWidth;
        grow();
      }
      page.style.setProperty("--composer-height", `${node.offsetHeight}px`);
    });
    observer.observe(node);
    return () => {
      observer.disconnect();
      page.style.removeProperty("--composer-height");
    };
  }, [grow]);

  const [dragging, endDrag] = useFileDragVisible();

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.nativeEvent.isComposing) return;
    if (event.key === "Tab" && event.shiftKey && modeOptions.length > 0) {
      event.preventDefault();
      const next = tuningActions.busy.mode ? null : nextModeId(modeOptions, tuning.shown.mode);
      if (next !== null) {
        tuning.select("mode", next);
        if (restart.mode) {
          setTuneFromKey(true);
          setTuneOpen(true);
        }
      }
      return;
    }
    if (handleMentionKey(event)) return;
    if (handleSuggestionKey(event)) return;
    if (!isModifiedEnterShortcut(event)) return;
    event.preventDefault();
    send("now");
  }

  function onPaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    if (!attachmentsEnabled) return;
    const files = filesFromDataTransfer(event.clipboardData);
    // Pasted files become attachments; plain text falls through to the field.
    if (files.length === 0) return;
    event.preventDefault();
    attachments.addFiles(files);
  }

  function onFilesPicked(event: ChangeEvent<HTMLInputElement>) {
    const files = event.target.files ? Array.from(event.target.files) : [];
    if (files.length) attachments.addFiles(files);
    event.target.value = "";
  }

  function insert(trigger: "/" | "@") {
    const node = field.current;
    if (!node) return;
    const next = insertTrigger(draft, node.selectionStart ?? draft.length, trigger);
    setDraft(next.draft);
    // Inside the tap, so a phone opens its keyboard.
    node.focus();
    requestAnimationFrame(() => {
      node.setSelectionRange(next.caret, next.caret);
      // The completion hooks follow the caret by its events.
      node.dispatchEvent(new Event("select"));
    });
  }

  const actions: PlusAction[] = [
    ...(attachmentsEnabled
      ? [
          { id: "attach", label: "Attach files", icon: "paperclip" as const, disabled, run: () => fileInput.current?.click() },
          { id: "files", label: "Session files", icon: "files" as const, run: () => onFilesOpenChange(true) },
        ]
      : []),
    { id: "schedule", label: "Schedule message", icon: "clock", disabled: session === null, run: () => setScheduleOpen(true) },
    ...(structured ? [{ id: "command", label: "Insert command", icon: "slash" as const, disabled, run: () => insert("/") }] : []),
    ...(attachmentsEnabled ? [{ id: "mention", label: "Insert mention", icon: "at" as const, disabled, run: () => insert("@") }] : []),
  ];

  return (
    <div ref={root} className={styles.composer}>
      {suggestionsOpen ? (
        <CommandMenu
          suggestions={suggestions}
          listRef={listRef}
          activeIndex={activeIndex}
          itemRefs={itemRefs}
          onApply={applySuggestion}
          onHover={setActiveIndex}
        />
      ) : null}
      {mentionsOpen ? (
        <MentionMenu
          host={host}
          token={token}
          sessionId={sessionId}
          mentions={mentions}
          activeIndex={mentionIndex}
          itemRefs={mentionRefs}
          onApply={applyMention}
          onHover={setMentionIndex}
        />
      ) : null}
      {failure ? <SendError failure={failure} onRetry={retry} onOpenSession={openSession} onDismiss={dismiss} /> : null}
      <div className={styles.surface}>
        {attachments.hasItems ? (
          <div className={styles.tray}>
            <AttachmentTray
              items={attachments.items}
              onRemove={attachments.remove}
              onRetry={attachments.retry}
              onClear={attachments.discardAll}
            />
          </div>
        ) : null}
        <textarea
          ref={field}
          className={styles.field}
          rows={1}
          value={draft}
          disabled={disabled}
          placeholder={`Reply to ${agentName}`}
          aria-label="Reply"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
        />
        <div className={styles.plus}>
          <PlusMenu actions={actions} />
        </div>
        <div className={styles.tune}>
          {session ? (
            <TuneControl
              open={tuneOpen}
              onOpenChange={(open) => {
                setTuneOpen(open);
                if (!open) setTuneFromKey(false);
              }}
              autoFocus={!tuneFromKey}
              triggerRef={tuneTrigger}
              agentName={agentName}
              summary={tuningSummary(tuningInput, options)}
              modeOptions={modeOptions}
              options={options}
              draft={tuning}
              restartLabel={pendingRestartLabel(tuning, modeOptions, options.modelEntries)}
              busy={tuningActions.busy}
              disabled={disabled}
              currentEffort={session.effort ?? null}
              defaultModelLabel={models.defaultModelLabel}
              defaultEffort={models.defaultEffort}
              focus={Boolean(session.focus)}
              focusBusy={focusBusy}
              onFocusChange={(enabled) => void setFocus(enabled)}
            />
          ) : null}
        </div>
        <div className={styles.meta}>
          {session?.focus ? (
            <FocusPill busy={focusBusy} onTurnOff={() => setFocus(false)} returnFocusRef={tuneTrigger} />
          ) : null}
          {tasks ? (
            <span className={styles.tasks} aria-label={`Tasks: ${tasks.completed} of ${tasks.total} done`} role="img">
              ☑ {tasks.completed}/{tasks.total}
            </span>
          ) : null}
          <UsageControl session={session} catalog={catalog} onRefresh={() => void refreshUsage()} refreshing={refreshing} />
        </div>
        <div className={styles.actions}>
          {canInterrupt(session) ? <StopButton stopping={stopping} onStop={() => void stop()} /> : null}
          <SendControl canSend={canSend} sending={sending} onSend={send} />
        </div>
      </div>
      {attachmentsEnabled ? (
        <input
          ref={fileInput}
          type="file"
          multiple
          hidden
          onChange={onFilesPicked}
          aria-hidden="true"
          tabIndex={-1}
        />
      ) : null}
      {attachmentsEnabled ? (
        <SessionFilesDialog
          host={host}
          token={token}
          sessionId={sessionId}
          open={filesOpen}
          onClose={() => onFilesOpenChange(false)}
          onReference={attachments.referenceExisting}
          referencedIds={attachments.attachedIds}
        />
      ) : null}
      {scheduleOpen ? (
        <ScheduleMessageDialog
          host={host}
          token={token}
          sessionId={sessionId}
          initialText={draft}
          onClose={() => setScheduleOpen(false)}
        />
      ) : null}
      <ConfirmDialog
        open={interrupting !== null}
        title="Interrupt the current turn?"
        detail={interrupting ? interruptPrompt(interrupting.field) : ""}
        confirmLabel="Continue"
        onConfirm={() => interrupting?.answer(true)}
        onClose={() => {
          interrupting?.answer(false);
          setInterrupting(null);
        }}
      />
      {dragging && dropRoot
        ? createPortal(
            <DropOverlay
              blocked={
                !attachmentsEnabled ? "This session doesn't accept attachments" : disabled ? "This session has exited" : null
              }
              sessionTitle={session?.title ?? "this session"}
              onFiles={attachments.addFiles}
              onDone={endDrag}
            />,
            dropRoot,
          )
        : null}
    </div>
  );
}
