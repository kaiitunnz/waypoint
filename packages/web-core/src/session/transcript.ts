import {
  isModelChangeEvent,
  isModelSwitchEvent,
  isPlanEvent,
  isTaskNotificationEvent,
  itemIdForEvent,
  planForEvent,
  readToolName,
} from "../events";
import type { EventRecord } from "../types";
import { stripAnsi } from "./terminalText";

export interface ToolPair {
  call: EventRecord | null;
  result: EventRecord | null;
  itemId: string;
  ts: string;
  sequence: number;
}

export type TranscriptItem =
  | { kind: "single"; event: EventRecord }
  | { kind: "pair"; event: EventRecord; pair: ToolPair }
  | { kind: "tool_run"; items: (Extract<TranscriptItem, { kind: "single" | "pair" }>)[] }
  | { kind: "notification_run"; items: (Extract<TranscriptItem, { kind: "single" | "pair" }>)[] };

export function minRawSequence(events: EventRecord[]): number | null {
  let min: number | null = null;
  for (const event of events) {
    if (min === null || event.sequence < min) {
      min = event.sequence;
    }
  }
  return min;
}

export function foldOlderEvents(
  current: EventRecord[],
  older: EventRecord[],
): EventRecord[] {
  // `older` arrives ascending and sits entirely before `current` in
  // sequence space. We must replicate what the forward merge would have
  // produced if the events had originally arrived in true sequence order
  // — naive text prepending breaks tool_result snapshots (a final
  // non-delta supersedes earlier deltas; mergeEventText handles this,
  // but only when we run it in the right direction). So: build a
  // per-target accumulator by replaying mergeEvents over the older
  // events for that item_id, then merge the accumulator with the
  // matching current entry exactly as if the accumulator had arrived
  // first and the current entry second.
  const seenIds = new Set<number>();
  const seenSequences = new Set<number>();
  for (const event of current) {
    if (typeof event.id === "number") seenIds.add(event.id);
    seenSequences.add(event.sequence);
  }
  const currentItemIndex = new Map<string, number>();
  for (let i = 0; i < current.length; i += 1) {
    const event = current[i];
    if (event.kind !== "agent_output" && event.kind !== "tool_result") continue;
    const itemId = readItemId(event);
    if (!itemId) continue;
    currentItemIndex.set(`${event.kind}:${itemId}`, i);
  }
  // For each target current index, the (single) accumulator EventRecord
  // built up from older events sharing that item_id, in sequence order.
  const accumulators = new Map<number, EventRecord>();
  // Older events that don't fold into anything currently visible.
  // Coalesced amongst themselves via mergeEvents — the forward pass is
  // correct here because they are sequence-ascending.
  let standalone: EventRecord[] = [];
  for (const event of older) {
    if (typeof event.id === "number" && seenIds.has(event.id)) continue;
    if (seenSequences.has(event.sequence)) continue;
    const itemId = readItemId(event);
    if ((event.kind === "agent_output" || event.kind === "tool_result") && itemId) {
      const targetIdx = currentItemIndex.get(`${event.kind}:${itemId}`);
      if (targetIdx !== undefined) {
        const existing = accumulators.get(targetIdx);
        if (existing === undefined) {
          accumulators.set(targetIdx, event);
        } else {
          // Replay mergeEvents on a one-element array to get the same
          // text/metadata combine the forward path would have produced.
          const merged = mergeEvents([existing], event);
          accumulators.set(targetIdx, merged[0]);
        }
        continue;
      }
    }
    standalone = mergeEvents(standalone, event);
  }
  let next = current;
  if (accumulators.size > 0) {
    next = current.map((event, index) => {
      const acc = accumulators.get(index);
      if (acc === undefined) return event;
      // Final fold: accumulator (older) plays the role of "existing",
      // the current entry plays the role of "incoming". This routes
      // the merge through mergeEventText with the same arguments
      // forward processing would have used, so all of its branches
      // (snapshot supersedes deltas, todo_list state-replacement,
      // delta append, newline separator for non-delta concat) keep
      // working on the backward path.
      const merged = mergeEvents([acc], event);
      return merged[0];
    });
  }
  return [...standalone, ...next];
}

