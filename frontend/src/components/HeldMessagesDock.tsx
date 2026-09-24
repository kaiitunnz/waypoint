"use client";

import { type CSSProperties, useEffect, useRef, useState } from "react";

import { ExpandableText } from "@/components/ExpandableText";
import { HeldMessage } from "@/lib/types";
import { formatRelativeTime } from "@/lib/usage";

function ChevronIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M4 10l4-4 4 4" />
    </svg>
  );
}

interface HeldMessagesDockProps {
  focus: boolean;
  messages: HeldMessage[];
  onRelease: (heldId: string) => Promise<void> | void;
  onCancel: (heldId: string) => Promise<void> | void;
  onReleaseAll: () => Promise<void> | void;
  onCancelAll: () => Promise<void> | void;
}

export function HeldMessagesDock({
  focus,
  messages,
  onRelease,
  onCancel,
  onReleaseAll,
  onCancelAll,
}: HeldMessagesDockProps) {
  const [expanded, setExpanded] = useState(false);
  const [confirmCancelAll, setConfirmCancelAll] = useState(false);
  const [, setTick] = useState(0);
  const [panelRoom, setPanelRoom] = useState<number | null>(null);
  const stripRef = useRef<HTMLDivElement | null>(null);
  const count = messages.length;

  useEffect(() => {
    if (!count) {
      setExpanded(false);
      return;
    }
    const id = window.setInterval(() => setTick((t) => t + 1), 30_000);
    return () => window.clearInterval(id);
  }, [count]);

  useEffect(() => {
    if (!expanded) {
      setConfirmCancelAll(false);
      return;
    }
    setPanelRoom(stripRef.current?.getBoundingClientRect().top ?? null);
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setExpanded(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  const latest = messages[count - 1];
  const awaiting = !focus && messages.every((m) => m.auto_release);
  const state = focus ? "Focus" : awaiting ? "After you respond" : "Focus off";

  return (
    <div
      className={`held-dock${expanded ? " expanded" : ""}${awaiting ? " awaiting" : ""}`}
    >
      {expanded ? (
        <>
          <button
            type="button"
            className="held-dock-scrim"
            aria-label="Close held messages"
            onClick={() => setExpanded(false)}
          />
          <div
            className="held-dock-panel"
            role="dialog"
            aria-label="Held messages"
            style={
              panelRoom === null
                ? undefined
                : ({ "--held-dock-room": `${panelRoom}px` } as CSSProperties)
            }
          >
            <div className="held-dock-panel-head">
              <span className="held-dock-panel-title">Held messages</span>
              <span className="held-dock-count">{count}</span>
              <button
                type="button"
                className="held-dock-collapse"
                aria-label="Collapse held messages"
                onClick={() => setExpanded(false)}
              >
                <ChevronIcon />
              </button>
            </div>
            <div className="held-dock-bulk">
              {confirmCancelAll ? (
                <>
                  <span className="held-dock-bulk-prompt">
                    Cancel {count} held message{count === 1 ? "" : "s"}?
                  </span>
                  <button
                    type="button"
                    className="link-button"
                    onClick={() => setConfirmCancelAll(false)}
                  >
                    Keep
                  </button>
                  <button
                    type="button"
                    className="link-button danger-link"
                    onClick={() => {
                      setConfirmCancelAll(false);
                      void onCancelAll();
                    }}
                  >
                    Cancel all
                  </button>
                </>
              ) : (
                <>
                  <button
                    type="button"
                    className="link-button held-dock-release"
                    onClick={() => void onReleaseAll()}
                  >
                    Release all
                  </button>
                  <button
                    type="button"
                    className="link-button danger-link"
                    onClick={() => setConfirmCancelAll(true)}
                  >
                    Cancel all
                  </button>
                </>
              )}
            </div>
            <div className="held-dock-list">
              {messages.map((m) => (
                <div key={m.id} className="held-dock-item">
                  <div className="held-dock-item-meta">
                    <span className="held-dock-origin">{originLabel(m)}</span>
                    <span className="held-dock-age">{formatRelativeTime(m.created_at)}</span>
                    <span className="held-dock-item-actions">
                      <button
                        type="button"
                        className="link-button held-dock-release"
                        onClick={() => void onRelease(m.id)}
                      >
                        Release
                      </button>
                      <button
                        type="button"
                        className="link-button danger-link"
                        onClick={() => void onCancel(m.id)}
                      >
                        Cancel
                      </button>
                    </span>
                  </div>
                  {m.auto_release && !focus ? (
                    <span className="held-dock-when">Sends after you respond</span>
                  ) : null}
                  <ExpandableText
                    className="held-dock-item-text"
                    text={messageText(m)}
                    collapsedMaxHeight="3em"
                  />
                </div>
              ))}
            </div>
          </div>
        </>
      ) : null}
      <div className="held-dock-strip" ref={stripRef}>
        <button
          type="button"
          className="held-dock-toggle"
          aria-expanded={expanded}
          disabled={!count}
          onClick={() => setExpanded((value) => !value)}
        >
          <span className="held-dock-glyph" aria-hidden>
            ◎
          </span>
          <span className="held-dock-state">{state}</span>
          <span className="held-dock-label">
            {latest ? messageText(latest) : "Holding messages from agents, schedules, and wake-ups"}
          </span>
          {count ? (
            <>
              <span className="held-dock-count">{count} held</span>
              <span className="held-dock-chevron" aria-hidden>
                <ChevronIcon />
              </span>
            </>
          ) : null}
        </button>
      </div>
      <span className="sr-only" aria-live="polite">
        {count} held message{count === 1 ? "" : "s"}
      </span>
    </div>
  );
}

function originLabel(message: HeldMessage): string {
  if (message.origin === "schedule") return "Scheduled";
  if (message.origin === "wake") return "Wake-up";
  return `From ${message.sender_title || message.sender_session_id || "a session"}`;
}

function messageText(message: HeldMessage): string {
  const attached = message.attachments.length;
  const suffix = attached ? ` (+${attached} file${attached === 1 ? "" : "s"})` : "";
  return (message.text || "(no text)") + suffix;
}
