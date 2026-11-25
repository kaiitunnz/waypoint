import type { EventRecord, EventsPage, PendingQuestionsSnapshot, SessionRecord } from "../types";
import { mergePendingQuestionSnapshot } from "./askQuestion";
import type { ConnectionState } from "./connection";
import { foldOlderEvents, mergeEvents, minRawSequence, sanitizeEvent } from "./transcript";

export interface StreamState {
  session: SessionRecord | null;
  // Coalesced: streamed deltas of one item are one event.
  events: EventRecord[];
  hasOlder: boolean;
  // The lowest raw sequence loaded, where the next older page starts.
  oldestSequence: number | null;
  // Whether the tail page has loaded; the session record alone can arrive first, over the socket.
  loaded: boolean;
  pendingQuestions: PendingQuestionsSnapshot | null;
  connection: ConnectionState;
  error: string | null;
  // The HTTP status behind `error`, when it came from one.
  errorStatus: number | null;
  // How many errors have been reported, so a repeated message can be told from a standing one.
  errorCount: number;
}

export const INITIAL_STREAM_STATE: StreamState = {
  session: null,
  events: [],
  hasOlder: false,
  oldestSequence: null,
  loaded: false,
  pendingQuestions: null,
  connection: "connecting",
  error: null,
  errorStatus: null,
  errorCount: 0,
};

export type StreamAction =
  | { type: "reset" }
  | { type: "loaded"; session: SessionRecord; page: EventsPage }
  // A live batch, already sanitized.
  | { type: "events"; events: EventRecord[] }
  | { type: "older"; page: EventsPage }
  | { type: "session"; session: SessionRecord }
  // `replace` for a socket's first frame, which restarts the server's revision counter.
  | { type: "pendingQuestions"; snapshot: PendingQuestionsSnapshot; replace: boolean }
  | { type: "connection"; connection: ConnectionState }
  | { type: "error"; message: string | null; status?: number };

// The highest event id in `events` or `from`, whichever is greater: where a reconnect resumes.
export function maxEventId(events: readonly EventRecord[], from: number | null = null): number | null {
  return events.reduce<number | null>(
    (highest, event) => (event.id === undefined || (highest !== null && highest >= event.id) ? highest : event.id),
    from,
  );
}

// The events a client has not applied yet, given the highest event id it has: an
// event carries its id, and a coalesced item keeps its first, so only ids tell
// a repeated delta from a new one.
export function takeNewer(events: readonly EventRecord[], latestId: number | null): EventRecord[] {
  return latestId === null ? [...events] : events.filter((event) => event.id === undefined || event.id > latestId);
}

function coalesce(events: EventRecord[], into: EventRecord[] = []): EventRecord[] {
  return events.reduce<EventRecord[]>((acc, event) => mergeEvents(acc, event), into);
}

export function streamReducer(state: StreamState, action: StreamAction): StreamState {
  switch (action.type) {
    case "reset":
      return INITIAL_STREAM_STATE;
    case "loaded": {
      const sanitized = action.page.events.map(sanitizeEvent);
      return {
        ...state,
        // A frame the socket delivered first is newer than this snapshot.
        session: state.session ?? action.session,
        events: coalesce(sanitized),
        hasOlder: action.page.has_more,
        oldestSequence: minRawSequence(sanitized),
        loaded: true,
        pendingQuestions: action.page.pending_questions
          ? mergePendingQuestionSnapshot(state.pendingQuestions, action.page.pending_questions, false)
          : state.pendingQuestions,
        error: null,
        errorStatus: null,
      };
    }
    case "events":
      return { ...state, events: coalesce(action.events, state.events) };
    case "older": {
      if (action.page.events.length === 0) return { ...state, hasOlder: false };
      const sanitized = action.page.events.map(sanitizeEvent);
      const incomingMin = minRawSequence(sanitized);
      return {
        ...state,
        events: foldOlderEvents(state.events, sanitized),
        hasOlder: action.page.has_more,
        oldestSequence:
          incomingMin === null
            ? state.oldestSequence
            : state.oldestSequence === null
              ? incomingMin
              : Math.min(state.oldestSequence, incomingMin),
      };
    }
    case "session":
      return { ...state, session: action.session };
    case "pendingQuestions":
      return {
        ...state,
        pendingQuestions: mergePendingQuestionSnapshot(state.pendingQuestions, action.snapshot, action.replace),
      };
    case "connection":
      return { ...state, connection: action.connection };
    case "error":
      return {
        ...state,
        error: action.message,
        errorStatus: action.message === null ? null : (action.status ?? null),
        errorCount: action.message === null ? state.errorCount : state.errorCount + 1,
      };
  }
}
