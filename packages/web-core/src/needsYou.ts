// The needs-you queue: pending asks, inbox items and board gates as one list,
// with its counts, search and routes.

import { gateHeadline, gateKey, isGateAnswered, type BoardGate } from "./board";
import type { InboxStatusFilter } from "./inbox";
import { decidesInline, decisionView, isRecommendedLabel, planPreview, stripRecommendedSuffix } from "./pendingAskActions";
import type { AskUserQuestion } from "./session/askQuestion";
import type { InboxItem, PendingAsk, PendingQuestionAsk } from "./types";

export type NeedsSource = "sessions" | "inbox" | "board";
export type SourceFilter = NeedsSource | "all";

export type QueueEntry =
  | { source: "sessions"; key: string; routeId: string; time: string; ask: PendingAsk }
  | { source: "inbox"; key: string; routeId: string; time: string; item: InboxItem }
  | { source: "board"; key: string; routeId: string; time: string; gate: BoardGate; answered: boolean };

// A gate with an item opens it; without one it has a detail of its own.
export function gateRouteId(gate: BoardGate): string {
  return gate.item?.id ?? gateKey(gate);
}

// The items a listed gate stands for, which get no row of their own.
export function gateItemIds(gates: readonly BoardGate[]): Set<string> {
  return new Set(gates.flatMap((gate) => (gate.ticket.inbox_item_id ? [gate.ticket.inbox_item_id] : [])));
}

// ─── Search ───

function matches(haystack: (string | null | undefined)[], query: string): boolean {
  return haystack.join(" ").toLowerCase().includes(query);
}

function askMatches(ask: PendingAsk, query: string): boolean {
  return matches([ask.session_title, ask.title, ask.body], query);
}

// A gate also answers to the search its hidden inbox item would.
function gateMatches(gate: BoardGate, query: string): boolean {
  return matches(
    [gate.ticket.title, gateHeadline(gate.ticket), gate.project, gate.item?.subject, gate.item?.from_label],
    query,
  );
}

// ─── Queue ───

const gateTime = (gate: BoardGate) => gate.ticket.awaiting_since ?? gate.ticket.updated_at;

export interface QueueInput {
  asks: readonly PendingAsk[];
  // The inbox list for `status`, already filtered and searched by the server.
  items: readonly InboxItem[];
  gates: readonly BoardGate[];
  status: InboxStatusFilter;
  source: SourceFilter;
  q: string;
}

// Newest first, with what the human has already answered after the rest. Asks and
// gates exist only while open, so a resolved view lists inbox items alone.
export function buildQueue({ asks, items, gates, status, source, q }: QueueInput): QueueEntry[] {
  const open = status !== "resolved";
  const query = q.trim().toLowerCase();
  const listedGates = open ? gates : [];
  const hidden = gateItemIds(listedGates);
  const entries: QueueEntry[] = [];
  if (open) {
    for (const ask of asks) {
      if (query && !askMatches(ask, query)) continue;
      entries.push({ source: "sessions", key: ask.id, routeId: ask.id, time: ask.created_at, ask });
    }
  }
  for (const item of items) {
    if (hidden.has(item.id)) continue;
    entries.push({ source: "inbox", key: item.id, routeId: item.id, time: item.updated_at, item });
  }
  for (const gate of listedGates) {
    if (query && !gateMatches(gate, query)) continue;
    const key = gateKey(gate);
    entries.push({
      source: "board",
      key,
      routeId: gateRouteId(gate),
      time: gateTime(gate),
      gate,
      answered: isGateAnswered(gate),
    });
  }
  const waiting = (entry: QueueEntry) => (entry.source === "board" && entry.answered ? 1 : 0);
  return entries
    .filter((entry) => source === "all" || entry.source === source)
    .sort((a, b) => waiting(a) - waiting(b) || Date.parse(b.time) - Date.parse(a.time) || a.key.localeCompare(b.key));
}

// ─── Counts ───

export interface NeedsCounts {
  all: number;
  sessions: number;
  inbox: number;
  board: number;
}

