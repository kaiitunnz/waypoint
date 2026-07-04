"use client";

import { useRef, useState, type PointerEvent } from "react";

import type { InboxItem } from "@/lib/types";

export interface InboxRowChip {
  key: string;
  label: string;
  pending: boolean;
}

// Movement slop before a drag is recognized (vs. a tap), and the leftward
// distance past which releasing deletes.
const SLOP = 8;
const DELETE_THRESHOLD = 96;

// A list row with a left-swipe-to-delete gesture (touch + pointer) layered over
// tap-to-select. The delete button is a real, keyboard-focusable control shown
// on hover/focus, so the gesture is an enhancement — not the only way to delete.
export function InboxRow({
  item,
  active,
  chips,
  timeLabel,
  onSelect,
  onDelete,
}: {
  item: InboxItem;
  active: boolean;
  chips: InboxRowChip[];
  timeLabel: string;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const [dx, setDx] = useState(0);
  const [swiping, setSwiping] = useState(false);
  const start = useRef<{ x: number; y: number } | null>(null);
  const axis = useRef<"none" | "horiz" | "vert">("none");
  const dxRef = useRef(0);
  const draggedRef = useRef(false);

  function setOffset(value: number) {
    dxRef.current = value;
    setDx(value);
  }

  // Both delete paths (swipe past threshold, hover-button click) confirm first
  // — deletion is irreversible — matching the item pane's wording.
  function requestDelete() {
    if (
      typeof window !== "undefined" &&
      !window.confirm("Delete this inbox item? This cannot be undone.")
    ) {
      return;
    }
    onDelete(item.id);
  }

  function onPointerDown(event: PointerEvent<HTMLButtonElement>) {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    start.current = { x: event.clientX, y: event.clientY };
    axis.current = "none";
    draggedRef.current = false;
  }

  function onPointerMove(event: PointerEvent<HTMLButtonElement>) {
    if (!start.current) return;
    const mdx = event.clientX - start.current.x;
    const mdy = event.clientY - start.current.y;
    if (axis.current === "none") {
      if (Math.abs(mdx) < SLOP && Math.abs(mdy) < SLOP) return;
      // Horizontal drags engage the swipe; vertical drags are left to native
      // list scrolling (touch-action: pan-y on the row).
      axis.current = Math.abs(mdx) > Math.abs(mdy) ? "horiz" : "vert";
      if (axis.current === "horiz") {
        setSwiping(true);
        event.currentTarget.setPointerCapture?.(event.pointerId);
      }
    }
    if (axis.current !== "horiz") return;
    draggedRef.current = true;
    setOffset(Math.max(-DELETE_THRESHOLD * 1.6, Math.min(0, mdx)));
  }

  function onPointerFinish() {
    if (axis.current === "horiz") {
      setSwiping(false);
      const passed = Math.abs(dxRef.current) >= DELETE_THRESHOLD;
      setOffset(0);
      if (passed) requestDelete();
    }
    start.current = null;
    axis.current = "none";
  }

  function onClick() {
    // Suppress the click that follows a drag so a swipe never also selects.
    if (draggedRef.current) {
      draggedRef.current = false;
      return;
    }
    onSelect(item.id);
  }

  return (
    <div
      className={`inbox-row-wrap${swiping ? " swiping" : ""}`}
      role="listitem"
    >
      <span className="inbox-row-delete-bg" aria-hidden="true">
        <TrashIcon />
        <span>Delete</span>
      </span>
      <button
        type="button"
        className={`inbox-row${active ? " active" : ""}${
          item.read_at ? "" : " unread"
        }${swiping ? " swiping" : ""}`}
        style={dx ? { transform: `translateX(${dx}px)` } : undefined}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerFinish}
        onPointerCancel={onPointerFinish}
        onClick={onClick}
      >
        <span
          className={`inbox-lamp inbox-status-${item.status}`}
          aria-label={item.status}
        />
        <span className="inbox-row-main">
          <span className="inbox-row-line">
            <span className="inbox-row-from">{item.from_label ?? "unknown"}</span>
            <span className="inbox-row-time">{timeLabel}</span>
          </span>
          <span className="inbox-row-subject">{item.subject}</span>
          {chips.length > 0 ? (
            <span className="inbox-row-tags">
              {chips.map((chip) => (
                <span
                  key={chip.key}
                  className={`inbox-row-tag${chip.pending ? " pending" : ""}`}
                >
                  {chip.label}
                </span>
              ))}
            </span>
          ) : null}
        </span>
        {item.read_at ? null : (
          <span className="inbox-row-unread" aria-label="unread" />
        )}
      </button>
      <button
        type="button"
        className="inbox-row-delete"
        aria-label={`Delete ${item.subject}`}
        onClick={requestDelete}
      >
        <TrashIcon />
      </button>
    </div>
  );
}

function TrashIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="15"
      height="15"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 6h18" />
      <path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2" />
      <path d="M6 6v14a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V6" />
      <path d="M10 11v6M14 11v6" />
    </svg>
  );
}
