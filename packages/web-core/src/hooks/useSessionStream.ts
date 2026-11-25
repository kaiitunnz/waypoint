"use client";

import { startTransition, useCallback, useEffect, useReducer, useRef, useState, type Dispatch } from "react";

import { connectSessionSocket, fetchEvents, fetchSession, isAuthError } from "../api";
import {
  INITIAL_STREAM_STATE,
  reconnectDelay,
  streamReducer,
  type StreamAction,
  type StreamState,
} from "../session/stream";
import { sanitizeEvent } from "../session/transcript";
import type { EventRecord, PendingQuestionsSnapshot, SessionEnvelope, SessionRecord } from "../types";

export interface SessionStream extends StreamState {
  // True until the session record has loaded.
  loading: boolean;
  loadingOlder: boolean;
  loadOlder: () => void;
  // For server responses that change the session or the events (a rename, an optimistic send).
  dispatch: Dispatch<StreamAction>;
}

interface Options {
  // Every frame the socket delivers, after the stream has applied the ones it owns.
  onFrame?: (message: SessionEnvelope) => void;
}

// One session's record, its events and its socket: the tail page loads first,
// older pages on request, and live events arrive batched per animation frame.
// `onAuthFailure` must be stable.
export function useSessionStream(
  host: string,
  token: string,
  sessionId: string,
  onAuthFailure: () => void,
  options: Options = {},
): SessionStream {
  const [state, dispatch] = useReducer(streamReducer, INITIAL_STREAM_STATE);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const onFrame = useRef(options.onFrame);
  useEffect(() => {
    onFrame.current = options.onFrame;
  });

  useEffect(() => {
    if (!host || !token || !sessionId) return;
    let active = true;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let pending: EventRecord[] = [];
    let frame: number | null = null;
    dispatch({ type: "reset" });

    const fail = (error: unknown, fallback: string) => {
      if (!active) return;
      if (isAuthError(error)) {
        onAuthFailure();
        return;
      }
      dispatch({ type: "error", message: error instanceof Error ? error.message : fallback });
    };

    Promise.all([fetchSession(host, token, sessionId), fetchEvents(host, token, sessionId)]).then(
      ([session, page]) => {
        if (active) dispatch({ type: "loaded", session, page });
      },
      (error) => fail(error, "failed to load session"),
    );

    const flush = () => {
      frame = null;
      const batch = pending;
      pending = [];
      if (batch.length > 0) startTransition(() => dispatch({ type: "events", events: batch }));
    };

    const connect = () => {
      let questionsHydrated = false;
      dispatch({ type: "connection", connection: attempt === 0 ? "connecting" : "reconnecting" });
      socket = connectSessionSocket(
        host,
        token,
        sessionId,
        (message) => {
          if (message.type === "event") {
            pending.push(sanitizeEvent(message.payload.event as EventRecord));
            if (frame === null) frame = window.requestAnimationFrame(flush);
          } else if (message.type === "session_state") {
            dispatch({ type: "session", session: message.payload.session as SessionRecord });
          } else if (message.type === "pending_questions") {
            dispatch({
              type: "pendingQuestions",
              snapshot: message.payload as unknown as PendingQuestionsSnapshot,
              replace: !questionsHydrated,
            });
            questionsHydrated = true;
          } else if (message.type === "auth_revoked") {
            onAuthFailure();
          }
          onFrame.current?.(message);
        },
        () => {
          if (active) onAuthFailure();
        },
        {
          onOpen: () => {
            attempt = 0;
            dispatch({ type: "connection", connection: "open" });
          },
          onClose: () => {
            if (!active) return;
            const delay = reconnectDelay(attempt);
            attempt += 1;
            dispatch({ type: "connection", connection: "reconnecting" });
            reconnectTimer = setTimeout(connect, delay);
          },
        },
      );
    };
    connect();

    return () => {
      active = false;
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      if (frame !== null) window.cancelAnimationFrame(frame);
      pending = [];
      socket?.close();
    };
  }, [host, token, sessionId, onAuthFailure]);

  const { hasOlder, oldestSequence } = state;
  const olderInFlight = useRef(false);
  const loadOlder = useCallback(() => {
    if (olderInFlight.current || !hasOlder || oldestSequence === null) return;
    olderInFlight.current = true;
    setLoadingOlder(true);
    fetchEvents(host, token, sessionId, { beforeSequence: oldestSequence })
      .then(
        (page) => dispatch({ type: "older", page }),
        (error) => {
          if (isAuthError(error)) onAuthFailure();
          else dispatch({ type: "error", message: error instanceof Error ? error.message : "failed to load older messages" });
        },
      )
      .finally(() => {
        olderInFlight.current = false;
        setLoadingOlder(false);
      });
  }, [host, token, sessionId, hasOlder, oldestSequence, onAuthFailure]);

  return { ...state, loading: state.session === null && state.error === null, loadingOlder, loadOlder, dispatch };
}
