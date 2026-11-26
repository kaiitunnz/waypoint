"use client";

import { useCallback, useRef, useState } from "react";

import {
  terminalInteractive,
  terminalKeyInjection,
  terminalResizable,
  type BackendCatalog,
} from "@waypoint/web-core/backends";
import { useTerminalConnection } from "@waypoint/web-core/hooks/useTerminalConnection";
import type { XTerminalHandle } from "@waypoint/web-core/hooks/useXTerminal";
import type { SessionRecord } from "@waypoint/web-core/types";

import { handleAuthFailure } from "@/lib/auth";
import { useShellData } from "@/lib/shell-data";

interface Options {
  sessionId: string;
  session: SessionRecord | null;
  catalog: BackendCatalog;
  // The Terminal view is showing.
  active: boolean;
}

// A session's terminal pane: its socket, its xterm and its input lock.
export function useTerminalPane({ sessionId, session, catalog, active }: Options) {
  const { host, token } = useShellData();
  const term = useRef<XTerminalHandle | null>(null);
  const transport = session?.transport ?? null;
  const interactive = transport !== null && terminalInteractive(transport, catalog);
  const keyInjection = transport !== null && terminalKeyInjection(transport, catalog);
  const resizable = transport !== null && terminalResizable(transport, catalog);
  // A key-injection pane that is not interactive is locked until unlocked,
  // and locks again on another session.
  const lockable = keyInjection && !interactive;
  const [unlockedId, setUnlockedId] = useState<string | null>(null);
  if (unlockedId !== null && unlockedId !== sessionId) setUnlockedId(null);
  const unlocked = lockable && unlockedId === sessionId;

  const connection = useTerminalConnection({
    host,
    token,
    sessionId,
    session,
    catalog,
    active,
    interactive: unlocked,
    sink: term,
    onAuthFailure: handleAuthFailure,
  });

  const toggleLock = useCallback(() => {
    const next = !unlocked;
    setUnlockedId(next ? sessionId : null);
    // Synchronous, so the focus in this tap raises a phone's keyboard.
    term.current?.setInputEnabled(next);
    if (next) term.current?.focus();
    else term.current?.blur();
  }, [unlocked, sessionId]);

  return {
    term,
    connection,
    interactive,
    keyInjection,
    resizable,
    lockable,
    unlocked,
    // The person can type into the pane.
    typable: interactive || unlocked,
    toggleLock,
  };
}

export type TerminalPane = ReturnType<typeof useTerminalPane>;
