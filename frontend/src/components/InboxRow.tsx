"use client";

import { useSwipeAction } from "@waypoint/web-core/hooks/useSwipeAction";
import type { InboxBlockChip } from "@waypoint/web-core/inbox";
import type { InboxItem } from "@waypoint/web-core/types";

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
  selectMode = false,
  selected = false,
  onToggleSelect,
}: {
  item: InboxItem;
  active: boolean;
  chips: InboxBlockChip[];
  timeLabel: string;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  selectMode?: boolean;
  selected?: boolean;
  onToggleSelect?: (id: string) => void;
}) {
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

  // No swipe-to-delete while selecting; the row is a selection toggle.
  const swipe = useSwipeAction({ disabled: selectMode, onAction: requestDelete });
  const { offset: dx, swiping } = swipe;

  function onClick() {
    // In select mode a tap toggles selection instead of opening the item.
    if (selectMode) {
      onToggleSelect?.(item.id);
      return;
    }
    // Suppress the click that follows a drag so a swipe never also selects.
    if (swipe.consumeClick()) return;
    onSelect(item.id);
  }

  return (
    <div
      className={`inbox-row-wrap${swiping ? " swiping" : ""}${
        selectMode ? " selecting" : ""
      }`}
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
        }${swiping ? " swiping" : ""}${selected ? " selected" : ""}`}
        style={dx ? { transform: `translateX(${dx}px)` } : undefined}
        role={selectMode ? "checkbox" : undefined}
        aria-checked={selectMode ? selected : undefined}
        {...swipe.handlers}
        onClick={onClick}
      >
        {selectMode ? (
          <span
            className={`inbox-check${selected ? " checked" : ""}`}
            aria-hidden="true"
          >
            {selected ? <CheckIcon /> : null}
          </span>
        ) : (
          <span
            className={`inbox-lamp inbox-status-${item.status}`}
            aria-label={item.status}
          />
        )}
        <span className="inbox-row-main">
          <span className="inbox-row-line">
            <span className="inbox-row-from">{item.from_label ?? "unknown"}</span>
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
        {/* Right rail: timestamp pinned top (yields to the hover delete button),
            unread dot pinned bottom so it stays clear and always visible. */}
        <span className="inbox-row-meta">
          <span className="inbox-row-time">{timeLabel}</span>
          {item.read_at ? null : (
            <span className="inbox-row-unread" aria-label="unread" />
          )}
        </span>
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

function CheckIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="12"
      height="12"
      fill="none"
      stroke="currentColor"
      strokeWidth="3"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M20 6 9 17l-5-5" />
    </svg>
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
