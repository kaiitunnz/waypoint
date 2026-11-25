"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { isSwitcherShortcut, ownsShortcutKeys } from "@waypoint/web-core/keyboard";

import { SessionSwitcher } from "./SessionSwitcher";

interface SwitcherControls {
  open: () => void;
}

const SwitcherContext = createContext<SwitcherControls | null>(null);

export function useSwitcher(): SwitcherControls {
  const value = useContext(SwitcherContext);
  if (!value) {
    throw new Error("useSwitcher needs a SwitcherProvider");
  }
  return value;
}

// A side drawer is a place to switch from, so the shortcut still answers over it.
const hasModalDialog = () => document.querySelector('[role="dialog"][aria-modal="true"]:not([data-drawer])') !== null;

// Mounted once for the signed-in app: Cmd/Ctrl+K toggles the switcher from every
// screen, and a screen's title can open it through `useSwitcher`.
export function SwitcherProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.repeat || event.defaultPrevented) return;
      if (!isSwitcherShortcut(event) || ownsShortcutKeys(event.target)) return;
      if (!open && hasModalDialog()) return;
      event.preventDefault();
      setOpen(!open);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  const controls = useMemo(() => ({ open: () => setOpen(true) }), []);
  return (
    <SwitcherContext.Provider value={controls}>
      {children}
      {open ? <SessionSwitcher onClose={close} /> : null}
    </SwitcherContext.Provider>
  );
}
