"use client";

import { useRouter } from "next/navigation";
import {
  useCallback,
  useId,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type ClipboardEvent,
  type KeyboardEvent,
} from "react";
import { createPortal } from "react-dom";

import { fidelityFor, supportsAttachments, type BackendCatalog } from "@waypoint/web-core/backends";
import { insertTrigger, useCommandCompletions } from "@waypoint/web-core/composer-completions";
import { filesFromDataTransfer, pasteAttaches } from "@waypoint/web-core/fileDrop";
import { useFileMentions } from "@waypoint/web-core/hooks/use-file-mentions";
import { useAttachments } from "@waypoint/web-core/hooks/useAttachments";
import { useTaskDismissal } from "@waypoint/web-core/hooks/useTaskDismissal";
import { useFileDragVisible } from "@waypoint/web-core/hooks/useFileDragVisible";
import type { OptimisticSends } from "@waypoint/web-core/hooks/useOptimisticSends";
import { isModifiedEnterShortcut } from "@waypoint/web-core/keyboard";
import { canInterrupt, composerAvailability, isExitedSession } from "@waypoint/web-core/session/send";
import { currentTodoEvent, readTodoEntries, summarizeTodos } from "@waypoint/web-core/todos";
import type { EventRecord, SessionRecord } from "@waypoint/web-core/types";

import { useToast } from "@/components/ui/Toast";
import { usePointerUI } from "@/lib/layout";
import { useShellData } from "@/lib/shell-data";
import { useFailureToast } from "@/lib/useFailureToast";

import { AttachmentTray } from "./AttachmentTray";
import styles from "./Composer.module.css";
import { DropOverlay } from "./DropOverlay";
import { FocusPill } from "./FocusPill";
import { PlusMenu, type PlusAction } from "./PlusMenu";
import { ScheduleMessageDialog } from "./ScheduleMessageDialog";
import { SendControl, StopButton } from "./SendControl";
import { SendError } from "./SendError";
import { SessionFilesDialog } from "./SessionFilesDialog";
import { CommandMenu, MentionMenu, suggestionId } from "./SuggestionMenu";
import { TasksControl } from "./TasksControl";
import { TuneControl } from "./TuneControl";
import { UsageControl } from "./UsageControl";
import { useComposerSend } from "./useComposerSend";
import { useComposerSizing } from "./useComposerSizing";
import { useComposerTuning } from "./useComposerTuning";
import { useSessionFocus } from "./useSessionFocus";

