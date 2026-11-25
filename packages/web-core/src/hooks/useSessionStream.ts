"use client";

import { startTransition, useCallback, useEffect, useReducer, useRef, useState } from "react";

import { ApiError, connectSessionSocket, fetchEvents, fetchSession, isAuthError } from "../api";
import { INITIAL_STREAM_STATE, maxEventId, reconnectDelay, streamReducer, type StreamState } from "../session/stream";
import { sanitizeEvent } from "../session/transcript";
import type { EventRecord, PendingQuestionsSnapshot, SessionRecord } from "../types";

export interface SessionStream extends StreamState {
  // True until the tail page has loaded or the load has failed.
  loading: boolean;
  loadingOlder: boolean;
  loadOlder: () => void;
}

// One session's record, its events and its socket: the tail page loads first,
// older pages on request, and live events arrive batched per animation frame.
// A reconnect fetches whatever the socket missed. `onAuthFailure` must be stable.
export function useSessionStream(
  host: string,
  token: string,
  sessionId: string,
  onAuthFailure: () => void,
): SessionStream {
  const [state, dispatch] = useReducer(streamReducer, INITIAL_STREAM_STATE);
  const [loadingOlder, setLoadingOlder] = useState(false);

  useEffect(() => {
    if (!host || !token || !sessionId) return;
    let active = true;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let pending: EventRecord[] = [];
    let frame: number | null = null;
    // The highest event id handed to the reducer, where a catch-up resumes; unknown until the tail page loads.
    let latestId: number | null = null;
    let loaded = false;
    // Live events wait while a catch-up is in flight, so a streamed message keeps its order.
    let catchingUp = 0;
    dispatch({ type: "reset" });

    const fail = (error: unknown, fallback: string) => {
      if (!active) return;
      if (isAuthError(error)) {
        onAuthFailure();
        return;
      }
      dispatch({
        type: "error",
        message: error instanceof Error ? error.message : fallback,
        status: error instanceof ApiError ? error.status : undefined,
      });
    };

    Promise.all([fetchSession(host, token, sessionId), fetchEvents(host, token, sessionId)]).then(
      ([session, page]) => {
        if (!active) return;
        loaded = true;
        latestId = maxEventId(page.events, latestId);
        dispatch({ type: "loaded", session, page });
      },
      (error) => fail(error, "failed to load session"),
    );

    const flush = () => {
      frame = null;
      // An event at or below the resume point was delivered already.
      const batch = pending.filter((event) => event.id === undefined || latestId === null || event.id > latestId);
      pending = [];
      latestId = maxEventId(batch, latestId);
      if (batch.length > 0) startTransition(() => dispatch({ type: "events", events: batch }));
    };
    const scheduleFlush = () => {
      if (frame === null && catchingUp === 0) frame = window.requestAnimationFrame(flush);
    };

    // The socket sends nothing for the time it was closed or not yet subscribed, so ask for it.
    const catchUp = () => {
      if (!loaded) return;
      catchingUp += 1;
      fetchEvents(host, token, sessionId, { afterId: latestId ?? 0 })
        .then(
          (page) => {
            if (!active || page.events.length === 0) return;
            const missed = page.events.map(sanitizeEvent);
            latestId = maxEventId(missed, latestId);
            dispatch({ type: "events", events: missed });
          },
          (error) => fail(error, "failed to refresh the transcript"),
        )
        .finally(() => {
          catchingUp -= 1;
          if (active && pending.length > 0) scheduleFlush();
        });
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
            scheduleFlush();
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
        },
        () => {
          if (active) onAuthFailure();
        },
        {
          onOpen: () => {
            attempt = 0;
            dispatch({ type: "connection", connection: "open" });
            catchUp();
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

  return { ...state, loading: !state.loaded && state.error === null, loadingOlder, loadOlder };
}
