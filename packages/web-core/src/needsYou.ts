import { gateHeadline, gateKey, gateTime, isGateAnswered, type BoardGate } from "./board";
import type { InboxStatusFilter } from "./inbox";
import {
  askBodyLine,
  askPreview,
  fitsListing,
  inlineCommand,
  isRecommendedLabel,
  planPreview,
  stripRecommendedSuffix,
} from "./pendingAskActions";
import type { AskUserQuestion } from "./session/askQuestion";
import { plainSummary, type ShownTitle } from "./session/summary";
import type { AskSession } from "./session/wholeRequest";
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
function gateItemIds(gates: readonly BoardGate[]): Set<string> {
  return new Set(gates.flatMap((gate) => (gate.ticket.inbox_item_id ? [gate.ticket.inbox_item_id] : [])));
}

// ─── Search ───

function matches(haystack: (string | null | undefined)[], query: string): boolean {
  return haystack.join(" ").toLowerCase().includes(query);
}

function askMatches(ask: PendingAsk, query: string): boolean {
  // Searched as shown, without the marks around its machine strings.
  const described = ask.kind === "approval" && ask.approval.summary ? plainSummary(ask.approval.summary) : null;
  return matches([ask.session_title, ask.title, ask.body, described], query);
}

// A gate also answers to the search its hidden inbox item would.
function gateMatches(gate: BoardGate, query: string): boolean {
  return matches(
    [gate.ticket.title, gateHeadline(gate.ticket), gate.project, gate.item?.subject, gate.item?.from_label],
    query,
  );
}

// ─── Queue ───

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

// A question a card cannot take the answer to.
export function askNeedsDetail(ask: PendingQuestionAsk): boolean {
  const [prompt, ...rest] = ask.question.questions;
  return rest.length > 0 || !prompt || prompt.multi_select || prompt.options.length === 0;
}

export function questionLine(ask: PendingQuestionAsk): string {
  const prompts = ask.question.questions;
  if (prompts.length !== 1) return `${prompts.length} questions`;
  const { options } = prompts[0];
  if (options.length === 0) return "Free-form answer";
  const count = `${options.length} option${options.length === 1 ? "" : "s"}`;
  const recommended = options.find((option) => option.recommended || isRecommendedLabel(option.label));
  return recommended ? `${count}, ${stripRecommendedSuffix(recommended.label)} recommended` : count;
}

export interface AskLine {
  text: string;
  // A command or path, set in mono.
  mono: boolean;
}

export interface AskRow extends AskLine {
  // Shown in place of `text`: a path keeps its file name on one line.
  path: string | null;
  // The row shows the whole request and takes the reply.
  answers: boolean;
}

// A row takes a reply only where it shows everything asked.
export function askRow(ask: PendingAsk, { cwd, fromScreen }: AskSession): AskRow {
  const line = askLine(ask, cwd);
  if (ask.kind === "question") {
    const answers = !askNeedsDetail(ask) && fitsListing(ask.question.questions[0].question);
    return { ...line, path: null, answers };
  }
  const preview = askPreview(ask, cwd);
  if (preview?.kind === "path") return { ...line, path: preview.path, answers: false };
  const command = inlineCommand(ask, fromScreen);
  if (command !== null) return { ...line, text: command, path: null, answers: true };
  return { ...line, path: null, answers: false };
}

// The line a row shows under its headline, dropped where it repeats the ask's own
// title. It stays under a summary, which can read like a command it is not.
export const rowLine = (row: AskRow, headline: ShownTitle): string | null =>
  row.text && (headline.marked || row.text !== headline.text) ? row.text : null;

export function askLine(ask: PendingAsk, cwd: string): AskLine {
  if (ask.kind === "question") return { text: questionLine(ask), mono: false };
  if (ask.kind === "plan_approval") return { text: planPreview(ask.plan.text, 1), mono: false };
  const preview = askPreview(ask, cwd);
  if (preview?.kind === "command") {
    const [first, ...rest] = preview.text.split("\n");
    return { text: rest.length > 0 ? `${first} …` : first, mono: true };
  }
  if (preview?.kind === "path") return { text: preview.path, mono: true };
  return { text: askBodyLine(ask, cwd) ?? ask.approval.tool_name ?? ask.title, mono: false };
}

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
