// Board-workspace taxonomy: channel grouping, ticket-state to lane/lamp
// mapping, and kind/priority tones.

import {
  BoardChannel,
  InboxItem,
  ManagerStateResponse,
  ManagerSummary,
  ManagerTicket,
  ManagerTicketListQuery,
  ManagerTicketScale,
  ManagerTicketSort,
  ManagerTicketState,
} from "./types";

// ─── Channel grouping (navigator) ───

export type ChannelGroupKey = "manager" | "ticket" | "job" | "other";

export const CHANNEL_GROUP_ORDER: ChannelGroupKey[] = [
  "manager",
  "ticket",
  "job",
  "other",
];

export const CHANNEL_GROUP_LABELS: Record<ChannelGroupKey, string> = {
  manager: "Manager",
  ticket: "Tickets",
  job: "Jobs",
  other: "Other",
};

// `job:*` is history and starts collapsed; the manager's own channels stay open.
export const CHANNEL_GROUP_COLLAPSED_DEFAULT: Record<ChannelGroupKey, boolean> = {
  manager: false,
  ticket: false,
  job: true,
  other: false,
};

// Bucket channels by group, optionally keeping only those matching a filter.
export function groupChannels(
  channels: BoardChannel[],
  filter?: string,
): Record<ChannelGroupKey, BoardChannel[]> {
  const needle = filter?.trim().toLowerCase() ?? "";
  const groups: Record<ChannelGroupKey, BoardChannel[]> = {
    manager: [],
    ticket: [],
    job: [],
    other: [],
  };
  for (const channel of channels) {
    if (needle && !channel.channel.toLowerCase().includes(needle)) continue;
    groups[classifyChannel(channel.channel)].push(channel);
  }
  return groups;
}

// The trailing dash in `-ticket-` keeps `<x>-tickets` out of the ticket group.
export function classifyChannel(channel: string): ChannelGroupKey {
  if (channel.startsWith("job:")) return "job";
  const name = channel.toLowerCase();
  if (
    name === "tickets" ||
    name === "org" ||
    name.endsWith("-tickets") ||
    name.endsWith("-org")
  ) {
    return "manager";
  }
  if (name.startsWith("ticket-") || name.includes("-ticket-")) return "ticket";
  return "other";
}

export function ticketIdFromChannel(
  channel: string,
  prefix: string | null | undefined,
): string | null {
  if (prefix && channel.startsWith(prefix)) {
    return channel.slice(prefix.length) || null;
  }
  return null;
}

// ─── Ticket state → lifecycle lane ───

export interface Lane {
  key: string;
  label: string;
  states: ManagerTicketState[];
}

export const LANES: Lane[] = [
  { key: "intake", label: "Intake · Triage", states: ["intake", "triaged"] },
  { key: "spec", label: "Spec", states: ["spec_pending", "spec_review"] },
  { key: "ready", label: "Ready", states: ["ready"] },
  {
    key: "build",
    label: "Building · Revising",
    states: ["delegated", "building", "revising"],
  },
  { key: "blocked", label: "Blocked", states: ["blocked"] },
  { key: "review", label: "Review", states: ["review_requested"] },
  { key: "done", label: "Done", states: ["merged", "deferred", "abandoned"] },
];

const STATE_TO_LANE: Record<ManagerTicketState, string> = Object.fromEntries(
  LANES.flatMap((lane) => lane.states.map((state) => [state, lane.key])),
) as Record<ManagerTicketState, string>;

export function laneForState(state: ManagerTicketState): string {
  return STATE_TO_LANE[state] ?? "other";
}

// The three approval gates — tickets awaiting a human decision.
export const AWAITING_STATES: ReadonlySet<ManagerTicketState> = new Set([
  "spec_review",
  "blocked",
  "review_requested",
]);

export function isAwaiting(state: ManagerTicketState): boolean {
  return AWAITING_STATES.has(state);
}

// ─── Gates: tickets waiting on a human decision ───

// Highest priority first.
export function awaitingTickets(tickets: ManagerTicket[]): ManagerTicket[] {
  return tickets
    .filter((ticket) => isAwaiting(ticket.state))
    .sort((a, b) => a.priority.localeCompare(b.priority));
}

