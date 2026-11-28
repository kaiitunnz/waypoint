import { describe, expect, it } from "vitest";

import type { BoardGate } from "./board";
import {
  askNeedsDetail,
  askRow,
  askLine,
  askUserQuestions,
  buildQueue,
  decodeNeedsId,
  gateRouteId,
  needsCounts,
  needsHref,
  parseNeedsId,
  rowLine,
} from "./needsYou";
import { askPreview, askTitle } from "./pendingAskActions";
import type { InboxItem, ManagerTicket, PendingApprovalAsk, PendingPlanAsk, PendingQuestionAsk } from "./types";

const at = (minute: number) => `2026-11-27T08:${String(minute).padStart(2, "0")}:00Z`;

const base = (id: string, minute: number, title = id) => ({
  id,
  session_id: "s1",
  session_title: "Fix race",
  session_backend: "claude_code",
  spawner_session_id: null,
  created_at: at(minute),
  sequence: minute,
  availability: "actionable" as const,
  title,
  body: null,
});

const question = (minute: number, options: string[] = ["A", "B (Recommended)"], multi = false): PendingQuestionAsk => ({
  ...base(`question:s1:t${minute}`, minute, "Which fix?"),
  kind: "question",
  question: {
    tool_use_id: `t${minute}`,
    questions: [
      {
        question: "Which fix?",
        header: null,
        multi_select: multi,
        options: options.map((label) => ({ label, description: null, recommended: label.includes("(Recommended)") })),
      },
    ],
  },
});

const approval = (minute: number, input: Record<string, unknown> = { command: "git push\nls" }, truncated = false): PendingApprovalAsk => ({
  ...base(`approval:s1:a${minute}`, minute, "Run a command"),
  kind: "approval",
  approval: {
    approval_id: `a${minute}`,
    tool_name: "Bash",
    tool_input: input,
    tool_input_truncated: truncated,
    decisions: ["accept", "decline"],
    supports_note: true,
  },
});

const plan: PendingPlanAsk = {
  ...base("plan_approval:s1:p1", 1, "Plan"),
  kind: "plan_approval",
  plan: { plan_id: "p1", text: "# Regenerate figures\n- one", decisions: ["accept"], supports_note: true, resolve_via: "approve" },
};

const item = (id: string, minute: number, over: Partial<InboxItem> = {}): InboxItem => ({
  id,
  from_session_id: "s2",
  from_label: "Manager",
  subject: `Subject ${id}`,
  status: "open",
  read_at: null,
  version: 1,
  created_at: at(minute),
  updated_at: at(minute),
  blocks: [],
  ...over,
});

const gate = (id: string, minute: number, linked: InboxItem | null, inboxItemId = linked?.id): BoardGate => ({
  managerId: "mgr",
  project: "waypoint",
  channelPrefix: "w-ticket-",
  ticket: {
    id,
    state: "spec_review",
    title: `Ticket ${id}`,
    priority: "p1",
    inbox_item_id: inboxItemId,
    awaiting_since: at(minute),
    updated_at: at(0),
  } as ManagerTicket,
  item: linked,
});

const keys = (input: Parameters<typeof buildQueue>[0]) => buildQueue(input).map((entry) => entry.key);
const queue = { asks: [], items: [], gates: [], status: "open" as const, source: "all" as const, q: "" };

describe("buildQueue", () => {
  it("merges the sources newest first", () => {
    const result = keys({ ...queue, asks: [question(10)], items: [item("i1", 20)], gates: [gate("g1", 15, null)] });
    expect(result).toEqual(["i1", "gate:mgr:g1", "question:s1:t10"]);
  });

  it("gives a gate's item no row of its own while the gate is listed", () => {
    const linked = item("i1", 20);
    const input = { ...queue, items: [linked, item("i2", 21)], gates: [gate("g1", 30, linked)] };
    expect(keys(input)).toEqual(["gate:mgr:g1", "i2"]);
    expect(buildQueue(input)[0].routeId).toBe("i1");
    expect(keys({ ...input, status: "all" })).toEqual(["gate:mgr:g1", "i2"]);
    // Resolved lists no gates, so the item has its own row.
    expect(keys({ ...input, status: "resolved", items: [linked] })).toEqual(["i1"]);
  });

  it("hides an item named by a gate even when the item was not read", () => {
    expect(keys({ ...queue, items: [item("i1", 1)], gates: [gate("g1", 2, null, "i1")] })).toEqual(["gate:mgr:g1"]);
  });

  it("puts answered gates after everything still waiting", () => {
    const answered = gate("g1", 50, item("i9", 50, { status: "resolved" }));
    const input = { ...queue, asks: [question(1)], gates: [answered, gate("g2", 2, null)] };
    expect(keys(input)).toEqual(["gate:mgr:g2", "question:s1:t1", "gate:mgr:g1"]);
    expect(buildQueue(input)[2]).toMatchObject({ source: "board", answered: true });
  });

  it("filters by source and leaves asks and gates out of resolved", () => {
    const input = { ...queue, asks: [question(1)], items: [item("i1", 2)], gates: [gate("g1", 3, null)] };
    expect(keys({ ...input, source: "sessions" })).toEqual(["question:s1:t1"]);
    expect(keys({ ...input, source: "inbox" })).toEqual(["i1"]);
    expect(keys({ ...input, source: "board" })).toEqual(["gate:mgr:g1"]);
    expect(keys({ ...input, status: "resolved" })).toEqual(["i1"]);
  });

  it("searches asks by session and headline, and gates by their hidden item too", () => {
    const linked = item("i1", 2, { subject: "Spec review: lanes RFC" });
    const input = { ...queue, asks: [question(1)], gates: [gate("g1", 3, linked)] };
    expect(keys({ ...input, q: "fix race" })).toEqual(["question:s1:t1"]);
    expect(keys({ ...input, q: "which fix" })).toEqual(["question:s1:t1"]);
    expect(keys({ ...input, q: "lanes rfc" })).toEqual(["gate:mgr:g1"]);
    expect(keys({ ...input, q: "ticket g1" })).toEqual(["gate:mgr:g1"]);
    expect(keys({ ...input, q: "nothing" })).toEqual([]);
  });
});