interface ComposerProps {
  sessionId: string;
  // Null while the session loads.
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
  onScheduled: () => void;
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
  onScheduled,
}: ComposerProps) {
  const { host, token } = useShellData();
  const { toast } = useToast();
  const router = useRouter();
  const pointer = usePointerUI();
  const [draft, setDraft] = useState("");
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const tuneTrigger = useRef<HTMLButtonElement>(null);
  const menuId = useId();

  const reportError = useCallback(
    (message: string) => {
      if (message) toast({ message, tone: "error" });
    },
    [toast],
  );

  const { disabled, dormant } = composerAvailability(session, catalog);
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
  // For a control about to remove itself: its focus goes to the field. On touch
  // only a keyboard's focus moves, so a tap does not raise the on-screen one.
  const keepFocus = useCallback(() => {
    const control = document.activeElement;
    if (root.current?.contains(control) && (pointer || control?.matches(":focus-visible"))) field.current?.focus();
  }, [pointer]);
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
    onScheduled,
  });

  const tuning = useComposerTuning({ host, token, session, catalog, onSession, onError: reportError });

  const todo = useMemo(() => currentTodoEvent(events, latestTodo), [events, latestTodo]);
  const tasks = useMemo(() => summarizeTodos(readTodoEntries(todo)), [todo]);
  const reportFailure = useFailureToast();
  const taskDock = useTaskDismissal({ host, token, sessionId, session, todo, onSession, onError: reportFailure });

  const focus = useSessionFocus({ host, token, sessionId, session, onSession });
  useComposerSizing(root, field, draft, pointer);

  const dragging = useFileDragVisible();

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Safari delivers the Enter that confirms a composition after it ends, as 229.
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.key === "Tab" && event.shiftKey && tuning.cycleMode()) {
      event.preventDefault();
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
    if (!pasteAttaches(files, event.clipboardData.getData("text/plain"))) return;
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
    ...(tasks && taskDock.dismissed ? [{ id: "tasks", label: "Show tasks", icon: "check" as const, run: taskDock.show }] : []),
    ...(structured ? [{ id: "command", label: "Insert command", icon: "slash" as const, disabled, run: () => insert("/") }] : []),
    ...(attachmentsEnabled ? [{ id: "mention", label: "Insert mention", icon: "at" as const, disabled, run: () => insert("@") }] : []),
  ];

  // Where the highlight is while a menu is open: the keys act on it from the field.
  const highlighted = mentionsOpen ? mentionIndex : suggestionsOpen ? activeIndex : null;

  const tray = attachments.hasItems ? (
    <div key="tray" className={styles.tray}>
      <AttachmentTray
        items={attachments.items}
        onRemove={(localId) => {
          keepFocus();
          attachments.remove(localId);
        }}
        onRetry={(localId) => {
          keepFocus();
          attachments.retry(localId);
        }}
        onClear={() => {
          keepFocus();
          attachments.discardAll();
        }}
        locked={sending}
      />
    </div>
  ) : null;
  const input = (
    <textarea
      key="input"
      ref={field}
      className={styles.field}
      rows={1}
      value={draft}
      disabled={disabled}
      placeholder={`Reply to ${agentName}`}
      aria-label="Reply"
      aria-controls={highlighted === null ? undefined : menuId}
      aria-activedescendant={highlighted === null ? undefined : suggestionId(menuId, highlighted)}
      onChange={(event) => setDraft(event.target.value)}
      onKeyDown={onKeyDown}
      onPaste={onPaste}
    />
  );
  const plus = (
    <div key="plus" className={styles.plus}>
      <PlusMenu actions={actions} />
    </div>
  );
  const tune = (
    <div key="tune" className={styles.tune}>
      {session ? (
        <TuneControl
          tuning={tuning}
          triggerRef={tuneTrigger}
          agentName={agentName}
          disabled={disabled}
          focus={focus}
        />
      ) : null}
    </div>
  );
  const meta = (
    <div key="meta" className={styles.meta}>
      {focus.on ? (
        <FocusPill busy={focus.busy} onTurnOff={() => focus.set(false)} returnFocusRef={tuneTrigger} />
      ) : null}
      {tasks && !taskDock.dismissed ? <TasksControl tasks={tasks} onDismiss={taskDock.dismiss} /> : null}
      <UsageControl session={session} catalog={catalog} onSession={onSession} />
    </div>
  );
  const actionButtons = (
    <div key="actions" className={styles.actions}>
      {canInterrupt(session) ? (
        <StopButton
          stopping={stopping}
          onStop={() => {
            keepFocus();
            void stop();
          }}
        />
      ) : null}
      <SendControl canSend={canSend} sending={sending} dormant={dormant} onSend={send} keepFocus={keepFocus} />
    </div>
  );

  return (
    <div ref={root} className={styles.composer}>
      {suggestionsOpen ? (
        <CommandMenu
          id={menuId}
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
          id={menuId}
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
      {failure ? (
        <SendError
          failure={failure}
          onRetry={() => {
            keepFocus();
            retry();
          }}
          onOpenSession={openSession}
          onDismiss={() => {
            keepFocus();
            dismiss();
          }}
        />
      ) : null}
      <div className={styles.surface}>
        {pointer
          ? [tray, input, plus, tune, meta, actionButtons]
          : [tune, meta, <div key="break" className={styles.break} />, tray, plus, input, actionButtons]}
      </div>
      <span className={styles.announce} role="status" aria-live="polite">
        {tuning.announcement}
      </span>
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
          onDeleted={attachments.release}
          locked={sending}
        />
      ) : null}
      {scheduleOpen ? (
        <ScheduleMessageDialog
          host={host}
          token={token}
          sessionId={sessionId}
          initialText={draft}
          canQueue={!isExitedSession(session)}
          onClose={() => setScheduleOpen(false)}
          onScheduled={onScheduled}
        />
      ) : null}
      {dragging && dropRoot
        ? createPortal(
            <DropOverlay
              blocked={
                !attachmentsEnabled ? "This session doesn't accept attachments" : disabled ? "This session has exited" : null
              }
              sessionTitle={session?.title ?? "this session"}
              onFiles={attachments.addFiles}
            />,
            dropRoot,
          )
        : null}
    </div>
  );
}
