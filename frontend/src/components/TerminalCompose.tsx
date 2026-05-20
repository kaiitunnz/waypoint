"use client";

import {
  KeyboardEvent,
  PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";

import { SessionUsagePill } from "@/components/SessionUsagePill";
import { COMPOSER_MIN_HEIGHT, SHORTCUT_IS_MAC } from "@/lib/composer";
import type { SessionRecord } from "@/lib/types";

type ConnectionState = "idle" | "connecting" | "open" | "reconnecting";

interface TerminalComposeProps {
  session: SessionRecord | null;
  // ``onSubmit`` returns false when the socket isn't open so we can leave
  // the draft in place and surface a hint without throwing it away.
  onSubmit: (text: string) => boolean;
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
  session,
  onSubmit,
  expanded,
  onExpandedChange,
  connection,
  rateLimitRefreshBusy,
  onRateLimitRefresh,
  refocusTerminal,
}: TerminalComposeProps) {
  const regionId = useId();
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [hint, setHint] = useState<string | null>(null);
  const [textareaHeight, setTextareaHeight] = useState<number | null>(null);

  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const handleRef = useRef<HTMLButtonElement | null>(null);

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
  const canSend = expanded && connected && dirty && !sending;

  const send = useCallback(() => {
    const text = draft;
    if (!text.trim()) return;
    if (!connected) {
      setHint("Reconnecting — try again in a moment");
      return;
    }
    setSending(true);
    const ok = onSubmit(text);
    if (ok) {
      setDraft("");
      setHint(null);
    } else {
      setHint("Socket not open — try again in a moment");
    }
    // The send call resolves synchronously (it's just a WebSocket.send),
    // but we briefly hold the disabled state so the user gets feedback.
    window.setTimeout(() => setSending(false), 120);
  }, [draft, connected, onSubmit]);

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
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
    [send, onExpandedChange, refocusTerminal],
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
          <div className="term-compose-field">
            <div
              className="composer-resize-handle term-compose-resize"
              onPointerDown={handlePointerDown}
              aria-hidden="true"
            />
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
              placeholder="Type a message"
              aria-label="Message to send to terminal"
              tabIndex={expanded ? 0 : -1}
            />
          </div>
          <div className="term-compose-meta">
            <SessionUsagePill
              session={session}
              connection={connection}
              onRateLimitRefresh={onRateLimitRefresh}
              rateLimitRefreshBusy={rateLimitRefreshBusy}
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
    </section>
  );
}