export function mergeEvents(current: EventRecord[], incoming: EventRecord): EventRecord[] {
  const dup = current.some((event) => event.id === incoming.id || event.sequence === incoming.sequence);
  if (dup) {
    return current;
  }
  const incomingItemId = readItemId(incoming);
  if ((incoming.kind === "agent_output" || incoming.kind === "tool_result") && incomingItemId) {
    const index = current.findIndex(
      (event) => event.kind === incoming.kind && readItemId(event) === incomingItemId,
    );
    if (index !== -1) {
      const next = current.slice();
      const existing = next[index];
      next[index] = {
        ...existing,
        text: mergeEventText(existing, incoming),
        metadata: { ...existing.metadata, ...incoming.metadata },
        ts: incoming.ts,
        sequence: incoming.sequence,
      };
      return next;
    }
  }
  return [...current, incoming];
}

function readItemId(event: EventRecord): string | null {
  return itemIdForEvent(event);
}

function mergeEventText(existing: EventRecord, incoming: EventRecord): string {
  if (incoming.kind === "agent_output") {
    return `${existing.text}${incoming.text}`;
  }
  if (incoming.kind !== "tool_result") {
    return incoming.text;
  }
  if (isTodoListEvent(existing) || isTodoListEvent(incoming)) {
    return incoming.text || existing.text;
  }
  if (isToolResultDelta(incoming)) {
    return `${existing.text}${incoming.text}`;
  }
  if (isToolResultDelta(existing)) {
    return existing.text || incoming.text;
  }
  if (!existing.text) {
    return incoming.text;
  }
  if (!incoming.text || existing.text === incoming.text) {
    return existing.text;
  }
  const separator = existing.text.endsWith("\n") || incoming.text.startsWith("\n") ? "" : "\n";
  return `${existing.text}${separator}${incoming.text}`;
}

export function collapseSupersededPlanEvents(events: EventRecord[]): EventRecord[] {
  const latestPlanByItemId = new Map<string, EventRecord>();
  for (const event of events) {
    if (!isPlanEvent(event)) {
      continue;
    }
    const itemId = readItemId(event);
    if (itemId) {
      latestPlanByItemId.set(itemId, event);
    }
  }
  if (latestPlanByItemId.size === 0) {
    return events;
  }
  return events.filter((event) => {
    if (!isPlanEvent(event)) {
      return true;
    }
    const itemId = readItemId(event);
    return !itemId || latestPlanByItemId.get(itemId) === event;
  });
}

export function latestActionablePlanEvent(events: EventRecord[]): EventRecord | null {
  const decidedPlanIds = new Set<string>();
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    const decisionPlanId = planDecisionItemId(event);
    if (decisionPlanId) {
      decidedPlanIds.add(decisionPlanId);
      continue;
    }
    const plan = planForEvent(event);
    if (!plan || event.kind === "approval_request") {
      continue;
    }
    return decidedPlanIds.has(plan.id) ? null : event;
  }
  return null;
}

function planDecisionItemId(event: EventRecord): string | null {
  const planItemId = event.metadata.plan_item_id;
  if (typeof planItemId !== "string" || !planItemId) {
    return null;
  }
  const decision = event.metadata.plan_decision;
  return typeof decision === "string" ? planItemId : null;
}

