"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  hasTerminalPane,
  terminalInteractive,
  terminalKeyInjection,
  terminalResizable,
  type BackendCatalog,
} from "../backends";
import { isExitedSession } from "../session/send";
import type { TerminalAppearance } from "../session/terminal";
import {
  openTerminalConnection,
  type TerminalConnection,
  type TerminalConnectionState,
} from "../session/terminalConnection";
import type { SessionRecord } from "../types";
import type { XTerminalHandle } from "./useXTerminal";

interface Options {
  host: string;
  token: string;
  sessionId: string;
  session: SessionRecord | null;
  catalog: BackendCatalog;
  // The pane is on screen; the socket is open only then.
  active: boolean;
  onAuthFailure: () => void;
}

// A session's terminal pane: its capabilities, input lock and socket. The
// socket reconnects when asked, when the session comes back from exited and
// when its tmux pane changes.
export function useTerminalPane({ host, token, sessionId, session, catalog, active, onAuthFailure }: Options) {
  const term = useRef<XTerminalHandle | null>(null);
  const transport = session?.transport ?? null;
  const hasPane = transport !== null && hasTerminalPane(transport, catalog);
  const interactive = transport !== null && terminalInteractive(transport, catalog);
  const keyInjection = transport !== null && terminalKeyInjection(transport, catalog);
  const resizable = transport !== null && terminalResizable(transport, catalog);
  // A key-injection pane that is not interactive is locked until unlocked, and
  // locks again on another session.
  const lockable = keyInjection && !interactive;
  const [unlockedId, setUnlockedId] = useState<string | null>(null);
  if (unlockedId !== null && unlockedId !== sessionId) setUnlockedId(null);
  const unlocked = lockable && unlockedId === sessionId;

  const [epoch, setEpoch] = useState(0);
  const [state, setState] = useState<TerminalConnectionState | null>(null);
  const [appearance, setAppearance] = useState<TerminalAppearance>("dark");
  const connection = useRef<TerminalConnection | null>(null);
  const authFailure = useRef(onAuthFailure);
  useEffect(() => {
    authFailure.current = onAuthFailure;
  }, [onAuthFailure]);

  const exited = isExitedSession(session);
  const wasExited = useRef(exited);
  useEffect(() => {
    if (wasExited.current && !exited) setEpoch((value) => value + 1);
    wasExited.current = exited;
  }, [exited]);

  const tmuxPane = typeof session?.transport_state?.tmux_pane === "string" ? session.transport_state.tmux_pane : null;
  const lastPane = useRef<string | null>(null);
  useEffect(() => {
    if (lastPane.current !== null && tmuxPane !== null && tmuxPane !== lastPane.current) setEpoch((value) => value + 1);
    lastPane.current = tmuxPane;
  }, [tmuxPane]);

  // Keyed on capabilities, not the session record or the catalog: a new
  // reference of either would reconnect, blanking and repainting the pane.
  useEffect(() => {
    if (!active || !hasPane) return;
    const opened = openTerminalConnection({
      host,
      token,
      sessionId,
      resizable,
      interactive: unlocked,
      sink: () => term.current,
      onState: setState,
      onAppearance: setAppearance,
      onAuthFailure: () => authFailure.current(),
    });
    connection.current = opened;
    return () => {
      opened.close();
      if (connection.current === opened) connection.current = null;
      setState(null);
    };
  }, [active, hasPane, resizable, unlocked, host, token, sessionId, epoch]);

  const input = useCallback((data: string) => connection.current?.input(data) ?? false, []);
  const submit = useCallback((text: string) => connection.current?.submit(text) ?? false, []);
  const resize = useCallback((cols: number, rows: number) => connection.current?.resize(cols, rows) ?? false, []);
  const refresh = useCallback(() => setEpoch((value) => value + 1), []);

  const toggleLock = useCallback(() => {
    const next = !unlocked;
    setUnlockedId(next ? sessionId : null);
    term.current?.setInputEnabled(next);
    if (next) term.current?.focus();
    else term.current?.blur();
  }, [unlocked, sessionId]);

  return {
    term,
    // Null while no connection is open, as when the pane is off screen.
    state,
    // The agent's own TUI ground; dark until the server says otherwise.
    appearance,
    input,
    submit,
    resize,
    // Reconnects and reseeds the pane.
    refresh,
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