// What is waiting on the human, by source. A gate with an open item counts once,
// as the gate; an answered gate does not count.
export function needsCounts(
  asks: number,
  unresolvedInbox: number,
  gates: readonly BoardGate[],
): NeedsCounts {
  const openItems = new Set(gates.flatMap((gate) => (gate.item?.status === "open" ? [gate.item.id] : [])));
  const board = gates.filter((gate) => !isGateAnswered(gate)).length;
  const inbox = Math.max(0, unresolvedInbox - openItems.size);
  return { all: asks + inbox + board, sessions: asks, inbox, board };
}

// ─── Routes ───

export type NeedsTarget =
  | { kind: "ask"; id: string; sessionId: string }
  | { kind: "gate"; id: string }
  | { kind: "inbox"; id: string };

const ASK_ID = /^(?:question|approval|plan_approval):([^:]+):/;

export function parseNeedsId(id: string): NeedsTarget {
  const ask = ASK_ID.exec(id);
  if (ask) return { kind: "ask", id, sessionId: ask[1] };
  return id.startsWith("gate:") ? { kind: "gate", id } : { kind: "inbox", id };
}

export const needsHref = (routeId: string) => `/inbox/${encodeURIComponent(routeId)}`;

// A route segment as the browser has it; ids hold `:`, which arrives encoded.
export function decodeNeedsId(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

// ─── Asks ───

// A row or card can answer these itself; the others need the detail.
export function askNeedsDetail(ask: PendingAsk): boolean {
  if (ask.kind !== "question") return !decidesInline(ask);
  const [prompt, ...rest] = ask.question.questions;
  return rest.length > 0 || !prompt || prompt.multi_select || prompt.options.length === 0;
}

function questionSummary(ask: PendingQuestionAsk): string {
  const prompts = ask.question.questions;
  if (prompts.length !== 1) return `${prompts.length} questions`;
  const { options } = prompts[0];
  if (options.length === 0) return "Free-form answer";
  const count = `${options.length} option${options.length === 1 ? "" : "s"}`;
  const recommended = options.find((option) => option.recommended || isRecommendedLabel(option.label));
  return recommended ? `${count}, ${stripRecommendedSuffix(recommended.label)} recommended` : count;
}

export interface AskSummary {
  text: string;
  // A command or path, set in mono.
  mono: boolean;
}

// A row of the split view answers an ask only where it shows everything asked;
// a longer request is decided in the detail.
const ROW_MAX_LENGTH = 160;
const ROW_MAX_LINES = 3;

const fitsRow = (text: string) => text.length <= ROW_MAX_LENGTH && text.split("\n").length <= ROW_MAX_LINES;

export interface AskRow extends AskSummary {
  // Set instead of `text` being shown: a path keeps its file name on one line.
  path: string | null;
  // The row shows the whole request and takes the reply.
  answers: boolean;
}

export function askRow(ask: PendingAsk): AskRow {
  const summary = askSummary(ask);
  const inline = !askNeedsDetail(ask);
  if (ask.kind === "question") {
    return { ...summary, path: null, answers: inline && fitsRow(ask.question.questions[0].question) };
  }
  const preview = ask.kind === "approval" ? decisionView(ask).preview : null;
  if (preview?.kind === "path") return { ...summary, path: preview.path, answers: inline };
  if (inline && preview?.kind === "command" && fitsRow(preview.text)) {
    return { ...summary, text: preview.text, path: null, answers: true };
  }
  return { ...summary, path: null, answers: false };
}

// The line under a row's headline.
export function askSummary(ask: PendingAsk): AskSummary {
  if (ask.kind === "question") return { text: questionSummary(ask), mono: false };
  if (ask.kind === "plan_approval") return { text: planPreview(ask.plan.text, 1), mono: false };
  const { preview } = decisionView(ask);
  if (preview?.kind === "command") {
    const [first, ...rest] = preview.text.split("\n");
    return { text: rest.length > 0 ? `${first} …` : first, mono: true };
  }
  if (preview?.kind === "path") return { text: preview.path, mono: true };
  return { text: ask.body ?? ask.approval.tool_name ?? ask.title, mono: false };
}

// A pending question in the shape the question editor takes.
export function askUserQuestions(ask: PendingQuestionAsk): AskUserQuestion[] {
  return ask.question.questions.map((prompt) => ({
    question: prompt.question,
    header: prompt.header ?? undefined,
    options: prompt.options.map((option) => ({
      label: option.label,
      description: option.description ?? undefined,
    })),
    multiSelect: prompt.multi_select,
  }));
}
