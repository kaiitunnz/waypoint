"use client";

import { useEffect, useRef, type ReactNode, type RefObject } from "react";

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
  // When set, initial focus lands on this element instead of the panel. A
  // child's `autoFocus` can't win here: the open rAF below runs after commit
  // and would steal focus back to the panel, so a field that wants focus must
  // route through this ref.
  initialFocusRef?: RefObject<HTMLElement | null>;
}

export function Sheet({
  open,
  onClose,
  eyebrow,
  title,
  children,
  initialFocusRef,
}: SheetProps) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  // Key the focus effect on `open` alone; a fresh inline onClose each render
  // would otherwise re-run it and its cleanup would steal focus mid-typing.
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
    // Move focus into the sheet on open — a named field if one asked for it,
    // otherwise the panel.
    const raf = requestAnimationFrame(() => {
      const target = initialFocusRef?.current ?? panelRef.current;
      target?.focus();
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
    // `initialFocusRef` is a stable ref object from the parent, so listing it
    // here satisfies exhaustive-deps without re-running the effect on renders.
  }, [open, initialFocusRef]);

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
