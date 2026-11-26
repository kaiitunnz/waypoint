"use client";

import { useRouter } from "next/navigation";
import { useCallback, useId, useRef, useState, type ChangeEvent, type ClipboardEvent, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";

import { isAuthError } from "@waypoint/web-core/api";
import { supportsAttachments, type BackendCatalog } from "@waypoint/web-core/backends";
import { insertTrigger, useCommandCompletions } from "@waypoint/web-core/composer-completions";
import { filesFromDataTransfer, pasteAttaches } from "@waypoint/web-core/fileDrop";
import { useFileMentions } from "@waypoint/web-core/hooks/use-file-mentions";
import { useAttachments } from "@waypoint/web-core/hooks/useAttachments";
import { useFileDragVisible } from "@waypoint/web-core/hooks/useFileDragVisible";
import { isModifiedEnterShortcut } from "@waypoint/web-core/keyboard";
import { isExitedSession } from "@waypoint/web-core/session/send";
import type { TerminalConnectionState } from "@waypoint/web-core/session/terminalConnection";
import { submitTerminalMessage } from "@waypoint/web-core/sessionDelivery";
import type { SessionAttachment, SessionRecord } from "@waypoint/web-core/types";

import { useToast } from "@/components/ui/Toast";
import { handleAuthFailure } from "@/lib/auth";
import { cx } from "@/lib/cx";
import { usePointerUI } from "@/lib/layout";
import { useShellData } from "@/lib/shell-data";

import { AttachmentTray } from "./AttachmentTray";
import styles from "./Composer.module.css";
import { DropOverlay } from "./DropOverlay";
import { PlusMenu, type PlusAction } from "./PlusMenu";
import { ScheduleMessageDialog } from "./ScheduleMessageDialog";
import { SendButton } from "./SendControl";
import { SessionFilesDialog } from "./SessionFilesDialog";
import { CommandMenu, MentionMenu, suggestionId } from "./SuggestionMenu";
import drawer from "./TerminalCompose.module.css";
import { useComposerSizing } from "./useComposerSizing";

interface TerminalComposeProps {
  sessionId: string;
  session: SessionRecord | null;
  catalog: BackendCatalog;
  connection: TerminalConnectionState | null;
  // Types one whole message into the pane; false when the socket is closed.
  submit: (text: string) => boolean;
  // Hands the keyboard back to the pane.
  refocusPane: () => void;
  dropRoot: HTMLElement | null;
  filesOpen: boolean;
  onFilesOpenChange: (open: boolean) => void;
  scheduleOpen: boolean;
  onScheduleOpenChange: (open: boolean) => void;
}

// A terminal-only session's composer: a drawer that sends whole messages into
// the pane, with the chat composer's attachments, mentions and completions.
export function TerminalCompose({
  sessionId,
  session,
  catalog,
  connection,
  submit,
  refocusPane,
  dropRoot,
  filesOpen,
  onFilesOpenChange,
  scheduleOpen,
  onScheduleOpenChange,
}: TerminalComposeProps) {
  const { host, token } = useShellData();
  const { toast } = useToast();
  const router = useRouter();
  const pointer = usePointerUI();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const menuId = useId();
  const regionId = useId();

  const reportError = useCallback(
    (message: string) => {
      if (message) toast({ message, tone: "error" });
    },
    [toast],
  );
  const exited = isExitedSession(session);
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
    handleSuggestionKey,
    reset: resetCompletions,
  } = useCommandCompletions({ host, token, sessionId, draft, setDraft, enabled: open, textareaRef: field });
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
    enabled: open && attachmentsEnabled,
    onReference: attachments.referenceExisting,
    textareaRef: field,
  });
  useComposerSizing(root, field, draft, pointer);
  const dragging = useFileDragVisible();

  const canSend =
    open && !exited && !sending && !attachments.uploading && (draft.trim() !== "" || attachments.readyIds.length > 0);

  function expand() {
    setOpen(true);
    requestAnimationFrame(() => field.current?.focus());
  }

  function collapse() {
    setOpen(false);
    window.setTimeout(refocusPane);
  }

  async function send() {
    if (!canSend) return;
    const text = draft;
    const attachmentIds = attachments.readyIds;
    setSending(true);
    try {
      const { result, openSessionId } = await submitTerminalMessage({
        host,
        token,
        sessionId,
        session,
        catalog,
        text,
        attachmentIds,
        sendOverSocket: submit,
      });
      if (result === "socket-closed") {
        reportError(
          connection === "open" ? "Not connected to the pane yet, try again" : "Reconnecting to the pane, try again in a moment",
        );
        return;
      }
      setDraft("");
      attachments.release(attachmentIds);
      resetCompletions();
      if (openSessionId) router.push(`/session/${openSessionId}`);
    } catch (error) {
      if (isAuthError(error)) handleAuthFailure();
      else reportError(error instanceof Error ? error.message : "failed to send input");
    } finally {
      setSending(false);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Safari delivers the Enter that confirms a composition after it ends, as 229.
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (handleMentionKey(event)) return;
    if (handleSuggestionKey(event)) return;
    if (event.key === "Escape") {
      event.preventDefault();
      collapse();
      return;
    }
    if (!isModifiedEnterShortcut(event)) return;
    event.preventDefault();
    void send();
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

  // A file added from outside the drawer opens it, so the tray is seen.
  const addFiles = (files: File[]) => {
    setOpen(true);
    attachments.addFiles(files);
  };
  const reference = (file: SessionAttachment) => {
    setOpen(true);
    attachments.referenceExisting(file);
  };

  function insert(trigger: "/" | "@") {
    const node = field.current;
    if (!node) return;
    const next = insertTrigger(draft, node.selectionStart ?? draft.length, trigger);
    setDraft(next.draft);
    node.focus();
    requestAnimationFrame(() => {
      node.setSelectionRange(next.caret, next.caret);
      node.dispatchEvent(new Event("select"));
    });
  }

  const actions: PlusAction[] = [
    ...(attachmentsEnabled
      ? [{ id: "attach", label: "Attach files", icon: "paperclip" as const, disabled: exited, run: () => fileInput.current?.click() }]
      : []),
    { id: "command", label: "Insert command", icon: "slash", disabled: exited, run: () => insert("/") },
    ...(attachmentsEnabled
      ? [{ id: "mention", label: "Insert mention", icon: "at" as const, disabled: exited, run: () => insert("@") }]
      : []),
  ];
  const highlighted = mentionsOpen ? mentionIndex : suggestionsOpen ? activeIndex : null;

  const tray = attachments.hasItems ? (
    <div key="tray" className={styles.tray}>
      <AttachmentTray
        items={attachments.items}
        onRemove={attachments.remove}
        onRetry={attachments.retry}
        onClear={attachments.discardAll}
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
      disabled={exited}
      placeholder="Message"
      aria-label="Message to type into the pane"
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
  const sendButton = (
    <div key="actions" className={styles.actions}>
      <SendButton canSend={canSend} sending={sending} onSend={() => void send()} />
    </div>
  );

  return (
    <div ref={root} className={cx(styles.composer, !open && drawer.closed)}>
      <button
        type="button"
        className={drawer.handle}
        aria-expanded={open}
        aria-controls={regionId}
        aria-label={open ? "Hide the composer" : "Compose a message"}
        onClick={() => (open ? collapse() : expand())}
      >
        <i aria-hidden="true" />
      </button>
      <div id={regionId} hidden={!open}>
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
        <div className={styles.surface}>{pointer ? [tray, input, plus, sendButton] : [tray, plus, input, sendButton]}</div>
      </div>
      {attachmentsEnabled ? (
        <input ref={fileInput} type="file" multiple hidden onChange={onFilesPicked} aria-hidden="true" tabIndex={-1} />
      ) : null}
      {attachmentsEnabled ? (
        <SessionFilesDialog
          host={host}
          token={token}
          sessionId={sessionId}
          open={filesOpen}
          onClose={() => onFilesOpenChange(false)}
          onReference={reference}
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
          canQueue={!exited}
          onClose={() => onScheduleOpenChange(false)}
        />
      ) : null}
      {dragging && dropRoot
        ? createPortal(
            <DropOverlay
              blocked={!attachmentsEnabled ? "This session doesn't accept attachments" : exited ? "This session has exited" : null}
              sessionTitle={session?.title ?? "this session"}
              onFiles={addFiles}
            />,
            dropRoot,
          )
        : null}
    </div>
  );
}
