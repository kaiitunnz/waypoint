"use client";

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

import { hasTerminalPane, terminalResizable, type BackendCatalog } from "../backends";
import { isExitedSession } from "../session/send";
import type { TerminalAppearance } from "../session/terminal";
import {
  openTerminalConnection,
  type TerminalConnection,
  type TerminalConnectionState,
  type TerminalSink,
} from "../session/terminalConnection";
import type { SessionRecord } from "../types";

interface Options {
  host: string;
  token: string;
  sessionId: string;
  session: SessionRecord | null;
  catalog: BackendCatalog;
  // Connects only while the pane is on screen.
  active: boolean;
  // The pane accepts typing (a key-injection pane the person unlocked).
  interactive: boolean;
  sink: RefObject<TerminalSink | null>;
  onAuthFailure: () => void;
}

export interface TerminalConnectionHandle {
  state: TerminalConnectionState | null;
  // The agent's own TUI ground; dark until the server says otherwise.
  appearance: TerminalAppearance;
  input: (data: string) => boolean;
  submit: (text: string) => boolean;
  resize: (cols: number, rows: number) => boolean;
  // Reconnects and reseeds the pane.
  refresh: () => void;
}

// The session's terminal socket. It reconnects when asked, when the session
// comes back from exited and when its tmux pane changes.
export function useTerminalConnection({
  host,
  token,
  sessionId,
  session,
  catalog,
  active,
  interactive,
  sink,
  onAuthFailure,
}: Options): TerminalConnectionHandle {
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

  // Keyed on what the transport offers, not the session record or the catalog:
  // a new reference of either would reconnect, blanking and repainting the pane.
  const transport = session?.transport ?? null;
  const hasPane = transport !== null && hasTerminalPane(transport, catalog);
  const resizable = transport !== null && terminalResizable(transport, catalog);
  useEffect(() => {
    if (!active || !hasPane) return;
    const opened = openTerminalConnection({
      host,
      token,
      sessionId,
      resizable,
      interactive,
      sink: () => sink.current,
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
  }, [active, hasPane, resizable, host, token, sessionId, interactive, sink, epoch]);

  const input = useCallback((data: string) => connection.current?.input(data) ?? false, []);
  const submit = useCallback((text: string) => connection.current?.submit(text) ?? false, []);
  const resize = useCallback((cols: number, rows: number) => connection.current?.resize(cols, rows) ?? false, []);
  const refresh = useCallback(() => setEpoch((value) => value + 1), []);

  return { state, appearance, input, submit, resize, refresh };
}
