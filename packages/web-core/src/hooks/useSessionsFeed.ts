"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  connectSessionsSocket,
  fetchLiveSessions,
  fetchMessageSchedules,
  fetchPendingAsks,
  fetchSchedules,
  isAuthError,
  parseLiveSessionList,
} from "../api";
import { applyPendingAsksSnapshot, parsePendingAsksSnapshot } from "../pendingAsks";
import type { ConnectionState } from "../session/connection";
import { applySessionState, upsertLiveSession } from "../sessionHistory";
import type {
  MessageSchedule,
  PendingAsksSnapshot,
  ScheduledSession,
  SessionEnvelope,
  SessionRecord,
} from "../types";

export interface SessionsFeed {
  // Live and pinned sessions; null until the first list arrives. Exited history
  // is paged separately (sessionHistory).
  sessions: SessionRecord[] | null;
  historyRevision: string;
  pendingAsks: PendingAsksSnapshot | null;
  schedules: ScheduledSession[];
  messageSchedules: MessageSchedule[];
  // Bumped on every `board_update` frame, so board views can refetch.
  boardRevision: number;
  connection: ConnectionState;
  // Applied to the live list ahead of the next list frame.
  upsertSession: (session: SessionRecord) => void;
  removeSessions: (shouldRemove: (session: SessionRecord) => boolean) => void;
  patchSession: (id: string, change: (session: SessionRecord) => SessionRecord) => void;
}

const NO_SCHEDULES: ScheduledSession[] = [];
const NO_MESSAGE_SCHEDULES: MessageSchedule[] = [];

// Everything `/ws/sessions` carries, over one connection: the live session list,
// pending asks (docs/pending_asks.md), schedules and board activity.
// `onAuthFailure` must be stable: a new one restarts the socket.
export function useSessionsFeed(
  host: string,
  token: string,
  onAuthFailure?: () => void,
): SessionsFeed {
  const [sessions, setSessions] = useState<SessionRecord[] | null>(null);
  const [historyRevision, setHistoryRevision] = useState("");
  const [pendingAsks, setPendingAsks] = useState<PendingAsksSnapshot | null>(null);
  const [schedules, setSchedules] = useState(NO_SCHEDULES);
  const [messageSchedules, setMessageSchedules] = useState(NO_MESSAGE_SCHEDULES);
  const [boardRevision, setBoardRevision] = useState(0);
  const [connection, setConnection] = useState<ConnectionState>("connecting");

  useEffect(() => {
    if (!host || !token) {
      return;
    }
    let active = true;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let askAbort: AbortController | null = null;
    // A frame is newer than any REST seed that resolves after it.
    let listFrameSeen = false;
    let scheduleFrameSeen = false;
    let opened = false;

    const failAuth = () => {
      if (active) onAuthFailure?.();
    };
    const ignoreAuth = (error: unknown) => {
      if (isAuthError(error)) failAuth();
    };
    const applyAsks = (incoming: PendingAsksSnapshot) => {
      if (active) setPendingAsks((held) => applyPendingAsksSnapshot(held, incoming));
    };
    const refreshAsks = () => {
      askAbort?.abort();
      const controller = new AbortController();
      askAbort = controller;
      fetchPendingAsks(host, token, controller.signal).then(applyAsks).catch(ignoreAuth);
    };

    fetchLiveSessions(host, token)
      .then((live) => {
        if (!active || listFrameSeen) return;
        setSessions(live.sessions);
        setHistoryRevision(live.historyRevision);
      })
      .catch(ignoreAuth);
    const seedSchedules = (replaceFrames: boolean) =>
      Promise.all([fetchSchedules(host, token), fetchMessageSchedules(host, token)])
        .then(([launches, messages]) => {
          if (!active || (scheduleFrameSeen && !replaceFrames)) return;
          setSchedules(launches);
          setMessageSchedules(messages);
        })
        .catch(ignoreAuth);
    seedSchedules(false);
    refreshAsks();

    function connect() {
      socket = connectSessionsSocket(
        host,
        token,
        (message: SessionEnvelope) => {
          switch (message.type) {
            case "auth_revoked":
              failAuth();
              break;
            case "session_list_update": {
              const live = parseLiveSessionList(message.payload);
              listFrameSeen = true;
              setSessions(live.sessions);
              setHistoryRevision(live.historyRevision);
              break;
            }
            case "session_state":
              applySessionState(host, message.payload.session as SessionRecord);
              break;
            case "pending_asks": {
              const parsed = parsePendingAsksSnapshot(message.payload);
              if (parsed) applyAsks(parsed);
              break;
            }
            case "schedule_list_update":
              scheduleFrameSeen = true;
              setSchedules(message.payload.schedules as ScheduledSession[]);
              if ("message_schedules" in message.payload) {
                setMessageSchedules(message.payload.message_schedules as MessageSchedule[]);
              }
              break;
            case "board_update":
              setBoardRevision((revision) => revision + 1);
              break;
          }
        },
        failAuth,
        {
          onOpen: () => {
            attempt = 0;
            if (active) setConnection("open");
            refreshAsks();
            // The socket replays the session list and asks on connect, not schedules
            // or board activity, so a reconnect re-reads what it missed.
            if (opened) {
              seedSchedules(true);
              setBoardRevision((revision) => revision + 1);
            }
            opened = true;
          },
          onClose: () => {
            if (!active) return;
            setConnection("reconnecting");
            const delay = Math.min(15000, 500 * 2 ** attempt);
            attempt += 1;
            reconnectTimer = setTimeout(connect, delay);
          },
        },
      );
    }

    connect();

    return () => {
      active = false;
      askAbort?.abort();
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      socket?.close();
      setSessions(null);
      setHistoryRevision("");
      setPendingAsks(null);
      setSchedules(NO_SCHEDULES);
      setMessageSchedules(NO_MESSAGE_SCHEDULES);
      setConnection("connecting");
    };
  }, [host, token, onAuthFailure]);

  const upsertSession = useCallback((session: SessionRecord) => {
    setSessions((current) => (current === null ? current : upsertLiveSession(current, session)));
  }, []);

  const removeSessions = useCallback((shouldRemove: (session: SessionRecord) => boolean) => {
    setSessions((current) => current?.filter((session) => !shouldRemove(session)) ?? current);
  }, []);

  const patchSession = useCallback((id: string, change: (session: SessionRecord) => SessionRecord) => {
    setSessions(
      (current) => current?.map((session) => (session.id === id ? change(session) : session)) ?? current,
    );
  }, []);

  return useMemo(
    () => ({
      sessions,
      historyRevision,
      pendingAsks,
      schedules,
      messageSchedules,
      boardRevision,
      connection,
      upsertSession,
      removeSessions,
      patchSession,
    }),
    [
      sessions,
      historyRevision,
      pendingAsks,
      schedules,
      messageSchedules,
      boardRevision,
      connection,
      upsertSession,
      removeSessions,
      patchSession,
    ],
  );
}
