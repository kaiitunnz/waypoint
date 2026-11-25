"use client";

import { startTransition, useCallback, useEffect, useReducer, useRef, useState } from "react";

import { ApiError, connectSessionSocket, fetchEvents, fetchSession, isAuthError } from "../api";
import { reconnectDelay } from "../session/connection";
import { INITIAL_STREAM_STATE, maxEventId, streamReducer, takeNewer, type StreamState } from "../session/stream";
import { sanitizeEvent } from "../session/transcript";
import type { EventRecord, PendingQuestionsSnapshot, SessionRecord } from "../types";

export interface SessionStream extends StreamState {
  // True until the tail page has loaded or the load has failed.
  loading: boolean;
  loadingOlder: boolean;
  loadOlder: () => void;
  // Takes a session record the server returned, ahead of its next session frame.
  setSession: (session: SessionRecord) => void;
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
    // The highest event id applied: where a catch-up resumes.
    let latestId: number | null = null;
    let loaded = false;
    let loading = false;
    let loadFailures = 0;
    let loadRetry: ReturnType<typeof setTimeout> | null = null;
    // A session that does not exist stays missing: nothing retries or reconnects.
    let missing = false;
    // Live events wait until the tail page is in and while a catch-up runs, so each
    // reaches the reducer once and in order.
    let holds = 1;
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

    const flush = () => {
      frame = null;
      if (holds > 0) return;
      const batch = takeNewer(pending, latestId);
      pending = [];
      latestId = maxEventId(batch, latestId);
      if (batch.length > 0) startTransition(() => dispatch({ type: "events", events: batch }));
    };
    const scheduleFlush = () => {
      if (frame === null && holds === 0) frame = window.requestAnimationFrame(flush);
    };
    const release = () => {
      holds -= 1;
      if (active && pending.length > 0) scheduleFlush();
    };

    // The socket sends nothing for the time before it subscribed or while it was
    // closed, so ask for what came after the last event applied.
    const catchUp = () => {
      holds += 1;
      fetchEvents(host, token, sessionId, { afterId: latestId ?? 0 })
        .then(
          (page) => {
            if (!active) return;
            attempt = 0;
            const missed = takeNewer(page.events.map(sanitizeEvent), latestId);
            if (missed.length === 0) return;
            latestId = maxEventId(missed, latestId);
            dispatch({ type: "events", events: missed });
          },
          (error) => {
            if (!active) return;
            // Held events would move the cursor past the hole: drop them, and let the
            // reconnect catch up from where the transcript really ends.
            pending = [];
            socket?.close();
            if (error instanceof ApiError && error.status === 404) {
              missing = true;
              if (reconnectTimer !== null) clearTimeout(reconnectTimer);
            }
            fail(error, "failed to refresh the transcript");
          },
        )
        .finally(release);
    };

    const load = () => {
      if (!active || loaded || loading || missing) return;
      if (loadRetry !== null) clearTimeout(loadRetry);
      loadRetry = null;
      loading = true;
      Promise.all([fetchSession(host, token, sessionId), fetchEvents(host, token, sessionId)]).then(
        ([session, page]) => {
          if (!active) return;
          loading = false;
          loaded = true;
          latestId = maxEventId(page.events, latestId);
          dispatch({ type: "loaded", session, page });
          // The socket may have subscribed after the page was read.
          catchUp();
          release();
        },
        (error) => {
          loading = false;
          fail(error, "failed to load session");
          missing = error instanceof ApiError && error.status === 404;
          if (!active || isAuthError(error)) return;
          if (missing) {
            if (reconnectTimer !== null) clearTimeout(reconnectTimer);
            socket?.close();
          } else {
            // The socket may already be open, so its opening cannot be what retries.
            loadRetry = setTimeout(load, reconnectDelay(loadFailures++));
          }
        },
      );
    };
    load();

    const connect = () => {
      let questionsHydrated = false;
      dispatch({ type: "connection", connection: attempt === 0 ? "connecting" : "reconnecting" });
      socket = connectSessionSocket(
        host,
        token,
        sessionId,
        (message) => {
          if (message.type === "event") {
            // Before the page loads, the catch-up that follows it reads these events anyway.
            if (loaded) {
              pending.push(sanitizeEvent(message.payload.event as EventRecord));
              scheduleFlush();
            }
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
            dispatch({ type: "connection", connection: "open" });
            // Before the page loads, its own completion catches up; a load that failed is retried.
            if (loaded) catchUp();
            else load();
          },
          onClose: () => {
            if (!active || missing) return;
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
      if (loadRetry !== null) clearTimeout(loadRetry);
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

  const setSession = useCallback((session: SessionRecord) => dispatch({ type: "session", session }), []);

  return { ...state, loading: !state.loaded && state.error === null, loadingOlder, loadOlder, setSession };
}