export function buildTranscriptItems(events: EventRecord[]): TranscriptItem[] {
  const result: (Extract<TranscriptItem, { kind: "single" | "pair" }>)[] = [];
  const pairIndex = new Map<string, number>();
  for (const event of events) {
    if (event.kind !== "tool_call" && event.kind !== "tool_result") {
      result.push({ kind: "single", event });
      continue;
    }
    const itemId = readItemId(event);
    if (!itemId) {
      result.push({ kind: "single", event });
      continue;
    }
    const existingIdx = pairIndex.get(itemId);
    if (existingIdx === undefined) {
      const pair: ToolPair = {
        itemId,
        call: event.kind === "tool_call" ? event : null,
        result: event.kind === "tool_result" ? event : null,
        ts: event.ts,
        sequence: event.sequence,
      };
      pairIndex.set(itemId, result.length);
      result.push({ kind: "pair", event, pair });
      continue;
    }
    const item = result[existingIdx];
    if (item.kind !== "pair") {
      continue;
    }
    if (event.kind === "tool_call") {
      item.pair.call = event;
    } else {
      item.pair.result = item.pair.result
        ? mergeToolResultEvent(item.pair.result, event)
        : event;
    }
    item.pair.ts = event.ts;
    item.pair.sequence = Math.max(item.pair.sequence, event.sequence);
  }

  // Classify each item so the grouping loop is easy to reason about.
  // "content"  → user messages, agent text, approvals, ask-questions:
  //              always breaks (and terminates) the current tool run.
  // "tool"     → ordinary tool call/result pairs/singles, including todos:
  //              join the run. The live task list lives in the dock now, so
  //              the transcript todo marker is just a historical tool record
  //              and folds into the run instead of standing alone.
  // "absorbed" → system_note / status_update lifecycle noise: silently join
  //              the active run (rendered as quiet separators when expanded),
  //              or fall through as standalone if no run is active yet.
  function classifyItem(item: Extract<TranscriptItem, { kind: "single" | "pair" }>): "content" | "tool" | "notification" | "absorbed" {
    if (item.kind === "pair") {
      const { call, result } = item.pair;
      const isSpecial = (e: EventRecord | null) =>
        e !== null && isStandaloneToolCard(readToolName(e));
      return isSpecial(call) || isSpecial(result) ? "content" : "tool";
    }
    const { event } = item;
    switch (event.kind) {
      case "user_input":
      case "agent_output":
      case "approval_request":
        return "content";
      case "tool_call":
      case "tool_result":
        return isStandaloneToolCard(readToolName(event)) ? "content" : "tool";
      default:
        if (isModelChangeEvent(event)) {
          // Switch divider stands alone; launch-time mismatch is absorbed.
          return isModelSwitchEvent(event) ? "content" : "absorbed";
        }
        if (isTaskNotificationEvent(event)) {
          // Consecutive task cards batch into their own run, so they neither
          // fold into an adjacent tool run nor stand alone one per card.
          return "notification";
        }
        return isPlanEvent(event) ? "content" : "absorbed";
    }
  }

  const grouped: TranscriptItem[] = [];
  // At most one run is open at a time: a tool item flushes an open notification
  // run and vice versa, so the accumulator carries its own kind.
  let run:
    | {
        kind: "tool" | "notification";
        items: Extract<TranscriptItem, { kind: "single" | "pair" }>[];
      }
    | null = null;
  const flush = () => {
    if (!run) return;
    grouped.push({
      kind: run.kind === "tool" ? "tool_run" : "notification_run",
      items: run.items,
    });
    run = null;
  };

  for (const item of result) {
    const cls = classifyItem(item);
    if (cls === "tool" || cls === "notification") {
      if (run && run.kind !== cls) flush();
      if (!run) run = { kind: cls, items: [] };
      run.items.push(item);
    } else if (cls === "absorbed") {
      // Lifecycle noise folds into whichever run is active, else stands alone.
      if (run) run.items.push(item);
      else grouped.push(item);
    } else {
      flush();
      grouped.push(item);
    }
  }
  flush();

  return grouped;
}

function mergeToolResultEvent(existing: EventRecord, incoming: EventRecord): EventRecord {
  return {
    ...existing,
    text: mergeEventText(existing, incoming),
    metadata: { ...existing.metadata, ...incoming.metadata },
    ts: incoming.ts,
    sequence: Math.max(existing.sequence, incoming.sequence),
  };
}

export function removeConfirmedOptimisticMessages<T extends { text: string }>(
  messages: T[],
  confirmedTexts: string[],
): T[] {
  const confirmedCounts = new Map<string, number>();
  for (const text of confirmedTexts) {
    confirmedCounts.set(text, (confirmedCounts.get(text) ?? 0) + 1);
  }
  if (confirmedCounts.size === 0) {
    return messages;
  }
  const next: T[] = [];
  for (const message of messages) {
    const count = confirmedCounts.get(message.text) ?? 0;
    if (count > 0) {
      confirmedCounts.set(message.text, count - 1);
      continue;
    }
    next.push(message);
  }
  return next;
}

function isToolResultDelta(event: EventRecord): boolean {
  if (event.kind !== "tool_result") {
    return false;
  }
  const method = event.metadata?.method;
  return method === "item/commandExecution/outputDelta" || method === "item/fileChange/outputDelta";
}

export function isTodoListEvent(event: EventRecord): boolean {
  return event.metadata?.item_type === "todo_list" || readToolName(event) === "TodoWrite";
}

// Tool calls that render as their own card instead of folding into a collapsed
// tool run.
function isStandaloneToolCard(toolName: string | null): boolean {
  return toolName === "AskUserQuestion" || toolName === "SendUserFile";
}

export function sanitizeEvent(event: EventRecord): EventRecord {
  return {
    ...event,
    text: stripAnsi(event.text),
  };
}