// The number of a pull request URL or `#123` reference.
export function prNumber(pr: string): string | null {
  const match = pr.match(/\/pull\/(\d+)|#(\d+)|\/(\d+)(?:$|[/?#])/);
  return match ? match[1] ?? match[2] ?? match[3] ?? null : null;
}

export interface GateView {
  gate: "PR review" | "Spec review" | "Blocked";
  // What the human is asked to do.
  summary: string;
}

export function gateView(ticket: ManagerTicket): GateView {
  const gate =
    ticket.state === "review_requested"
      ? "PR review"
      : ticket.state === "spec_review"
        ? "Spec review"
        : "Blocked";
  const num = ticket.pr_url ? prNumber(ticket.pr_url) : null;
  const summary =
    ticket.state === "review_requested" && ticket.pr_url
      ? `Review PR${num ? ` #${num}` : ""} and merge, request changes, or abort.`
      : ticket.state === "spec_review"
        ? "Approve the spec, request changes, or reject."
        : "The tech-lead is blocked and needs your decision.";
  return { gate, summary };
}

// The ask itself, for a queue row.
export function gateHeadline(ticket: ManagerTicket): string {
  if (ticket.state === "spec_review") return "Spec ready for review";
  if (ticket.state !== "review_requested") return "Blocked, needs your decision";
  const num = ticket.pr_url ? prNumber(ticket.pr_url) : null;
  return num ? `PR #${num} ready to merge` : "Ready for review";
}

// An awaiting ticket of one manager, with the inbox item that asks about it.
export interface BoardGate {
  managerId: string;
  project: string;
  channelPrefix: string | null;
  ticket: ManagerTicket;
  // Null when the ticket names no item or the item is gone.
  item: InboxItem | null;
}

// A manager's gates, before their items are read.
export function managerGates(manager: ManagerSummary, state: ManagerStateResponse): BoardGate[] {
  return awaitingTickets(state.tickets).map((ticket) => ({
    managerId: manager.id,
    project: manager.project,
    channelPrefix: state.config?.render_context?.ticket_channel_prefix ?? null,
    ticket,
    item: null,
  }));
}

export function gateKey(gate: BoardGate): string {
  return `gate:${gate.managerId}:${gate.ticket.id}`;
}

// Where the ticket's discussion lives; the board itself when it has no channel.
export function gateBoardHref(gate: BoardGate): string {
  return gate.channelPrefix
    ? `/board?channel=${encodeURIComponent(`${gate.channelPrefix}${gate.ticket.id}`)}`
    : "/board";
}

// The human has replied, and the ticket has not moved on yet.
export function isGateAnswered(gate: BoardGate): boolean {
  return gate.item?.status === "resolved";
}

// ─── Ticket state → lamp tone + label ───

export type StateTone =
  | "idle"
  | "spec"
  | "awaiting"
  | "active"
  | "revising"
  | "blocked"
  | "done";

const STATE_TONE: Record<ManagerTicketState, StateTone> = {
  intake: "idle",
  triaged: "idle",
  spec_pending: "spec",
  spec_review: "awaiting",
  ready: "spec",
  delegated: "active",
  building: "active",
  revising: "revising",
  blocked: "blocked",
  review_requested: "awaiting",
  merged: "done",
  deferred: "idle",
  abandoned: "idle",
};

const STATE_LABEL: Record<ManagerTicketState, string> = {
  intake: "Intake",
  triaged: "Triaged",
  spec_pending: "Spec pending",
  spec_review: "Spec review",
  ready: "Ready",
  delegated: "Delegated",
  building: "Building",
  revising: "Revising",
  blocked: "Blocked",
  review_requested: "Review requested",
  merged: "Merged",
  deferred: "Deferred",
  abandoned: "Abandoned",
};

export function stateTone(state: ManagerTicketState): StateTone {
  return STATE_TONE[state] ?? "idle";
}

export function stateLabel(state: ManagerTicketState): string {
  return STATE_LABEL[state] ?? state;
}

// ─── Post kind → tone ───

export type KindTone =
  | "muted"
  | "strategy"
  | "success"
  | "danger"
  | "warn"
  | "relay"
  | "writer";

const KIND_TONE: Record<string, KindTone> = {
  progress: "muted",
  intake_open: "muted",
  strategy: "strategy",
  done: "success",
  partial: "success",
  decision: "danger",
  error: "danger",
  attention: "warn",
  respec: "warn",
  relay: "relay",
  spec_ready: "writer",
  infeasible: "writer",
  recommendation: "writer",
};

export function kindTone(kind: string | null | undefined): KindTone {
  if (!kind) return "muted";
  return KIND_TONE[kind] ?? "muted";
}

// ─── Priority tone ───

export type PriorityTone = "p0" | "p1" | "p2" | "p3";

export function priorityTone(priority: string | null | undefined): PriorityTone {
  switch (priority) {
    case "p0":
      return "p0";
    case "p1":
      return "p1";
    case "p3":
      return "p3";
    default:
      return "p2";
  }
}

// ─── Board query controls ───

export const TICKET_PAGE_LIMIT = 50;

export const DEFAULT_TICKET_QUERY: ManagerTicketListQuery = {
  q: "",
  state: [],
  priority: [],
  scale: [],
  sort: "updated_at",
  direction: "desc",
};

export const SORT_OPTIONS: { key: ManagerTicketSort; label: string }[] = [
  { key: "updated_at", label: "Last updated" },
  { key: "created_at", label: "Created" },
  { key: "priority", label: "Priority" },
  { key: "id", label: "ID" },
];

export const SCALE_OPTIONS: { value: ManagerTicketScale; label: string }[] = [
  { value: "substantial", label: "Substantial" },
  { value: "trivial", label: "Trivial" },
];

// State facet options grouped by lifecycle lane.
export const STATE_FACET_GROUPS: { lane: string; states: ManagerTicketState[] }[] =
  LANES.map((lane) => ({ lane: lane.label, states: lane.states }));

// True when any filter is active; sort and direction do not count.
export function isFilteredQuery(query: ManagerTicketListQuery): boolean {
  return (
    query.q.trim().length > 0 ||
    query.state.length > 0 ||
    query.priority.length > 0 ||
    query.scale.length > 0
  );
}
