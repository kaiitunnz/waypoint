import { describe, expect, it } from "vitest";

import {
  AWAITING_STATES,
  CHANNEL_GROUP_ORDER,
  DEFAULT_TICKET_QUERY,
  LANES,
  STATE_FACET_GROUPS,
  awaitingTickets,
  classifyChannel,
  gateBoardHref,
  gateHeadline,
  gateKey,
  gatePrUrl,
  gateTime,
  gateView,
  isGateAnswered,
  prNumber,
  groupChannels,
  isAwaiting,
  isFilteredQuery,
  kindTone,
  laneForState,
  managerGates,
  mergeGates,
  priorityTone,
  stateLabel,
  stateTone,
  ticketIdFromChannel,
} from "./board";
import type { BoardChannel, InboxItem, ManagerTicket, ManagerTicketListQuery, ManagerTicketState } from "./types";

const channel = (name: string) => ({ channel: name }) as BoardChannel;

describe("classifyChannel", () => {
  it.each([
    ["job:123", "job"],
    ["JOB:123", "other"],
    ["tickets", "manager"],
    ["org", "manager"],
    ["Org", "manager"],
    ["waypoint-tickets", "manager"],
    ["waypoint-org", "manager"],
    ["ticket-42", "ticket"],
    ["waypoint-ticket-42", "ticket"],
    ["waypoint-tickets-42", "other"],
    ["tickets-extra", "other"],
    ["random", "other"],
    ["", "other"],
  ])("puts %j in the %s group", (name, group) => {
    expect(classifyChannel(name)).toBe(group);
  });

  it("checks the job prefix before the manager names", () => {
    expect(classifyChannel("job:tickets")).toBe("job");
  });
});

describe("groupChannels", () => {
  const channels = [channel("job:1"), channel("tickets"), channel("ticket-9"), channel("misc"), channel("job:2")];

  it("buckets every channel by group, preserving order", () => {
    const groups = groupChannels(channels);
    expect(groups.job.map((c) => c.channel)).toEqual(["job:1", "job:2"]);
    expect(groups.manager.map((c) => c.channel)).toEqual(["tickets"]);
    expect(groups.ticket.map((c) => c.channel)).toEqual(["ticket-9"]);
    expect(groups.other.map((c) => c.channel)).toEqual(["misc"]);
    expect(Object.keys(groups).sort()).toEqual([...CHANNEL_GROUP_ORDER].sort());
  });

  it("filters by a case-insensitive, trimmed substring", () => {
    expect(groupChannels(channels, "  JOB ").job).toHaveLength(2);
    expect(groupChannels(channels, "ticket").ticket).toHaveLength(1);
    expect(groupChannels(channels, "zzz").other).toHaveLength(0);
  });

  it("treats a blank filter as no filter", () => {
    expect(groupChannels(channels, "   ").job).toHaveLength(2);
    expect(groupChannels(channels, "").other).toHaveLength(1);
  });

  it("returns empty groups for no channels", () => {
    expect(groupChannels([])).toEqual({ manager: [], ticket: [], job: [], other: [] });
  });
});

describe("ticketIdFromChannel", () => {
  it("strips the prefix", () => {
    expect(ticketIdFromChannel("ticket-42", "ticket-")).toBe("42");
  });

  it("is null without a matching prefix or with nothing left", () => {
    expect(ticketIdFromChannel("other-42", "ticket-")).toBeNull();
    expect(ticketIdFromChannel("ticket-", "ticket-")).toBeNull();
    expect(ticketIdFromChannel("ticket-42", null)).toBeNull();
    expect(ticketIdFromChannel("ticket-42", undefined)).toBeNull();
    expect(ticketIdFromChannel("ticket-42", "")).toBeNull();
  });
});