describe("needsCounts", () => {
  it("counts a gate with an open item once and leaves answered gates out", () => {
    const gates = [
      gate("open", 1, item("i1", 1)),
      gate("none", 2, null),
      gate("answered", 3, item("i2", 3, { status: "resolved" })),
    ];
    // Two unresolved items: i1 (a gate's) and another.
    expect(needsCounts(2, 2, gates)).toEqual({ sessions: 2, inbox: 1, board: 2, all: 5 });
    expect(needsCounts(0, 0, [])).toEqual({ sessions: 0, inbox: 0, board: 0, all: 0 });
    // The unread count can trail the gates.
    expect(needsCounts(0, 0, [gates[0]])).toEqual({ sessions: 0, inbox: 0, board: 1, all: 1 });
  });
});

describe("routes", () => {
  it("tells asks, gates and items apart", () => {
    expect(parseNeedsId("approval:claude_code-1:a:b")).toEqual({
      kind: "ask",
      id: "approval:claude_code-1:a:b",
      sessionId: "claude_code-1",
    });
    expect(parseNeedsId("plan_approval:s:p")).toMatchObject({ kind: "ask", sessionId: "s" });
    expect(parseNeedsId("gate:mgr:t1")).toMatchObject({ kind: "gate" });
    expect(parseNeedsId("5cdc84aa")).toEqual({ kind: "inbox", id: "5cdc84aa" });
  });

  it("builds and decodes a detail path", () => {
    expect(needsHref("question:s:t")).toBe("/inbox/question%3As%3At");
    expect(decodeNeedsId("question%3As%3At")).toBe("question:s:t");
    expect(decodeNeedsId("%")).toBe("%");
  });

  it("routes a gate to its item", () => {
    expect(gateRouteId(gate("g", 1, item("i1", 1)))).toBe("i1");
    expect(gateRouteId(gate("g", 1, null))).toBe("gate:mgr:g");
  });
});

const NATIVE = { cwd: "/srv/app", fromScreen: false };
const SCREEN = { cwd: "/srv/app", fromScreen: true };

describe("asks", () => {
  it("sends a question a card cannot answer to the detail", () => {
    expect(askNeedsDetail(question(1))).toBe(false);
    expect(askNeedsDetail(question(1, [], false))).toBe(true);
    expect(askNeedsDetail(question(1, ["A"], true))).toBe(true);
    const two = question(1);
    two.question.questions.push({ ...two.question.questions[0], question: "Which test?" });
    expect(askNeedsDetail(two)).toBe(true);
    expect(askLine(two, "/srv/app")).toEqual({ text: "2 questions", mono: false });
  });

  it("says each ask in a line", () => {
    expect(askLine(question(1), "/srv/app")).toEqual({ text: "2 options, B recommended", mono: false });
    expect(askLine(question(1, []), "/srv/app")).toEqual({ text: "Free-form answer", mono: false });
    expect(askLine(approval(1), "/srv/app")).toEqual({ text: "git push …", mono: true });
    expect(askLine(approval(1, { command: "ls" }), "/srv/app")).toEqual({ text: "ls", mono: true });
    expect(askLine(approval(1, {}), "/srv/app")).toEqual({ text: "Bash", mono: false });
    expect(askLine(plan, "/srv/app")).toEqual({ text: "Regenerate figures", mono: false });
  });

  it("lets a row answer only what it can show whole", () => {
    expect(askRow(question(1), NATIVE)).toMatchObject({ answers: true, path: null });
    // A question read off the screen is still answered.
    expect(askRow(question(1), SCREEN).answers).toBe(true);
    expect(askRow(question(1, ["A"], true), NATIVE).answers).toBe(false);
    expect(askRow(plan, NATIVE).answers).toBe(false);
    const long = question(1);
    long.question.questions[0].question = `Which ${"very ".repeat(40)}fix?`;
    expect(askRow(long, NATIVE).answers).toBe(false);

    expect(askRow(approval(1), NATIVE)).toMatchObject({ text: "git push\nls", mono: true, answers: true });
    expect(askRow(approval(1), SCREEN)).toMatchObject({ text: "git push …", answers: false });
    expect(askRow(approval(1, { command: "ls", workdir: "/etc" }), NATIVE).answers).toBe(false);
    expect(askRow(approval(1, { command: "ls" }, true), NATIVE).answers).toBe(false);

    const edit = approval(1);
    edit.approval.tool_name = "Edit";
    edit.approval.tool_input = { file_path: "/srv/app/secrets.env" };
    expect(askRow(edit, NATIVE)).toMatchObject({ answers: false, path: "/srv/app/secrets.env" });
  });

  it("shapes a question for the editor", () => {
    expect(askUserQuestions(question(1, ["A"]))).toEqual([
      { question: "Which fix?", header: undefined, multiSelect: false, options: [{ label: "A", description: undefined }] },
    ]);
  });
});

