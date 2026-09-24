"use client";

import { type CSSProperties, type RefObject, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { useMediaQuery } from "@/lib/use-media-query";
import { usePopoverAnchor } from "@/lib/use-popover-anchor";
import { usePopoverDismiss } from "@/lib/use-popover-dismiss";

const NARROW_INSET = 12;

interface FocusPillProps {
  onTurnOff: () => void;
  busy: boolean;
  // Focused after a turn-off unmounts the pill.
  returnFocusRef: RefObject<HTMLElement | null>;
  // Portals the panel below the trigger, out of .session-terminal's overflow: hidden.
  anchored?: boolean;
}

export function FocusPill({ onTurnOff, busy, returnFocusRef, anchored = false }: FocusPillProps) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const offRef = useRef<HTMLButtonElement | null>(null);
  const turnOffFocusedRef = useRef(false);
  const narrow = useMediaQuery("(max-width: 540px)");

  usePopoverDismiss(open, setOpen, wrapRef, panelRef, triggerRef);

  useEffect(() => {
    const turnOffFocused = turnOffFocusedRef;
    const focusTarget = returnFocusRef.current;
    return () => {
      if (!turnOffFocused.current) return;
      // Defer past the DOM removal that drops focus to <body>.
      window.setTimeout(() => {
        if (document.activeElement === document.body || !document.activeElement) {
          focusTarget?.focus();
        }
      });
    };
  }, [returnFocusRef]);

  // Settling while still mounted means the turn-off failed.
  useEffect(() => {
    if (!busy) turnOffFocusedRef.current = false;
  }, [busy]);

  // The portaled panel is last in <body>, far from the trigger in tab order.
  useEffect(() => {
    if (open && anchored) offRef.current?.focus();
  }, [open, anchored]);

  const { style: anchorStyle } = usePopoverAnchor(wrapRef, open && anchored, "left");
  const panelStyle: CSSProperties | undefined =
    anchorStyle && narrow
      ? { ...anchorStyle, left: NARROW_INSET, right: NARROW_INSET, width: "auto" }
      : (anchorStyle ?? undefined);

  const panel = (
    <div
      ref={panelRef}
      className="focus-pill-panel"
      style={panelStyle}
      role="dialog"
      aria-label="Focus"
    >
      <span className="focus-pill-panel-title">
        <span className="focus-mark" aria-hidden />
        Focus is on
      </span>
      <p>Messages from agents and schedules are held until you release them.</p>
      <button
        ref={offRef}
        type="button"
        className="focus-pill-off"
        disabled={busy}
        onClick={() => {
          turnOffFocusedRef.current = document.activeElement === offRef.current;
          onTurnOff();
        }}
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
        <span className="focus-mark" aria-hidden />
        <span className="focus-pill-label" aria-hidden>
          Focus
        </span>
      </button>
      {open ? (anchored ? createPortal(panel, document.body) : panel) : null}
    </div>
  );
}