describe("lanes", () => {
  const states: ManagerTicketState[] = [
    "intake", "triaged", "spec_pending", "spec_review", "ready", "delegated", "building", "revising",
    "blocked", "review_requested", "merged", "deferred", "abandoned",
  ];

  it("assigns every state to exactly one lane", () => {
    const counts = new Map<string, number>();
    for (const lane of LANES) for (const state of lane.states) counts.set(state, (counts.get(state) ?? 0) + 1);
    for (const state of states) expect(counts.get(state)).toBe(1);
    expect(counts.size).toBe(states.length);
  });

  it("maps states to their lane key", () => {
    expect(laneForState("triaged")).toBe("intake");
    expect(laneForState("spec_review")).toBe("spec");
    expect(laneForState("revising")).toBe("build");
    expect(laneForState("review_requested")).toBe("review");
    expect(laneForState("abandoned")).toBe("done");
    expect(laneForState("mystery" as ManagerTicketState)).toBe("other");
  });

  it("builds the state facet groups from the lane labels", () => {
    expect(STATE_FACET_GROUPS).toHaveLength(LANES.length);
    expect(STATE_FACET_GROUPS[0]).toEqual({ lane: "Intake · Triage", states: ["intake", "triaged"] });
  });

  it("flags only the three approval gates as awaiting", () => {
    expect([...AWAITING_STATES].sort()).toEqual(["blocked", "review_requested", "spec_review"]);
    expect(isAwaiting("spec_review")).toBe(true);
    expect(isAwaiting("building")).toBe(false);
  });
});

describe("state tone and label", () => {
  it("maps states to lamp tones", () => {
    expect(stateTone("intake")).toBe("idle");
    expect(stateTone("spec_review")).toBe("awaiting");
    expect(stateTone("building")).toBe("active");
    expect(stateTone("revising")).toBe("revising");
    expect(stateTone("blocked")).toBe("blocked");
    expect(stateTone("merged")).toBe("done");
    expect(stateTone("mystery" as ManagerTicketState)).toBe("idle");
  });

  it("labels states, falling back to the raw value", () => {
    expect(stateLabel("review_requested")).toBe("Review requested");
    expect(stateLabel("spec_pending")).toBe("Spec pending");
    expect(stateLabel("mystery" as ManagerTicketState)).toBe("mystery");
  });
});

describe("kindTone", () => {
  it.each([
    ["progress", "muted"],
    ["strategy", "strategy"],
    ["done", "success"],
    ["partial", "success"],
    ["decision", "danger"],
    ["error", "danger"],
    ["attention", "warn"],
    ["respec", "warn"],
    ["relay", "relay"],
    ["spec_ready", "writer"],
    ["infeasible", "writer"],
    ["recommendation", "writer"],
  ])("maps %s to %s", (kind, tone) => {
    expect(kindTone(kind)).toBe(tone);
  });

  it("defaults to muted for unknown, empty or missing kinds", () => {
    expect(kindTone("whatever")).toBe("muted");
    expect(kindTone("")).toBe("muted");
    expect(kindTone(null)).toBe("muted");
    expect(kindTone(undefined)).toBe("muted");
  });
});

describe("priorityTone", () => {
  it("passes p0, p1 and p3 through and defaults everything else to p2", () => {
    expect(priorityTone("p0")).toBe("p0");
    expect(priorityTone("p1")).toBe("p1");
    expect(priorityTone("p2")).toBe("p2");
    expect(priorityTone("p3")).toBe("p3");
    expect(priorityTone("p9")).toBe("p2");
    expect(priorityTone(null)).toBe("p2");
    expect(priorityTone(undefined)).toBe("p2");
  });
});

describe("isFilteredQuery", () => {
  const query = (overrides: Partial<ManagerTicketListQuery> = {}): ManagerTicketListQuery => ({
    ...DEFAULT_TICKET_QUERY,
    ...overrides,
  });

  it("is false for the default query, whatever the sort", () => {
    expect(isFilteredQuery(DEFAULT_TICKET_QUERY)).toBe(false);
    expect(isFilteredQuery(query({ sort: "priority", direction: "asc" }))).toBe(false);
  });

  it("is true when text or any facet is set", () => {
    expect(isFilteredQuery(query({ q: "bug" }))).toBe(true);
    expect(isFilteredQuery(query({ state: ["ready"] }))).toBe(true);
    expect(isFilteredQuery(query({ priority: ["p0"] }))).toBe(true);
    expect(isFilteredQuery(query({ scale: ["trivial"] }))).toBe(true);
  });

  it("ignores whitespace-only search text", () => {
    expect(isFilteredQuery(query({ q: "   " }))).toBe(false);
  });
});

