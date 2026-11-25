"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { isSwitcherShortcut } from "@waypoint/web-core/keyboard";

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

// Mounted once for the signed-in app: Cmd/Ctrl+K toggles the switcher from every
// screen, and a screen's title can open it through `useSwitcher`.
export function SwitcherProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (!isSwitcherShortcut(event)) return;
      event.preventDefault();
      setOpen((value) => !value);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  const controls = useMemo(() => ({ open: () => setOpen(true) }), []);
  return (
    <SwitcherContext.Provider value={controls}>
      {children}
      {open ? <SessionSwitcher onClose={close} /> : null}
    </SwitcherContext.Provider>
  );
}
