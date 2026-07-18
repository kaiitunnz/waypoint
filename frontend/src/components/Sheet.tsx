"use client";

import { useEffect, useRef, type ReactNode } from "react";

import { trapTabFocus } from "@/lib/keyboard";

// Glass slide-over (desktop) / bottom sheet (mobile) used to host the launch
// form and the schedule manager. Traps focus, closes on Escape or scrim click,
// and returns focus to whatever was focused before it opened.
interface SheetProps {
  open: boolean;
  onClose: () => void;
  eyebrow: string;
  title: string;
  children: ReactNode;
}

export function Sheet({ open, onClose, eyebrow, title, children }: SheetProps) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  // Read onClose through a ref so the focus-management effect below keys on
  // `open` alone. Callers pass a fresh inline onClose each render, and the
  // homepage re-renders continuously as live session/board updates stream in;
  // depending on onClose would re-run the effect every render, and its cleanup
  // (restoreFocusRef.current?.focus()) would yank focus out of whatever field
  // the user is typing in back to the trigger.
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    if (!open) {
      return;
    }
    restoreFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    // Move focus into the sheet on open.
    const raf = requestAnimationFrame(() => {
      panelRef.current?.focus();
    });
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      trapTabFocus(event, panelRef.current, { preventWhenEmpty: true });
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener("keydown", onKeyDown);
      restoreFocusRef.current?.focus();
    };
  }, [open]);

  if (!open) {
    return null;
  }

  return (
    <div className="wp-sheet-layer">
      <div className="wp-sheet-scrim" onClick={onClose} aria-hidden="true" />
      <div
        className="wp-sheet"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        ref={panelRef}
      >
        <div className="wp-sheet-head">
          <div className="wp-sheet-titles">
            <p className="wp-sheet-eyebrow">{eyebrow}</p>
            <span className="wp-sheet-title">{title}</span>
          </div>
          <button
            type="button"
            className="wp-sheet-close"
            onClick={onClose}
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        <div className="wp-sheet-body">{children}</div>
      </div>
    </div>
  );
}
