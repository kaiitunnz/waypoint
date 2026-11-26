"use client";

import { useCallback, useId, useRef, useState, type ChangeEvent, type ClipboardEvent, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";

import { insertTrigger, useCommandCompletions } from "@waypoint/web-core/composer-completions";
import { filesFromDataTransfer, pasteAttaches } from "@waypoint/web-core/fileDrop";
import { useFileMentions } from "@waypoint/web-core/hooks/use-file-mentions";
import { useAttachments } from "@waypoint/web-core/hooks/useAttachments";
import { useFileDragVisible } from "@waypoint/web-core/hooks/useFileDragVisible";
import type { SessionAttachment, SessionRecord } from "@waypoint/web-core/types";

import { useToast } from "@/components/ui/Toast";
import { useShellData } from "@/lib/shell-data";

import { AttachmentTray } from "./AttachmentTray";
import styles from "./Composer.module.css";
import { DropOverlay } from "./DropOverlay";
import { ScheduleMessageDialog } from "./ScheduleMessageDialog";
import { SessionFilesDialog } from "./SessionFilesDialog";
import { CommandMenu, MentionMenu, suggestionId } from "./SuggestionMenu";

interface FieldOptions {
  sessionId: string;
  attachmentsEnabled: boolean;
  // `/` and `$` completions.
  commands: boolean;
  // `./` completions.
  paths: boolean;
  // `@` mentions of the session's files.
  mentions: boolean;
}

// A composer's text field with its attachments, completions and mentions.
export function useComposerField({ sessionId, attachmentsEnabled, commands, paths, mentions: mentionsEnabled }: FieldOptions) {
  const { host, token } = useShellData();
  const { toast } = useToast();
  const [draft, setDraft] = useState("");
  const field = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const menuId = useId();

  const reportError = useCallback(
    (message: string) => {
      if (message) toast({ message, tone: "error" });
    },
    [toast],
  );
  const attachments = useAttachments({ host, token, sessionId, onError: reportError });
  const completions = useCommandCompletions({
    host,
    token,
    sessionId,
    draft,
    setDraft,
    enabled: commands,
    pathEnabled: paths,
    textareaRef: field,
  });
  const mentions = useFileMentions({
    host,
    token,
    sessionId,
    draft,
    setDraft,
    enabled: mentionsEnabled,
    onReference: attachments.referenceExisting,
    textareaRef: field,
  });

  // Where the highlight is while a menu is open: the keys act on it from the field.
  const highlighted = mentions.open ? mentions.activeIndex : completions.suggestionsOpen ? completions.activeIndex : null;

  return {
    sessionId,
    attachmentsEnabled,
    draft,
    setDraft,
    field,
    fileInput,
    menuId,
    attachments,
    completions,
    mentions,
    highlighted,
    reportError,
    // True when an open menu took the key.
    handleMenuKey: (event: KeyboardEvent<HTMLTextAreaElement>) =>
      mentions.handleKey(event) || completions.handleSuggestionKey(event),
    onPaste: (event: ClipboardEvent<HTMLTextAreaElement>) => {
      if (!attachmentsEnabled) return;
      const files = filesFromDataTransfer(event.clipboardData);
      if (!pasteAttaches(files, event.clipboardData.getData("text/plain"))) return;
      event.preventDefault();
      attachments.addFiles(files);
    },
    insert: (trigger: "/" | "@") => {
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
    },
  };
}

export type ComposerFieldState = ReturnType<typeof useComposerField>;

interface ComposerInputProps {
  field: ComposerFieldState;
  placeholder: string;
  label: string;
  disabled: boolean;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
}

export function ComposerInput({ field, placeholder, label, disabled, onKeyDown }: ComposerInputProps) {
  const { field: ref, draft, setDraft, onPaste, highlighted, menuId } = field;
  return (
    <textarea
      ref={ref}
      className={styles.field}
      rows={1}
      value={draft}
      disabled={disabled}
      placeholder={placeholder}
      aria-label={label}
      aria-controls={highlighted === null ? undefined : menuId}
      aria-activedescendant={highlighted === null ? undefined : suggestionId(menuId, highlighted)}
      onChange={(event) => setDraft(event.target.value)}
      onKeyDown={onKeyDown}
      onPaste={onPaste}
    />
  );
}

interface ComposerTrayProps {
  field: ComposerFieldState;
  locked: boolean;
  // For a control about to remove itself.
  keepFocus?: () => void;
}

export function ComposerTray({ field: { attachments }, locked, keepFocus }: ComposerTrayProps) {
  if (!attachments.hasItems) return null;
  return (
    <div className={styles.tray}>
      <AttachmentTray
        items={attachments.items}
        onRemove={(localId) => {
          keepFocus?.();
          attachments.remove(localId);
        }}
        onRetry={(localId) => {
          keepFocus?.();
          attachments.retry(localId);
        }}
        onClear={() => {
          keepFocus?.();
          attachments.discardAll();
        }}
        locked={locked}
      />
    </div>
  );
}

// The completion and mention menus, above the field.
export function ComposerMenus({ field }: { field: ComposerFieldState }) {
  const { host, token } = useShellData();
  const { completions, mentions, menuId } = field;
  return (
    <>
      {completions.suggestionsOpen ? (
        <CommandMenu
          id={menuId}
          suggestions={completions.suggestions}
          listRef={completions.listRef}
          activeIndex={completions.activeIndex}
          itemRefs={completions.itemRefs}
          onApply={completions.applySuggestion}
          onHover={completions.setActiveIndex}
        />
      ) : null}
      {mentions.open ? (
        <MentionMenu
          id={menuId}
          host={host}
          token={token}
          sessionId={field.sessionId}
          mentions={mentions.mentions}
          activeIndex={mentions.activeIndex}
          itemRefs={mentions.itemRefs}
          onApply={mentions.apply}
          onHover={mentions.setActiveIndex}
        />
      ) : null}
    </>
  );
}

interface ComposerDialogsProps {
  field: ComposerFieldState;
  session: SessionRecord | null;
  // The session takes no input.
  disabled: boolean;
  // A stopped session cannot queue a message for when it is idle.
  exited: boolean;
  // A send is in flight.
  locked: boolean;
  filesOpen: boolean;
  onFilesOpenChange: (open: boolean) => void;
  scheduleOpen: boolean;
  onScheduleOpenChange: (open: boolean) => void;
  // The conversation, where a file dropped anywhere attaches.
  dropRoot: HTMLElement | null;
  // Runs before a file from outside the composer lands in the tray.
  onReveal?: () => void;
}

// The file picker, the session files and schedule dialogs, and the drop target.
export function ComposerDialogs({
  field,
  session,
  disabled,
  exited,
  locked,
  filesOpen,
  onFilesOpenChange,
  scheduleOpen,
  onScheduleOpenChange,
  dropRoot,
  onReveal,
}: ComposerDialogsProps) {
  const { host, token } = useShellData();
  const dragging = useFileDragVisible();
  const { attachments, attachmentsEnabled, sessionId, draft, fileInput } = field;
  const addFiles = (files: File[]) => {
    onReveal?.();
    attachments.addFiles(files);
  };
  const reference = (file: SessionAttachment) => {
    onReveal?.();
    attachments.referenceExisting(file);
  };
  const onFilesPicked = (event: ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files ? Array.from(event.target.files) : [];
    if (files.length) attachments.addFiles(files);
    event.target.value = "";
  };

  return (
    <>
      {attachmentsEnabled ? (
        <>
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            onChange={onFilesPicked}
            aria-hidden="true"
            tabIndex={-1}
          />
          <SessionFilesDialog
            host={host}
            token={token}
            sessionId={sessionId}
            open={filesOpen}
            onClose={() => onFilesOpenChange(false)}
            onReference={reference}
            referencedIds={attachments.attachedIds}
            onDeleted={attachments.release}
            locked={locked}
          />
        </>
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
              blocked={
                !attachmentsEnabled ? "This session doesn't accept attachments" : disabled ? "This session has exited" : null
              }
              sessionTitle={session?.title ?? "this session"}
              onFiles={addFiles}
            />,
            dropRoot,
          )
        : null}
    </>
  );
}
