"use client";

import { type CSSProperties, type RefObject, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { usePopoverAnchor } from "@/lib/use-popover-anchor";

const NARROW_MAX_WIDTH = 540;
const NARROW_INSET = 12;

interface FocusPillProps {
  onTurnOff: () => void | Promise<void>;
  busy?: boolean;
  // Terminal placement: the panel drops below the trigger and is portaled out
  // of ``.session-terminal``'s ``overflow: hidden`` box, as the usage panel is.
  anchored?: boolean;
  // Receives keyboard focus once a turn-off unmounts the pill from under it.
  returnFocusRef?: RefObject<HTMLElement | null>;
}

export function FocusPill({
  onTurnOff,
  busy = false,
  anchored = false,
  returnFocusRef,
}: FocusPillProps) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const offRef = useRef<HTMLButtonElement | null>(null);
  const turnOffFocusedRef = useRef(false);

  useEffect(() => {
    const turnOffFocused = turnOffFocusedRef;
    const focusTarget = returnFocusRef?.current;
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

  // A turn-off that settles with the pill still mounted failed; forget it so a
  // later unmount by another path doesn't move focus.
  useEffect(() => {
    if (!busy) turnOffFocusedRef.current = false;
  }, [busy]);

  // The portaled panel sits at the end of <body>, far from the trigger in tab
  // order, so move focus into it.
  useEffect(() => {
    if (open && anchored) offRef.current?.focus();
  }, [open, anchored]);

  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${NARROW_MAX_WIDTH}px)`);
    const onChange = () => setNarrow(mq.matches);
    onChange();
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const { style: anchorStyle } = usePopoverAnchor(wrapRef, open && anchored, "left");
  // On a phone the dropdown spans the viewport below the term-bar rather than
  // hanging off a trigger that sits mid-bar.
  const panelStyle: CSSProperties | undefined =
    anchorStyle && narrow
      ? { ...anchorStyle, left: NARROW_INSET, right: NARROW_INSET, width: "auto" }
      : (anchorStyle ?? undefined);

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
          void onTurnOff();
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
      {open
        ? anchored && typeof document !== "undefined"
          ? createPortal(panel, document.body)
          : panel
        : null}
    </div>
  );
}