describe("gates", () => {
  const ticket = (id: string, state: ManagerTicketState, over: Partial<ManagerTicket> = {}) =>
    ({ id, state, priority: "p2", title: id, ...over }) as ManagerTicket;
  const gate = (over: Partial<ManagerTicket> = {}, item: InboxItem | null = null, channelPrefix: string | null = "p-ticket-") => ({
    managerId: "mgr-1",
    project: "waypoint",
    channelPrefix,
    ticket: ticket("t1", "spec_review", over),
    item,
  });

  it("lists awaiting tickets, highest priority first", () => {
    const tickets = [
      ticket("b", "blocked", { priority: "p1" }),
      ticket("x", "building"),
      ticket("a", "review_requested", { priority: "p0" }),
    ];
    expect(awaitingTickets(tickets).map((entry) => entry.id)).toEqual(["a", "b"]);
  });

  it("reads a pull request number", () => {
    expect(prNumber("https://github.com/o/r/pull/594")).toBe("594");
    expect(prNumber("#12")).toBe("12");
    expect(prNumber("https://example.test/mr/7/")).toBe("7");
    expect(prNumber("none")).toBeNull();
  });

  it("names the gate and what it asks", () => {
    expect(gateView(ticket("t", "review_requested", { pr_url: "https://github.com/o/r/pull/9" }))).toEqual({
      gate: "PR review",
      summary: "Review PR #9 and merge, request changes, or abort.",
    });
    expect(gateView(ticket("t", "spec_review")).gate).toBe("Spec review");
    expect(gateView(ticket("t", "blocked")).summary).toBe("The tech-lead is blocked and needs your decision.");
    expect(gateHeadline(ticket("t", "review_requested", { pr_url: "#9" }))).toBe("PR #9 ready to merge");
    expect(gateHeadline(ticket("t", "review_requested"))).toBe("Ready for review");
    expect(gateHeadline(ticket("t", "spec_review"))).toBe("Spec ready for review");
    expect(gateHeadline(ticket("t", "blocked"))).toBe("Blocked, needs your decision");
  });

  it("takes a manager's awaiting tickets as gates", () => {
    const summary = { id: "mgr-1", project: "waypoint", repo_dir: "/r", ticket_count: 2, attention_count: 1 };
    const state = {
      config: { render_context: { templates_dir: "", tickets_channel: "t", ticket_channel_prefix: "p-ticket-" } },
      tree: { free: true },
      tickets: [ticket("t1", "spec_review"), ticket("t2", "building")],
    } as unknown as Parameters<typeof managerGates>[1];
    expect(managerGates(summary, state)).toEqual([
      { managerId: "mgr-1", project: "waypoint", channelPrefix: "p-ticket-", ticket: state.tickets[0], item: null },
    ]);
  });

  it("keeps the gates of a manager that could not be read", () => {
    const mine = gate();
    const other = { ...gate(), managerId: "mgr-2" };
    expect(mergeGates([mine, other], { gates: [mine], failed: [] })).toEqual([mine]);
    expect(mergeGates([mine, other], { gates: [mine], failed: ["mgr-2"] })).toEqual([mine, other]);
    expect(mergeGates([mine], { gates: [], failed: ["mgr-2"] })).toEqual([]);
    expect(mergeGates(null, { gates: [other], failed: ["mgr-1"] })).toEqual([other]);
    expect(mergeGates(null, { gates: [], failed: ["mgr-1"] })).toBeNull();
  });

  it("keys and links a gate", () => {
    expect(gateKey(gate())).toBe("gate:mgr-1:t1");
    expect(gateBoardHref(gate())).toBe("/board?channel=p-ticket-t1");
    expect(gateBoardHref(gate({}, null, null))).toBe("/board");
  });

  it("links only a pull request a browser can open", () => {
    expect(gatePrUrl(ticket("t", "review_requested", { pr_url: "https://github.com/o/r/pull/9" }))).toBe(
      "https://github.com/o/r/pull/9",
    );
    expect(gatePrUrl(ticket("t", "review_requested", { pr_url: "#9" }))).toBeNull();
    expect(gatePrUrl(ticket("t", "blocked"))).toBeNull();
  });

  it("dates a gate from when it started waiting", () => {
    expect(gateTime(gate({ awaiting_since: "b", updated_at: "a" }))).toBe("b");
    expect(gateTime(gate({ updated_at: "a" }))).toBe("a");
  });

  it("is answered once its item is resolved", () => {
    expect(isGateAnswered(gate())).toBe(false);
    expect(isGateAnswered(gate({}, { status: "open" } as InboxItem))).toBe(false);
    expect(isGateAnswered(gate({}, { status: "resolved" } as InboxItem))).toBe(true);
  });
});
