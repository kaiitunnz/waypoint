"use client";

import { useEffect, useRef, useState } from "react";

import { ActivityLoader, deriveActivity, type SessionActivity } from "../activity";
import { fetchEvents, isAuthError } from "../api";
import type { EventsPage, SessionRecord } from "../types";

// A session's activity, null when it could not be loaded; no entry while it loads.
export type SessionActivities = ReadonlyMap<string, SessionActivity | null>;

const NO_ACTIVITY_MAP: SessionActivities = new Map();
// A streamed reply or a tool pair is one message, so three reach past a bare system note.
const ACTIVITY_MESSAGES = 3;

interface CachedActivity {
  lastEventAt: string;
  activity: SessionActivity;
}

// What was loaded, per host, for the app's lifetime, so a remounted screen shows it
// at once and fetches only the sessions whose `last_event_at` has moved.
const cache = new Map<string, Map<string, CachedActivity>>();

function cachedActivities(host: string): SessionActivities {
  const held = cache.get(host);
  if (!held) return NO_ACTIVITY_MAP;
  return new Map([...held].map(([id, { activity }]) => [id, activity]));
}

// The one-line activity and task progress for each session given, loaded from its
// latest events when its `last_event_at` moves. Pass only the rows on screen; when
// not `enabled`, nothing is fetched. `onAuthFailure` must be stable.
export function useSessionActivity(
  host: string,
  token: string,
  sessions: SessionRecord[],
  onAuthFailure?: () => void,
  enabled = true,
): SessionActivities {
  const [activities, setActivities] = useState(() => cachedActivities(host));
  const loader = useRef<ActivityLoader<EventsPage> | null>(null);
  const statuses = useRef(new Map<string, SessionRecord["status"]>());

  useEffect(() => {
    if (!host || !token || !enabled) return;
    const created = new ActivityLoader<EventsPage>({
      fetch: (id, signal) =>
        fetchEvents(host, token, id, { messages: ACTIVITY_MESSAGES, signal }).catch((error) => {
          if (isAuthError(error)) onAuthFailure?.();
          throw error;
        }),
      onResult: (id, page, lastEventAt) => {
        const activity = deriveActivity(page.events, page.latest_todo, statuses.current.get(id));
        const held = cache.get(host) ?? new Map<string, CachedActivity>();
        cache.set(host, held.set(id, { lastEventAt, activity }));
        setActivities((current) => new Map(current).set(id, activity));
      },
      // A preview already shown stays; one never loaded falls back.
      onFailure: (id) => setActivities((current) => (current.has(id) ? current : new Map(current).set(id, null))),
    });
    loader.current = created;
    return () => {
      created.dispose();
      loader.current = null;
    };
  }, [host, token, onAuthFailure, enabled]);

  useEffect(() => {
    statuses.current = new Map(sessions.map((session) => [session.id, session.status]));
    const held = cache.get(host);
    for (const session of sessions) {
      if (held?.get(session.id)?.lastEventAt === session.last_event_at) {
        loader.current?.seed(session.id, session.last_event_at);
      }
      loader.current?.request(session.id, session.last_event_at);
    }
  }, [sessions, host, token, enabled]);

  return activities;
}
