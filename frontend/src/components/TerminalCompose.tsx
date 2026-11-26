"use client";

import {
  ChangeEvent,
  ClipboardEvent,
  KeyboardEvent,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";

import { AttachmentTray, FilesIcon, PaperclipIcon } from "@/components/AttachmentTray";
import { filesFromDataTransfer } from "@waypoint/web-core/fileDrop";
import { useAttachments } from "@waypoint/web-core/hooks/useAttachments";
import { CommandSuggestions } from "@/components/CommandSuggestions";
import { useFileDropTarget } from "@/components/FileDrop";
import { FileMentions } from "@/components/FileMentions";
import { SessionFilesPanel } from "@/components/SessionFilesPanel";
import { type TerminalSubmitResult } from "@waypoint/web-core/sessionDelivery";
import { isModifiedEnterShortcut } from "@waypoint/web-core/keyboard";
import { useCommandCompletions } from "@waypoint/web-core/composer-completions";
import { useFileMentions } from "@waypoint/web-core/hooks/use-file-mentions";

type ConnectionState = "idle" | "connecting" | "open" | "reconnecting";

interface TerminalComposeProps {
  host: string;
  token: string;
  sessionId: string;
  // ``socket-closed`` keeps the draft and surfaces a retry hint,
  // ``command-error`` keeps it silently (the host already reported the
  // error), ``ok`` clears it.
  onSubmit: (text: string, attachmentIds: string[]) => Promise<TerminalSubmitResult>;
  attachmentsEnabled: boolean;
  onError: (message: string) => void;
  expanded: boolean;
  onExpandedChange: (next: boolean) => void;
  connection: ConnectionState;
  // Focus target when the user dismisses the drawer via Escape — the
  // xterm canvas, so typing resumes inside the pane.
  refocusTerminal: () => void;
  // A post-mount increment opens the files panel; the mount value is the baseline.
  filesOpenRequest?: number;
}


export function TerminalCompose({
  host,
  token,
  sessionId,
  onSubmit,
  attachmentsEnabled,
  onError,
  expanded,
  onExpandedChange,
  connection,
  refocusTerminal,
  filesOpenRequest = 0,
}: TerminalComposeProps) {
  const regionId = useId();
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [hint, setHint] = useState<string | null>(null);
  // Mirrors the chat composer: 📁/📎 collapse to a ›-chevron on focus (CSS),
  // and the chevron forces them back open via `lead-forced`.
  const [leadForced, setLeadForced] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [filesOpen, setFilesOpen] = useState(false);
  const attachments = useAttachments({ host, token, sessionId, onError });
  useFileDropTarget(attachmentsEnabled ? null : "unsupported", "the terminal composer", (files) => {
    onExpandedChange(true);
    attachments.addFiles(files);
  });

  // Baseline the mount value so a session switch never reopens Files.
  const filesRequestBaseline = useRef(filesOpenRequest);
  useEffect(() => {
    if (filesOpenRequest === filesRequestBaseline.current) return;
    filesRequestBaseline.current = filesOpenRequest;
    setFilesOpen(true);
  }, [filesOpenRequest]);

  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const handleRef = useRef<HTMLButtonElement | null>(null);

  // ``/new`` is a Waypoint control command the host intercepts (see
  // ``handleTerminalSubmit``) rather than forwarding to the wrapped CLI, so
  // we keep the default local fallback to surface it instantly. ``/fork`` is
  // not offered here — the backend omits it from tmux completions. Other
  // entries come from the wrapped backend (CC built-ins, workspace skills).
  const {
    suggestions,
    suggestionsOpen,
    activeIndex,
    setActiveIndex,
    listRef: suggestionsRef,
    itemRefs: suggestionItemRefs,
    applySuggestion,
    handleSuggestionKey,
    reset: resetCompletions,
  } = useCommandCompletions({
    host,
    token,
    sessionId,
    draft,
    setDraft,
    enabled: expanded,
    textareaRef,
  });

  const mentions = useFileMentions({
    host,
    token,
    sessionId,
    draft,
    setDraft,
    enabled: expanded && attachmentsEnabled,
    onReference: attachments.referenceExisting,
    textareaRef,
  });

  // Auto-grow the field: a single line by default (matching the flanking
  // buttons), growing as the draft wraps up to the CSS max-height.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [draft]);

  const dirty = draft.trim().length > 0;
  const connected = connection === "open";
  const hasAttachments = attachments.readyIds.length > 0;
  // Not gated on ``connected``: control commands like ``/new`` work without
  // the socket, and ``send`` surfaces a retry hint for plain text when the WS
  // is closed — so the button mirrors the ⌘↵ path rather than greying out.
  const canSend =
    expanded && (dirty || hasAttachments) && !sending && !attachments.uploading;

  const send = useCallback(async () => {
    const text = draft;
    const attachmentIds = attachments.readyIds;
    if ((!text.trim() && attachmentIds.length === 0) || attachments.uploading) {
      return;
    }
    // No connection gate here: control commands like ``/new`` are handled by
    // the host and don't need the socket, so ``onSubmit`` decides whether the
    // WS was actually required.
    setSending(true);
    try {
      const result = await onSubmit(text, attachmentIds);
      if (result === "ok") {
        setDraft("");
        setHint(null);
        attachments.clear();
        resetCompletions();
      } else if (result === "socket-closed") {
        setHint(
          connected
            ? "Socket not open — try again in a moment"
            : "Reconnecting — try again in a moment",
        );
      }
      // ``command-error``: the host already surfaced the error; keep the draft.
    } finally {
      // Briefly hold the disabled state so the user gets feedback.
      window.setTimeout(() => setSending(false), 120);
    }
  }, [draft, connected, onSubmit, attachments, resetCompletions]);

  const addFilesFromInput = (event: ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files ? Array.from(event.target.files) : [];
    if (files.length) attachments.addFiles(files);
    event.target.value = "";
  };

  const handlePaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    if (!attachmentsEnabled) return;
    const files = filesFromDataTransfer(event.clipboardData);
    if (files.length === 0) return;
    event.preventDefault();
    attachments.addFiles(files);
  };

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      // The suggestion/mention menus own Tab/Enter/Arrows/Esc when open, so
      // Esc-to-dismiss takes precedence over Esc-to-collapse-drawer.
      if (mentions.handleKey(event)) {
        return;
      }
      if (handleSuggestionKey(event)) {
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        onExpandedChange(false);
        // Defer so the textarea blurs before we move focus.
        window.setTimeout(refocusTerminal, 0);
        return;
      }
      if (!isModifiedEnterShortcut(event)) return;
      event.preventDefault();
      send();
    },
    [mentions, handleSuggestionKey, send, onExpandedChange, refocusTerminal],
  );

  const toggle = useCallback(() => {
    onExpandedChange(!expanded);
    if (expanded) {
      window.setTimeout(refocusTerminal, 0);
    }
  }, [expanded, onExpandedChange, refocusTerminal]);

  return (
    <section
      className={`term-compose ${expanded ? "is-open" : "is-closed"}`}
      aria-label="Quick compose"
    >
      <button
        ref={handleRef}
        type="button"
        className="term-compose-handle"
        aria-expanded={expanded}
        aria-controls={regionId}
        aria-label={expanded ? "Collapse quick compose" : "Expand quick compose"}
        title={expanded ? "Collapse" : "Quick compose"}
        onClick={toggle}
      >
        <span className="term-compose-handle-pill" aria-hidden="true" />
      </button>
      <div className="term-compose-shell" id={regionId} aria-hidden={!expanded}>
        <div className="term-compose-inner">
          {hint ? (
            <div className="term-compose-rail">
              <span className="term-compose-hint" role="status">
                {hint}
              </span>
            </div>
          ) : null}
          <div
            className={`term-compose-field${leadForced ? " lead-forced" : ""}`}
          >
            {attachmentsEnabled ? (
              <>
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  className="composer-file-input"
                  onChange={addFilesFromInput}
                  aria-hidden="true"
                  tabIndex={-1}
                />
                <button
                  type="button"
                  className="term-compose-act term-compose-files"
                  onClick={() => setFilesOpen(true)}
                  title="Session files"
                  aria-label="Session files"
                  tabIndex={expanded ? 0 : -1}
                >
                  <span className="glyph" aria-hidden>
                    <FilesIcon />
                  </span>
                </button>
                <button
                  type="button"
                  className="term-compose-act term-compose-attach"
                  onClick={() => fileInputRef.current?.click()}
                  title="Attach files"
                  aria-label="Attach files"
                  tabIndex={expanded ? 0 : -1}
                >
                  <span className="glyph" aria-hidden>
                    <PaperclipIcon />
                  </span>
                </button>
              </>
            ) : null}
            <div className="composer-entry">
              <button
                type="button"
                className="term-compose-lead-expand"
                // Keeps the field focused where a press doesn't focus the button itself.
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => {
                  setLeadForced(true);
                  textareaRef.current?.focus();
                }}
                aria-label="Show actions"
                title="Show actions"
                tabIndex={expanded ? 0 : -1}
              >
                ›
              </button>
              <div className="term-compose-input">
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
                  className="composer-textarea term-compose-textarea"
                  rows={1}
                  value={draft}
                  onChange={(event) => {
                    setDraft(event.target.value);
                    setLeadForced(false);
                    if (hint) setHint(null);
                  }}
                  onBlur={() => setLeadForced(false)}
                  onKeyDown={handleKeyDown}
                  onPaste={handlePaste}
                  placeholder="Type a message"
                  aria-label="Message to send to terminal"
                  tabIndex={expanded ? 0 : -1}
                />
                <button
                  type="button"
                  className="term-compose-send"
                  onClick={send}
                  disabled={!canSend}
                  aria-label="Send"
                  title="Send"
                  tabIndex={expanded ? 0 : -1}
                >
                  <span className="composer-send-glyph" aria-hidden>
                    {sending ? "…" : "↑"}
                  </span>
                </button>
              </div>
            </div>
          </div>
        </div>
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
}
