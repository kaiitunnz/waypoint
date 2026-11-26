"use client";

import { useRouter } from "next/navigation";
import { useId, useRef, useState, type KeyboardEvent } from "react";

import { isAuthError } from "@waypoint/web-core/api";
import { supportsAttachments, type BackendCatalog } from "@waypoint/web-core/backends";
import { isModifiedEnterShortcut } from "@waypoint/web-core/keyboard";
import { isExitedSession } from "@waypoint/web-core/session/send";
import type { TerminalConnectionState } from "@waypoint/web-core/session/terminalConnection";
import { submitTerminalMessage } from "@waypoint/web-core/sessionDelivery";
import type { SessionRecord } from "@waypoint/web-core/types";

import { handleAuthFailure } from "@/lib/auth";
import { cx } from "@/lib/cx";
import { usePointerUI } from "@/lib/layout";
import { useShellData } from "@/lib/shell-data";

import styles from "./Composer.module.css";
import { ComposerDialogs, ComposerInput, ComposerMenus, ComposerTray, useComposerField } from "./ComposerField";
import { PlusMenu, type PlusAction } from "./PlusMenu";
import { SendButton } from "./SendControl";
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
  onScheduled: () => void;
}

// A terminal-only session's composer: a drawer that types whole messages into
// the pane.
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
  onScheduled,
}: TerminalComposeProps) {
  const { host, token } = useShellData();
  const router = useRouter();
  const pointer = usePointerUI();
  const [open, setOpen] = useState(false);
  const [sending, setSending] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const regionId = useId();

  const exited = isExitedSession(session);
  const attachmentsEnabled = session !== null && supportsAttachments(session.backend, catalog);
  const composerField = useComposerField({
    sessionId,
    attachmentsEnabled,
    commands: open,
    paths: open,
    mentions: open && attachmentsEnabled,
  });
  const { draft, setDraft, field, attachments, completions, reportError } = composerField;
  useComposerSizing(root, field, draft, pointer);

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
    const attachmentIds = attachments.readyIds;
    setSending(true);
    try {
      const { result, openSessionId } = await submitTerminalMessage({
        host,
        token,
        sessionId,
        session,
        catalog,
        text: draft,
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
      completions.reset();
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
    if (composerField.handleMenuKey(event)) return;
    if (event.key === "Escape") {
      event.preventDefault();
      collapse();
      return;
    }
    if (!isModifiedEnterShortcut(event)) return;
    event.preventDefault();
    void send();
  }

  const actions: PlusAction[] = [
    ...(attachmentsEnabled
      ? [
          {
            id: "attach",
            label: "Attach files",
            icon: "paperclip" as const,
            disabled: exited,
            run: () => composerField.fileInput.current?.click(),
          },
        ]
      : []),
    { id: "command", label: "Insert command", icon: "slash", disabled: exited, run: () => composerField.insert("/") },
    ...(attachmentsEnabled
      ? [{ id: "mention", label: "Insert mention", icon: "at" as const, disabled: exited, run: () => composerField.insert("@") }]
      : []),
  ];

  const tray = <ComposerTray key="tray" field={composerField} locked={sending} />;
  const input = (
    <ComposerInput
      key="input"
      field={composerField}
      placeholder="Message"
      label="Message to type into the pane"
      disabled={exited}
      onKeyDown={onKeyDown}
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
      <ComposerMenus field={composerField} />
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
        <div className={styles.surface}>{pointer ? [tray, input, plus, sendButton] : [tray, plus, input, sendButton]}</div>
      </div>
      <ComposerDialogs
        field={composerField}
        session={session}
        disabled={exited}
        exited={exited}
        locked={sending}
        filesOpen={filesOpen}
        onFilesOpenChange={onFilesOpenChange}
        scheduleOpen={scheduleOpen}
        onScheduleOpenChange={onScheduleOpenChange}
        onScheduled={onScheduled}
        dropRoot={dropRoot}
        onReveal={() => setOpen(true)}
      />
    </div>
  );
}
