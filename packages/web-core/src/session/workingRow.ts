import { itemIdForEvent, readToolName } from "../events";
import type { EventRecord } from "../types";
import { isBlankAgentMessage } from "./cardText";
import { toolBadgeFor } from "./toolPresentation";
import { pendingCallOf } from "./toolRun";
import { isStandaloneToolCard, isToolResultDelta, minRawSequence } from "./transcript";
import { lastRunIndex, runCallsOf, type TranscriptTurn } from "./turns";

// Turn boundaries come from the session status each event was recorded with; an
// event without one marks neither.
const ACTIVE = new Set(["starting", "running", "waiting_input"]);
const ENDED = new Set(["idle", "interrupted", "error", "exited"]);
const CUT_SHORT = new Set(["interrupted", "error", "exited"]);
// Structured Claude Code records a compaction as idle while its turn goes on.
const COMPACTION_METHODS = new Set(["system.status", "system.compact_boundary"]);

const statusOf = (event: EventRecord): string => {
  const status = event.metadata?.status;
  return typeof status === "string" ? status : "";
};

function endsTurn(event: EventRecord): boolean {
  if (event.kind === "user_input") return false;
  // A question left open ends its turn as `waiting_input`.
  return event.metadata?.awaiting_answer === true || ENDED.has(statusOf(event));
}

// A message starts a turn whatever status it was recorded with.
const isActivity = (event: EventRecord): boolean =>
  !endsTurn(event) && (event.kind === "user_input" || ACTIVE.has(statusOf(event)));

// The index of the event that ended the turn before the current one, or -1.
function lastTurnEnd(events: readonly EventRecord[]): number {
  let next: EventRecord | null = null;
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (isActivity(event)) {
      next = event;
      continue;
    }
    if (!endsTurn(event)) continue;
    const compaction = COMPACTION_METHODS.has(String(event.metadata?.method));
    if (!compaction || next?.kind === "user_input") return index;
  }
  return -1;
}

// A note about the session, which can be recorded as running between turns.
const isNote = (event: EventRecord): boolean => event.kind === "system_note" || event.kind === "status_update";

function currentTurn(events: readonly EventRecord[]): { events: readonly EventRecord[]; bounded: boolean } {
  const end = lastTurnEnd(events);
  const since = events.slice(end + 1);
  // A turn cut short can still deliver events afterwards; the next message starts anew.
  const cutShort = end >= 0 && CUT_SHORT.has(statusOf(events[end]));
  const message = cutShort ? since.findIndex((event) => event.kind === "user_input") : -1;
  return { events: message > 0 ? since.slice(message) : since, bounded: end >= 0 };
}

export interface TurnStart {
  // Epoch ms, or null when the events hold nothing of the current turn.
  startedAt: number | null;
  // The end of the turn before is among the events, so older ones cannot move the start.
  bounded: boolean;
}

// When the transcript's latest turn started: its first activity, leaving out notes
// that run up to its first message.
export function turnStart(events: readonly EventRecord[]): TurnStart {
  const turn = currentTurn(events);
  const activity = turn.events.filter(isActivity);
  const message = activity.findIndex((event) => event.kind === "user_input");
  const first = message > 0 && activity.slice(0, message).every(isNote) ? activity[message] : activity[0];
  const startedAt = first ? Date.parse(first.ts) : Number.NaN;
  return { startedAt: Number.isFinite(startedAt) ? startedAt : null, bounded: turn.bounded };
}

export type FetchOlderEvents = (beforeSequence: number) => Promise<{ events: EventRecord[]; has_more: boolean }>;

// Enough pages for all but the longest turns.
const MAX_PAGES = 25;

// Pages back to the start of a turn that began before the loaded events; null when
// it is out of reach.
export async function findTurnStart(
  events: readonly EventRecord[],
  oldestSequence: number,
  fetchOlder: FetchOlderEvents,
): Promise<number | null> {
  let history = [...events];
  let before = oldestSequence;
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const older = await fetchOlder(before);
    history = [...older.events, ...history];
    const found = turnStart(history);
    if (found.bounded || !older.has_more) return found.startedAt;
    const next = minRawSequence(older.events);
    if (next === null) return null;
    before = next;
  }
  return null;
}

// "42s", "1m 12s", "1h 5m".
export function formatElapsed(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function hasLiveRun(turns: readonly TranscriptTurn[]): boolean {
  const turn = turns[turns.length - 1];
  if (!turn || turn.kind === "rule") return false;
  const item = turn.items[lastRunIndex(turn)];
  return item !== undefined && pendingCallOf(runCallsOf(item)) !== undefined;
}

// A call still running: no result, or only streamed output. Calls drawn as their
// own card never record a result, so they are skipped.
function runningCall(events: readonly EventRecord[]): EventRecord | undefined {
  const finished = new Set(
    events.filter((event) => event.kind === "tool_result" && !isToolResultDelta(event)).map(itemIdForEvent),
  );
  return events.findLast((event) => {
    if (event.kind !== "tool_call" || isStandaloneToolCard(readToolName(event))) return false;
    const id = itemIdForEvent(event);
    return id !== null && !finished.has(id);
  });
}

const isTurnContent = (event: EventRecord): boolean =>
  event.kind === "user_input" ||
  event.kind === "tool_call" ||
  event.kind === "tool_result" ||
  (event.kind === "agent_output" && !isBlankAgentMessage(event));

// What the working row names after "Working", or null where the transcript says it
// already: a live run line names its call, and a reply being written shows itself.
export function workingStep(turns: readonly TranscriptTurn[], events: readonly EventRecord[]): string | null {
  if (hasLiveRun(turns)) return null;
  const turn = currentTurn(events).events;
  const call = runningCall(turn);
  if (call) {
    const tool = readToolName(call);
    return tool ? `Running ${toolBadgeFor(tool).label.toLowerCase()}` : null;
  }
  const last = turn.findLast(isTurnContent);
  if (last?.kind === "agent_output" && last.metadata?.item_kind !== "reasoning") return null;
  return "Thinking";
}
