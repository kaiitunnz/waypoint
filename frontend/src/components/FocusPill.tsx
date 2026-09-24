"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { usePopoverAnchor } from "@/lib/use-popover-anchor";

interface FocusPillProps {
  onTurnOff: () => void | Promise<void>;
  busy?: boolean;
  // Terminal placement: the panel drops below the trigger and is portaled out
  // of ``.session-terminal``'s ``overflow: hidden`` box, as the usage panel is.
  anchored?: boolean;
}

export function FocusPill({ onTurnOff, busy = false, anchored = false }: FocusPillProps) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  const { style: anchorStyle } = usePopoverAnchor(
    wrapRef,
    open && anchored,
    "left",
    { deferBelow: 540 },
    "dropdown",
  );

  useEffect(() => {
    if (!open) return;
    function onDocClick(event: MouseEvent) {
      const target = event.target as Node | null;
      if (!target) return;
      if (wrapRef.current?.contains(target)) return;
      if (panelRef.current?.contains(target)) return;
      const restoreFocus = panelRef.current?.contains(document.activeElement);
      setOpen(false);
      if (restoreFocus) triggerRef.current?.focus();
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const panel = (
    <div
      ref={panelRef}
      className="focus-pill-panel"
      style={anchorStyle ?? undefined}
      role="dialog"
      aria-label="Focus"
    >
      <span className="focus-pill-panel-title">
        <span aria-hidden>◎</span> Focus is on
      </span>
      <p>
        Messages from other sessions, schedules, and wake-ups wait in the
        held-messages dock until you release them.
      </p>
      <button
        type="button"
        className="focus-pill-off"
        disabled={busy}
        onClick={() => void onTurnOff()}
      >
        {busy ? "Turning off…" : "Turn off Focus"}
      </button>
    </div>
  );

  return (
    <div className="focus-pill-wrap" ref={wrapRef}>
      <button
        ref={triggerRef}
        type="button"
        className={`focus-pill${open ? " open" : ""}`}
        title="Focus is on"
        aria-label="Focus is on"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="focus-pill-mark" aria-hidden>
          ◎
        </span>
        <span className="focus-pill-label" aria-hidden>
          Focus
        </span>
      </button>
      {open
        ? anchored && typeof document !== "undefined"
          ? createPortal(panel, document.body)
          : panel
        : null}
    </div>
  );
}
