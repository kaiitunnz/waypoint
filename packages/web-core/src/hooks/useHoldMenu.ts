"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent,
  type RefObject,
} from "react";

// How long a mouse hover or a held touch takes to open the menu.
const HOLD_MS = 450;

export interface HoldMenu {
  open: boolean;
  close: (refocusTrigger?: boolean) => void;
  triggerRef: RefObject<HTMLButtonElement | null>;
  itemRefs: RefObject<(HTMLButtonElement | null)[]>;
  // Around the trigger and the menu: an outside press closes it.
  wrapRef: RefObject<HTMLDivElement | null>;
  wrapProps: {
    onPointerEnter: (event: PointerEvent) => void;
    onPointerLeave: (event: PointerEvent) => void;
  };
  triggerProps: {
    onClick: (event: MouseEvent) => void;
    onPointerDown: (event: PointerEvent) => void;
    onPointerUp: (event: PointerEvent) => void;
    onPointerCancel: (event: PointerEvent) => void;
    onKeyDown: (event: KeyboardEvent) => void;
  };
  menuProps: { onKeyDown: (event: KeyboardEvent) => void };
}

export interface HoldMenuOptions {
  // A tap also closes the menu and disarms the hover until the mouse leaves, so
  // it does not appear after the person has already acted.
  dismissOnActivate?: boolean;
}

// A button that acts on a tap and offers a menu on hover (mouse), a held touch,
// ArrowDown or Space.
export function useHoldMenu(
  disabled: boolean,
  onActivate: () => void,
  { dismissOnActivate = false }: HoldMenuOptions = {},
): HoldMenu {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const timer = useRef<number | null>(null);
  const longPressed = useRef(false);
  // Disabling and enabling a trigger under the mouse fires another pointerenter.
  const hoverSpent = useRef(false);

  const clearTimer = useCallback(() => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  function openMenu(focusFirst: boolean) {
    if (disabled) return;
    setOpen(true);
    // Deferred to after the menu mounts.
    if (focusFirst) requestAnimationFrame(() => itemRefs.current[0]?.focus());
  }

  const close = useCallback((refocusTrigger = false) => {
    setOpen(false);
    if (refocusTrigger) triggerRef.current?.focus();
  }, []);

  // A touch-opened menu has no pointerleave, so an outside press closes it.
  useEffect(() => {
    if (!open) return;
    function onPointer(event: globalThis.PointerEvent) {
      if (wrapRef.current?.contains(event.target as Node)) return;
      setOpen(false);
    }
    window.addEventListener("pointerdown", onPointer);
    return () => window.removeEventListener("pointerdown", onPointer);
  }, [open]);

  useEffect(() => clearTimer, [clearTimer]);

  return {
    open,
    close,
    triggerRef,
    itemRefs,
    wrapRef,
    wrapProps: {
      onPointerEnter: (event) => {
        if (event.pointerType !== "mouse" || disabled || hoverSpent.current) return;
        clearTimer();
        timer.current = window.setTimeout(() => openMenu(false), HOLD_MS);
      },
      onPointerLeave: (event) => {
        if (event.pointerType !== "mouse") return;
        hoverSpent.current = false;
        clearTimer();
        setOpen(false);
      },
    },
    triggerProps: {
      onClick: (event) => {
        // The click that ends a long press only opened the menu.
        if (longPressed.current) {
          longPressed.current = false;
          return;
        }
        if (dismissOnActivate) {
          // `detail` is 0 for a keyboard click, which has no mouse to disarm.
          hoverSpent.current = event.detail > 0;
          clearTimer();
          setOpen(false);
        }
        onActivate();
      },
      onPointerDown: (event) => {
        if (event.pointerType !== "touch" || disabled) return;
        longPressed.current = false;
        clearTimer();
        timer.current = window.setTimeout(() => {
          longPressed.current = true;
          openMenu(false);
        }, HOLD_MS);
      },
      onPointerUp: (event) => {
        if (event.pointerType === "touch") clearTimer();
      },
      onPointerCancel: (event) => {
        if (event.pointerType === "touch") clearTimer();
      },
      onKeyDown: (event) => {
        if (event.key === "ArrowDown" || event.key === " ") {
          event.preventDefault();
          openMenu(true);
        } else if (event.key === "Escape" && open) {
          event.preventDefault();
          close(true);
        }
      },
    },
    menuProps: {
      onKeyDown: (event) => {
        const items = itemRefs.current.filter((item): item is HTMLButtonElement => item !== null);
        const current = items.findIndex((item) => item === document.activeElement);
        if (event.key === "Escape") {
          event.preventDefault();
          close(true);
        } else if (event.key === "ArrowDown") {
          event.preventDefault();
          items[(current + 1 + items.length) % items.length]?.focus();
        } else if (event.key === "ArrowUp") {
          event.preventDefault();
          items[(current - 1 + items.length) % items.length]?.focus();
        }
      },
    },
  };
}
