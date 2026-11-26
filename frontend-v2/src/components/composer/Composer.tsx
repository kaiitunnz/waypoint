"use client";

import { useRouter } from "next/navigation";
import { useCallback, useMemo, useRef, type KeyboardEvent } from "react";

import { fidelityFor, supportsAttachments, type BackendCatalog } from "@waypoint/web-core/backends";
import type { OptimisticSends } from "@waypoint/web-core/hooks/useOptimisticSends";
import { isModifiedEnterShortcut } from "@waypoint/web-core/keyboard";
import { canInterrupt, composerAvailability, isExitedSession } from "@waypoint/web-core/session/send";
import { currentTodoEvent, readTodoEntries, summarizeTodos } from "@waypoint/web-core/todos";
import type { EventRecord, SessionRecord } from "@waypoint/web-core/types";

import { usePointerUI } from "@/lib/layout";
import { useShellData } from "@/lib/shell-data";

import styles from "./Composer.module.css";
import { ComposerDialogs, ComposerInput, ComposerMenus, ComposerTray, useComposerField } from "./ComposerField";
import { FocusPill } from "./FocusPill";
import { PlusMenu, type PlusAction } from "./PlusMenu";
import { SendControl, StopButton } from "./SendControl";
import { SendError } from "./SendError";
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
  scheduleOpen: boolean;
  onScheduleOpenChange: (open: boolean) => void;
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
  scheduleOpen,
  onScheduleOpenChange,
  onPending,
}: ComposerProps) {
  const { host, token } = useShellData();
  const router = useRouter();
  const pointer = usePointerUI();
  const root = useRef<HTMLDivElement>(null);
  const tuneTrigger = useRef<HTMLButtonElement>(null);

  const { disabled, dormant } = composerAvailability(session, catalog);
  const structured = session !== null && fidelityFor(session.transport, catalog) === "structured";
  const attachmentsEnabled = session !== null && supportsAttachments(session.backend, catalog);
  const composerField = useComposerField({
    sessionId,
    attachmentsEnabled,
    commands: structured,
    paths: !disabled,
    mentions: attachmentsEnabled,
  });
  const { draft, setDraft, field, attachments, completions, reportError } = composerField;

  const openSession = useCallback((id: string) => router.push(`/session/${id}`), [router]);
  // For a control about to remove itself: its focus goes to the field. On touch
  // only a keyboard's focus moves, so a tap does not raise the on-screen one.
  const keepFocus = useCallback(() => {
    const control = document.activeElement;
    if (root.current?.contains(control) && (pointer || control?.matches(":focus-visible"))) field.current?.focus();
  }, [pointer, field]);
  const { canSend, sending, stopping, failure, send, retry, dismiss, stop } = useComposerSend({
    host,
    token,
    sessionId,
    session,
    catalog,
    draft,
    setDraft,
    attachments,
    commandInvocation: completions.selectedCommandInvocation,
    resetCompletions: completions.reset,
    onPending,
    onOpenSession: openSession,
  });

  const tuning = useComposerTuning({ host, token, session, catalog, onSession, onError: reportError });

  const todo = useMemo(() => currentTodoEvent(events, latestTodo), [events, latestTodo]);
  const tasks = useMemo(() => summarizeTodos(readTodoEntries(todo)), [todo]);

  const focus = useSessionFocus({ host, token, sessionId, session, onSession });
  useComposerSizing(root, field, draft, pointer);

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Safari delivers the Enter that confirms a composition after it ends, as 229.
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.key === "Tab" && event.shiftKey && tuning.cycleMode()) {
      event.preventDefault();
      return;
    }
    if (composerField.handleMenuKey(event)) return;
    if (!isModifiedEnterShortcut(event)) return;
    event.preventDefault();
    send("now");
  }

  const actions: PlusAction[] = [
    ...(attachmentsEnabled
      ? [
          {
            id: "attach",
            label: "Attach files",
            icon: "paperclip" as const,
            disabled,
            run: () => composerField.fileInput.current?.click(),
          },
          { id: "files", label: "Session files", icon: "files" as const, run: () => onFilesOpenChange(true) },
        ]
      : []),
    { id: "schedule", label: "Schedule message", icon: "clock", disabled: session === null, run: () => onScheduleOpenChange(true) },
    ...(structured
      ? [{ id: "command", label: "Insert command", icon: "slash" as const, disabled, run: () => composerField.insert("/") }]
      : []),
    ...(attachmentsEnabled
      ? [{ id: "mention", label: "Insert mention", icon: "at" as const, disabled, run: () => composerField.insert("@") }]
      : []),
  ];

  const tray = <ComposerTray key="tray" field={composerField} locked={sending} keepFocus={keepFocus} />;
  const input = (
    <ComposerInput
      key="input"
      field={composerField}
      placeholder={`Reply to ${agentName}`}
      label="Reply"
      disabled={disabled}
      onKeyDown={onKeyDown}
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
      {tasks ? (
        <span className={styles.tasks} aria-label={`Tasks: ${tasks.completed} of ${tasks.total} done`} role="img">
          ☑ {tasks.completed}/{tasks.total}
        </span>
      ) : null}
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
      <ComposerMenus field={composerField} />
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
      <ComposerDialogs
        field={composerField}
        session={session}
        disabled={disabled}
        exited={isExitedSession(session)}
        locked={sending}
        filesOpen={filesOpen}
        onFilesOpenChange={onFilesOpenChange}
        scheduleOpen={scheduleOpen}
        onScheduleOpenChange={onScheduleOpenChange}
        dropRoot={dropRoot}
      />
    </div>
  );
}
