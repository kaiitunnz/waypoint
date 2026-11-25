import type { EventRecord, EventsPage, PendingQuestionsSnapshot, SessionRecord } from "../types";
import { mergePendingQuestionSnapshot } from "./askQuestion";
import type { ConnectionState } from "./connection";
import { foldOlderEvents, mergeEvents, minRawSequence, sanitizeEvent } from "./transcript";

export const RECONNECT_BASE_MS = 1000;
export const RECONNECT_MAX_MS = 15000;

// The wait before reconnect attempt `attempt` (0-based): 1s doubling to 15s.
export function reconnectDelay(attempt: number): number {
  return Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** attempt);
}

export interface StreamState {
  session: SessionRecord | null;
  // Coalesced: streamed deltas of one item are one event.
  events: EventRecord[];
  hasOlder: boolean;
  // The lowest raw sequence loaded, where the next older page starts.
  oldestSequence: number | null;
  // The tail page's latest todo snapshot, for when a todo predates the loaded window.
  loadedTodo: EventRecord | null;
  pendingQuestions: PendingQuestionsSnapshot | null;
  connection: ConnectionState;
  error: string | null;
}

export const INITIAL_STREAM_STATE: StreamState = {
  session: null,
  events: [],
  hasOlder: false,
  oldestSequence: null,
  loadedTodo: null,
  pendingQuestions: null,
  connection: "connecting",
  error: null,
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
  | { type: "error"; message: string | null };

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
        session: action.session,
        // Live events that beat the page here keep their place.
        events: coalesce(state.events, coalesce(sanitized)),
        hasOlder: action.page.has_more,
        oldestSequence: minRawSequence(sanitized),
        loadedTodo: action.page.latest_todo ? sanitizeEvent(action.page.latest_todo) : null,
        pendingQuestions: action.page.pending_questions
          ? mergePendingQuestionSnapshot(state.pendingQuestions, action.page.pending_questions, false)
          : state.pendingQuestions,
        error: null,
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
      return { ...state, error: action.message };
  }
}