describe("an approval titled by the call's summary", () => {
  const watch = (summary: string | null, input: Record<string, unknown> = {}): PendingApprovalAsk => {
    const ask = approval(1, { command: "rg -n modes src", ...input });
    return { ...ask, title: "Approve Monitor", approval: { ...ask.approval, tool_name: "Monitor", summary } };
  };

  it("takes the summary and keeps the command in the row", () => {
    const ask = watch("Search `modes`");
    expect(askTitle(ask, "/srv/app")).toEqual({ text: "Search `modes`", marked: true, agentType: null });
    expect(askPreview(ask, "/srv/app")).toEqual({ kind: "command", text: "rg -n modes src" });
    expect(askRow(ask, NATIVE)).toEqual({ text: "rg -n modes src", mono: true, path: null, answers: false });
  });

  it("keeps its own title, with no extra line, when the summary can't stand", () => {
    for (const ask of [watch(null), watch("Search \u202emodes"), watch("Search `modes`", { additionalPermissions: { network: true } })]) {
      expect(askTitle(ask, "/srv/app")).toEqual({ text: ask.title, marked: false, agentType: null });
      expect(askPreview(ask, "/srv/app")).toBeNull();
    }
    // A session not loaded yet has no directory to check the call's against.
    expect(askTitle(watch("Search `modes`", { workdir: "/srv/app" }), "").marked).toBe(false);
  });

  it("titles a described shell call that the row still decides", () => {
    const ask = approval(1, { command: "ls", description: "List the files" });
    ask.approval.summary = "List the files";
    expect(askTitle(ask, "/srv/app").text).toBe("List the files");
    expect(askRow(ask, NATIVE)).toMatchObject({ text: "ls", answers: true });
    // Screen-read calls the transport couldn't match keep the plain title.
    ask.approval.call_verified = false;
    expect(askTitle(ask, "/srv/app").marked).toBe(false);
  });

  it.each([
    ["Agent", "subagent_type", "Explore"],
    ["task", "agent", "general"],
  ])("names a %s approval's kind where its body would repeat the title", (tool, key, kind) => {
    const ask = approval(1, { description: "Find callers", prompt: "List them", [key]: kind });
    ask.body = `Approve subagent task: Find callers (via ${kind})`;
    ask.approval.tool_name = tool;
    ask.approval.summary = "Find callers";
    expect(askTitle(ask, "/srv/app")).toEqual({ text: "Find callers", marked: true, agentType: kind });
    expect(askRow(ask, NATIVE)).toMatchObject({ text: `Approve subagent task (via ${kind})`, mono: false, answers: false });
    ask.approval.summary = null;
    expect(askRow(ask, NATIVE).text).toBe(ask.body);
  });

  it("answers to its summary, as shown, in a search", () => {
    const queue = buildQueue({ asks: [watch("Search `modes`")], items: [], gates: [], status: "all", source: "all", q: "search modes" });
    expect(queue).toHaveLength(1);
  });

  it("keeps the command in view under a summary that reads like it", () => {
    const command = "echo `rm -rf ~`";
    const ask = approval(1, { command, description: command });
    ask.approval.summary = command;
    const row = askRow(ask, NATIVE);
    expect(row).toMatchObject({ answers: true, text: command });
    expect(rowLine(row, askTitle(ask, "/srv/app"))).toBe(command);
    // The ask's own title is not said twice.
    expect(rowLine({ ...row, text: ask.title }, { text: ask.title, marked: false })).toBeNull();
  });
});
