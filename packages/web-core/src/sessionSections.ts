import { matchesQuery, parseQuery } from "./search";
import { byRecency, SESSION_SEARCH_FIELDS } from "./sessionHistory";
import type { SessionRecord } from "./types";

// Sources the home never lists (each has its own surface or is ephemeral).
export const HOME_HIDDEN_SOURCES = ["assistant", "telemetry"] as const;

export function visibleSessions(sessions: SessionRecord[]): SessionRecord[] {
  return sessions.filter(
    (session) => !(HOME_HIDDEN_SOURCES as readonly string[]).includes(session.source),
  );
}

export interface SessionSections {
  // Pinned and not exited; newest pin first.
  pinned: SessionRecord[];
  // Running or idle and not pinned. A session with a pending ask is shown by its
  // Needs-you card instead.
  live: SessionRecord[];
  // Exited and pinned; newest pin first. They head the Exited group.
  pinnedExited: SessionRecord[];
  // Exited and not pinned, among the sessions the live list still carries.
  exited: SessionRecord[];
}

export const byPin = (a: SessionRecord, b: SessionRecord): number =>
  (b.pinned_at ?? "").localeCompare(a.pinned_at ?? "");

export function buildSections(
  sessions: SessionRecord[],
  askSessionIds: ReadonlySet<string>,
): SessionSections {
  const pinned: SessionRecord[] = [];
  const live: SessionRecord[] = [];
  const pinnedExited: SessionRecord[] = [];
  const exited: SessionRecord[] = [];
  for (const session of sessions) {
    if (session.status === "exited") (session.pinned_at ? pinnedExited : exited).push(session);
    else if (session.pinned_at) pinned.push(session);
    else if (!askSessionIds.has(session.id)) live.push(session);
  }
  pinned.sort(byPin);
  live.sort(byRecency);
  pinnedExited.sort(byPin);
  exited.sort(byRecency);
  return { pinned, live, pinnedExited, exited };
}

export interface SessionSearch {
  lead: SessionRecord[];
  rest: SessionRecord[];
}

// The session query syntax (fields, quotes, regex, OR) over the sessions in hand.
export function searchSessions(sessions: SessionRecord[], query: string): SessionSearch {
  const terms = parseQuery(query);
  const matches = sessions.filter((session) => matchesQuery(session, terms, SESSION_SEARCH_FIELDS));
  return {
    lead: matches.filter((session) => session.pinned_at).sort(byRecency),
    rest: matches.filter((session) => !session.pinned_at).sort(byRecency),
  };
}
