// Board-workspace taxonomy: channel grouping, ticket-state to lane/lamp
// mapping, and kind/priority tones.

import { ManagerTicket, ManagerTicketState } from "@/lib/types";

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

// ─── Board rollup ───

export interface BoardRollup {
  needYou: number;
  inFlight: number;
  blocked: number;
  merged: number;
}

const IN_FLIGHT_STATES: ReadonlySet<ManagerTicketState> = new Set([
  "delegated",
  "building",
  "revising",
]);

export function rollupTickets(tickets: ManagerTicket[]): BoardRollup {
  const rollup: BoardRollup = { needYou: 0, inFlight: 0, blocked: 0, merged: 0 };
  for (const ticket of tickets) {
    if (isAwaiting(ticket.state)) rollup.needYou += 1;
    if (IN_FLIGHT_STATES.has(ticket.state)) rollup.inFlight += 1;
    if (ticket.state === "blocked") rollup.blocked += 1;
    if (ticket.state === "merged") rollup.merged += 1;
  }
  return rollup;
}
