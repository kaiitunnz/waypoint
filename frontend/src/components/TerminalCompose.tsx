"use client";

import {
  ChangeEvent,
  ClipboardEvent,
  DragEvent,
  KeyboardEvent,
  PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";

import {
  AttachmentTray,
  filesFromDataTransfer,
  FilesIcon,
  PaperclipIcon,
  useAttachments,
} from "@/components/AttachmentTray";
import { CommandSuggestions } from "@/components/CommandSuggestions";
import { FileMentions } from "@/components/FileMentions";
import { SessionFilesPanel } from "@/components/SessionFilesPanel";
import { SessionUsagePill } from "@/components/SessionUsagePill";
import { useBackendCatalog } from "@/lib/backends";
import {
  COMPOSER_MIN_HEIGHT,
  SHORTCUT_IS_MAC,
  type TerminalSubmitResult,
} from "@/lib/composer";
import { useCommandCompletions } from "@/lib/composer-completions";
import { useFileMentions } from "@/lib/use-file-mentions";
import type { SessionRecord } from "@/lib/types";

type ConnectionState = "idle" | "connecting" | "open" | "reconnecting";

interface TerminalComposeProps {
  host: string;
  token: string;
  sessionId: string;
  session: SessionRecord | null;
  // Resolves a ``TerminalSubmitResult``: ``socket-closed`` keeps the draft
  // and surfaces a retry hint, ``command-error`` keeps it silently (the host
  // already reported the error), ``ok`` clears it. Async because Waypoint
  // control commands (``/new``) are handled by the host rather than the
  // socket, so a submit can succeed even while the WS is reconnecting.
  onSubmit: (text: string) => Promise<TerminalSubmitResult>;
  // Used instead of ``onSubmit`` when the draft carries attachments — routes
  // through the HTTP input endpoint so the server appends the file paths.
  onSubmitWithAttachments: (
    text: string,
    attachmentIds: string[],
  ) => Promise<TerminalSubmitResult>;
  attachmentsEnabled: boolean;
  onError: (message: string) => void;
  expanded: boolean;
  onExpandedChange: (next: boolean) => void;
  connection: ConnectionState;
  rateLimitRefreshBusy: boolean;
  onRateLimitRefresh: () => void | Promise<void>;
  // Focus target when the user dismisses the drawer via Escape — the
  // xterm canvas, so typing resumes inside the pane.
  refocusTerminal: () => void;
}

const HEIGHT_STORAGE_KEY = "waypoint-term-compose-height";
const HEIGHT_FALLBACK = 132;
const HEIGHT_MAX = 320;

export function TerminalCompose({
  host,
  token,
  sessionId,
  session,
  onSubmit,
  onSubmitWithAttachments,
  attachmentsEnabled,
  onError,
  expanded,
  onExpandedChange,
  connection,
  rateLimitRefreshBusy,
  onRateLimitRefresh,
  refocusTerminal,
}: TerminalComposeProps) {
  const regionId = useId();
  const catalog = useBackendCatalog(host || null, token || null, null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [hint, setHint] = useState<string | null>(null);
  const [textareaHeight, setTextareaHeight] = useState<number | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [filesOpen, setFilesOpen] = useState(false);
  const attachments = useAttachments({ host, token, sessionId, onError });

  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const handleRef = useRef<HTMLButtonElement | null>(null);
  // Host for popovers that need to escape ``.term-compose-inner``'s
  // ``overflow: hidden`` clip (the grid-row open animation requires it).
  // The slash-suggestions list and the SessionUsagePill panel both
  // anchor against this element, which is the outer drawer container
  // and lives outside the clipped subtree.
  const composeRef = useRef<HTMLElement | null>(null);
  // ``popoverContainer`` only takes effect once the section ref is
  // attached, which happens on first commit. Mirror it into state so
  // SessionUsagePill re-renders with a valid host after mount.
  const [popoverHost, setPopoverHost] = useState<HTMLElement | null>(null);
  useEffect(() => {
    setPopoverHost(composeRef.current);
  }, []);

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

  // Restore the desktop height preference on mount; mobile media query
  // hides the resize handle so the persisted value is harmless there.
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const stored = window.localStorage.getItem(HEIGHT_STORAGE_KEY);
      if (!stored) return;
      const parsed = Number.parseInt(stored, 10);
      if (Number.isFinite(parsed) && parsed >= COMPOSER_MIN_HEIGHT) {
        setTextareaHeight(Math.min(parsed, HEIGHT_MAX));
      }
    } catch {
      // localStorage can throw in private windows — fall back to default.
    }
  }, []);

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
      const result = attachmentIds.length
        ? await onSubmitWithAttachments(text, attachmentIds)
        : await onSubmit(text);
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
  }, [draft, connected, onSubmit, onSubmitWithAttachments, attachments, resetCompletions]);

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
      if (event.key !== "Enter" || event.shiftKey) return;
      if (!event.metaKey && !event.ctrlKey) return;
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

  // Desktop resize handle on the top edge of the textarea. Drag-up
  // grows; drag-down shrinks. Mirrors the chat composer's behaviour so
  // the gesture feels identical between the two surfaces.
  const handlePointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.pointerType === "touch") return;
    const startY = event.clientY;
    const startHeight =
      textareaRef.current?.getBoundingClientRect().height ?? HEIGHT_FALLBACK;
    event.currentTarget.setPointerCapture(event.pointerId);
    const onMove = (e: PointerEvent) => {
      const delta = startY - e.clientY;
      const next = Math.min(
        HEIGHT_MAX,
        Math.max(COMPOSER_MIN_HEIGHT, Math.round(startHeight + delta)),
      );
      setTextareaHeight(next);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      try {
        if (textareaRef.current) {
          window.localStorage.setItem(
            HEIGHT_STORAGE_KEY,
            String(textareaRef.current.getBoundingClientRect().height),
          );
        }
      } catch {
        // Ignore storage failures — height resets to default next load.
      }
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  }, []);

  const shortcutLabel = SHORTCUT_IS_MAC ? "⌘↵" : "Ctrl+↵";

  return (
    <section
      ref={composeRef}
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
          <div
            className={`term-compose-field${dragActive ? " is-drag-active" : ""}`}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          >
            <div
              className="composer-resize-handle term-compose-resize"
              onPointerDown={handlePointerDown}
              aria-hidden="true"
            />
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
              style={textareaHeight ? { height: textareaHeight } : undefined}
              rows={3}
              value={draft}
              onChange={(event) => {
                setDraft(event.target.value);
                if (hint) setHint(null);
              }}
              onKeyDown={handleKeyDown}
              onPaste={handlePaste}
              placeholder="Type a message"
              aria-label="Message to send to terminal"
              tabIndex={expanded ? 0 : -1}
            />
            {dragActive ? (
              <div className="composer-drop-hint" aria-hidden="true">
                <span className="composer-drop-glyph">⤓</span>
                <span>Drop to attach</span>
              </div>
            ) : null}
          </div>
          <div className="term-compose-meta">
            <SessionUsagePill
              session={session}
              connection={connection}
              catalog={catalog}
              onRateLimitRefresh={onRateLimitRefresh}
              rateLimitRefreshBusy={rateLimitRefreshBusy}
              popoverContainer={popoverHost}
            />
            {hint ? (
              <span className="term-compose-hint" role="status">
                {hint}
              </span>
            ) : null}
            <span className="term-compose-meta-spacer" />
            <span className="composer-shortcut term-compose-shortcut" aria-hidden="true">
              <kbd>{shortcutLabel}</kbd>
              <span>send</span>
            </span>
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
                  className="ghost composer-attach term-compose-files"
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
                  className="ghost composer-attach term-compose-attach"
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
            <button
              type="button"
              className="primary send term-compose-send"
              onClick={send}
              disabled={!canSend}
              tabIndex={expanded ? 0 : -1}
            >
              {sending ? "Sending…" : "Send"}
            </button>
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
