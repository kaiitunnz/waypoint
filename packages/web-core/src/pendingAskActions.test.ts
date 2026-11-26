import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "./api";
import {
  answerQuestion,
  approvalRequest,
  buildAnswer,
  decideAsk,
  decisionView,
  inlineCommand,
  emptyPicks,
  isAnswerable,
  isAgentNotReady,
  planPreview,
  retryWhileNotReady,
  showsSend,
  stripRecommendedSuffix,
  tapOption,
  toggleOption,
  visibleOptions,
  type PromptPick,
  isRecommendedLabel,
} from "./pendingAskActions";
import { ev } from "./test-helpers";
import type {
  PendingApprovalAsk,
  PendingAskApproval,
  PendingAskPlan,
  PendingAskPrompt,
  PendingPlanAsk,
  PendingQuestionAsk,
} from "./types";

vi.mock("./api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./api")>();
  return {
    ...original,
    answerAskQuestion: vi.fn(),
    approveSession: vi.fn(),
    approvePlan: vi.fn(),
  };
});

const prompt = (overrides: Partial<PendingAskPrompt> = {}): PendingAskPrompt => ({
  question: "Which fix should I ship?",
  header: null,
  multi_select: false,
  options: [
    { label: "Lock around release (Recommended)", description: null, recommended: true },
    { label: "Retry on conflict", description: null, recommended: false },
  ],
  ...overrides,
});

describe("stripRecommendedSuffix", () => {
  it("drops a trailing marker, ignoring case and spacing", () => {
    expect(stripRecommendedSuffix("Lock around release (Recommended)")).toBe("Lock around release");
    expect(stripRecommendedSuffix("Both (recommended)  ")).toBe("Both");
    expect(stripRecommendedSuffix("Retry (Recommended) later")).toBe("Retry (Recommended) later");
    expect(stripRecommendedSuffix("(Recommended)")).toBe("(Recommended)");
  });
});

describe("answers", () => {
  it("holds one label for a single-select prompt and toggles for multi-select", () => {
    let pick: PromptPick = { labels: [], other: "" };
    pick = toggleOption(pick, "a", false);
    pick = toggleOption(pick, "b", false);
    expect(pick.labels).toEqual(["b"]);
    expect(toggleOption(pick, "b", false).labels).toEqual([]);

    let multi: PromptPick = { labels: [], other: "" };
    multi = toggleOption(multi, "a", true);
    multi = toggleOption(multi, "b", true);
    expect(multi.labels).toEqual(["a", "b"]);
    expect(toggleOption(multi, "a", true).labels).toEqual(["b"]);
  });

  it("needs every prompt answered by a label or text", () => {
    const prompts = [prompt(), prompt({ question: "Second?" })];
    expect(isAnswerable(prompts, emptyPicks(prompts))).toBe(false);
    expect(isAnswerable(prompts, [{ labels: ["x"], other: "" }, { labels: [], other: "  " }])).toBe(false);
    expect(isAnswerable(prompts, [{ labels: ["x"], other: "" }, { labels: [], other: "custom" }])).toBe(true);
    expect(isAnswerable([], [])).toBe(false);
  });

  it("serializes the verbatim label, with Other joined in", () => {
    const built = buildAnswer([prompt()], [{ labels: ["Lock around release (Recommended)"], other: "" }]);
    expect(built).toEqual({
      text: '"Which fix should I ship?"="Lock around release (Recommended)"',
      answers: [{ question: "Which fix should I ship?", answer: "Lock around release (Recommended)" }],
    });
    const multi = buildAnswer(
      [prompt({ multi_select: true }), prompt({ question: "Why?" })],
      [
        { labels: ["a", "b"], other: "c" },
        { labels: [], other: "because" },
      ],
    );
    expect(multi?.text).toBe('"Which fix should I ship?"="a, b, c", "Why?"="because"');
    expect(buildAnswer([prompt()], emptyPicks([prompt()]))).toBeNull();
  });
});

describe("decisions", () => {
  const approval = (overrides: Partial<PendingAskApproval> = {}): PendingApprovalAsk =>
    ({
      kind: "approval",
      approval: {
        approval_id: "r1",
        tool_name: "Bash",
        tool_input: { command: "git push -u origin feat/x" },
        tool_input_truncated: false,
        decisions: ["accept", "decline"],
        supports_note: true,
        ...overrides,
      },
    }) as unknown as PendingApprovalAsk;
  const plan = (decisions: string[]) =>
    ({ kind: "plan_approval", plan: { plan_id: "p1", text: "# Plan", decisions, supports_note: false, resolve_via: "approve" } }) as unknown as PendingPlanAsk;

  it("offers only a single decision, whatever wider grant the agent lists", () => {
    const listed = ["accept", "acceptForSession", "decline", "acceptAlways"];
    for (const ask of [approval({ decisions: listed }), plan(listed), plan([])]) {
      expect(decisionView(ask).actions.map((a) => [a.decision, a.label])).toEqual([
        ["accept", "Approve"],
        ["decline", "Decline"],
      ]);
    }
  });

  it("previews the command or the edited file, and carries the plan text", () => {
    expect(decisionView(approval()).preview).toEqual({ kind: "command", text: "git push -u origin feat/x" });
    expect(decisionView(approval({ tool_name: "Edit", tool_input: { file_path: "/a/b.py" } })).preview).toEqual({
      kind: "path",
      path: "/a/b.py",
    });
    expect(decisionView(approval({ tool_name: "WebFetch", tool_input: { url: "x" } })).preview).toBeNull();
    expect(decisionView(approval({ tool_input: null })).preview).toBeNull();
    expect(decisionView(plan([])).planBody).toBe("# Plan");
    expect(decisionView(approval()).planBody).toBeNull();
  });

  it("reads the note support from the approval or the plan", () => {
    expect(decisionView(approval({ supports_note: false })).supportsNote).toBe(false);
    expect(decisionView(approval()).supportsNote).toBe(true);
  });
});

describe("deciding outside the detail", () => {
  const approval = (
    tool_name: string | null,
    tool_input: Record<string, unknown> | null,
    tool_input_truncated = false,
  ): PendingApprovalAsk => ({
    id: "approval:s:a",
    session_id: "s",
    session_title: "Session",
    session_backend: "x",
    spawner_session_id: null,
    created_at: "2026-11-27T08:00:00Z",
    sequence: 1,
    availability: "actionable",
    title: "Approve",
    body: null,
    kind: "approval",
    approval: {
      approval_id: "a",
      tool_name,
      tool_input,
      tool_input_truncated,
      decisions: ["accept", "decline"],
      supports_note: false,
    },
  });
  const plan = (text: string, resolve_via: PendingAskPlan["resolve_via"]) =>
    ({ kind: "plan_approval", plan: { plan_id: "p", text, resolve_via } }) as unknown as PendingPlanAsk;
  const session = { cwd: "/w", fromScreen: false };
  const inline = (tool: string | null, input: Record<string, unknown> | null) => inlineCommand(approval(tool, input), false);

  it("decides a plain shell command that lists whole", () => {
    expect(inline("Bash", { command: "git push" })).toBe("git push");
    expect(inline("bash", { command: "a\nb\nc", description: "x", timeout: 5, run_in_background: true })).toBe("a\nb\nc");
    expect(inline("Bash", { command: "x".repeat(160) })).toBe("x".repeat(160));
    expect(inline("Bash", { command: "x".repeat(161) })).toBeNull();
    expect(inline("Bash", { command: "a\nb\nc\nd" })).toBeNull();
  });

  it("opens a call that carries anything else", () => {
    for (const extra of [
      { dangerouslyDisableSandbox: true },
      { dangerouslyDisableSandbox: false },
      { workdir: "/etc" },
      { cwd: "/w" },
      { anything: 1 },
    ]) {
      expect(inline("Bash", { command: "ls", ...extra })).toBeNull();
    }
  });

  it("opens a command read off the screen, which may not be the one asked", () => {
    expect(inlineCommand(approval("Bash", { command: "ls" }), true)).toBeNull();
  });

  it("opens everything else", () => {
    expect(inline("Bash", null)).toBeNull();
    expect(inline("Bash", {})).toBeNull();
    expect(inline("Bash", { command: " " })).toBeNull();
    expect(inline("Bash", { command: ["ls"] })).toBeNull();
    expect(inline(null, { command: "ls" })).toBeNull();
    expect(inline("Edit", { file_path: "/srv/a.ts" })).toBeNull();
    expect(inline("mcp__sh__run", { command: "ls" })).toBeNull();
    // Cut by the server.
    expect(inlineCommand(approval("Bash", { command: "git pu" }, true), false)).toBeNull();
    // It would not read as it runs.
    expect(inline("Bash", { command: "ls # \u202e/ fr- mr ;" })).toBeNull();
    expect(inline("Bash", { command: "rm\u200b -rf a" })).toBeNull();
    expect(inlineCommand(plan("# Plan", "approve"), false)).toBeNull();
  });

  it("shows a request from its transcript", () => {
    const request = ev(1, "approval_request", "Approve Bash", { tool_name: "Bash", tool_input: { command: "ls" } });
    const ask = approval("Bash", { command: "ls" });
    expect(approvalRequest(ask, null, session)).toEqual({ status: "loading" });
    expect(approvalRequest(ask, "missing", session)).toEqual({ status: "failed" });
    expect(approvalRequest(ask, "failed", session)).toEqual({ status: "failed" });
    expect(approvalRequest(ask, request, session)).toMatchObject({
      status: "ready",
      body: { kind: "command", command: "ls" },
      fields: { tool_name: "Bash", tool_input: { command: "ls" } },
      whole: true,
    });
  });

  it("takes a plan's text from its ask, beside the rest of its call", () => {
    const request = ev(1, "approval_request", "Approve plan", {
      tool_name: "ExitPlanMode",
      tool_input: { plan: "# Plan", allowedPrompts: [{ tool: "Bash", prompt: "run tests" }] },
    });
    expect(approvalRequest(plan("# Plan", "approve"), null, session)).toEqual({ status: "loading" });
    expect(approvalRequest(plan("# Plan", "approve"), request, session)).toMatchObject({
      body: { kind: "plan", plan: "# Plan" },
      warnings: ["Extra permissions or network access"],
      whole: true,
    });
    // A plan from Waypoint's own plan mode has no call to read.
    // Its source shows under the rendered view.
    expect(approvalRequest(plan("# Plan", "approve_plan"), null, session)).toEqual({
      status: "ready",
      body: { kind: "plan", plan: "# Plan" },
      fields: { plan: "# Plan" },
      warnings: [],
      unverified: false,
      whole: true,
    });
    expect(approvalRequest(plan(" ", "approve_plan"), null, session)).toMatchObject({ whole: false });
  });
});

describe("option taps", () => {
  const options = [
    { label: "Lock (Recommended)", description: null, recommended: true },
    { label: "Retry", description: null, recommended: false },
    { label: "Both", description: null, recommended: false },
  ];
  const single: PendingAskPrompt = { question: "Which?", header: null, multi_select: false, options };
  const multi: PendingAskPrompt = { ...single, multi_select: true };

  it("sends a lone single-select tap, again on a repeat tap", () => {
    const first = tapOption([single], emptyPicks([single]), 0, "Retry");
    expect(first).toEqual({ picks: [{ labels: ["Retry"], other: "" }], send: true });
    expect(tapOption([single], first.picks, 0, "Retry")).toEqual({ picks: first.picks, send: true });
  });

  it("toggles a multi-select option without sending", () => {
    const first = tapOption([multi], emptyPicks([multi]), 0, "Retry");
    expect(first).toEqual({ picks: [{ labels: ["Retry"], other: "" }], send: false });
    expect(tapOption([multi], first.picks, 0, "Retry").picks).toEqual([{ labels: [], other: "" }]);
  });

  it("only toggles when there are several prompts", () => {
    const picks = emptyPicks([single, single]);
    expect(tapOption([single, single], picks, 1, "Both")).toEqual({
      picks: [picks[0], { labels: ["Both"], other: "" }],
      send: false,
    });
  });

  it("shows Send for a multi-select prompt or typed text", () => {
    expect(showsSend(single, { labels: [], other: "" })).toBe(false);
    expect(showsSend(single, { labels: [], other: " custom " })).toBe(true);
    expect(showsSend(multi, { labels: [], other: "" })).toBe(true);
  });

  it("cuts a long single-select list, recommended first, and shows a multi-select whole", () => {
    expect(visibleOptions(single, undefined, false)).toEqual({ shown: options, limited: false });
    const cut = visibleOptions(single, 2, false);
    expect(cut.limited).toBe(true);
    expect(cut.shown.map((o) => o.label)).toEqual(["Lock (Recommended)", "Retry"]);
    expect(visibleOptions(single, 2, true).limited).toBe(false);
    expect(visibleOptions(multi, 2, false)).toEqual({ shown: options, limited: false });
  });
});

describe("planPreview", () => {
  it("drops markdown marks, blank lines and everything past the first lines", () => {
    expect(planPreview("# Plan: create hello.txt\n\n## Context\nCreate a `file`.\n- step one\n- step two")).toBe(
      "Plan: create hello.txt\nContext\nCreate a file.",
    );
    expect(planPreview("one\ntwo", 5)).toBe("one\ntwo");
    expect(planPreview("")).toBe("");
  });
});

describe("retryWhileNotReady", () => {
  const conflict = () => new api.ApiError("the session is waiting on a dialog", 409);

  it("retries a 409 and returns the first success", async () => {
    const run = vi.fn().mockRejectedValueOnce(conflict()).mockRejectedValueOnce(conflict()).mockResolvedValue("ok");
    const sleep = vi.fn().mockResolvedValue(undefined);
    const onRetry = vi.fn();
    await expect(retryWhileNotReady(run, { sleep, onRetry, delayMs: 10 })).resolves.toBe("ok");
    expect(run).toHaveBeenCalledTimes(3);
    expect(sleep).toHaveBeenCalledWith(10);
    expect(onRetry).toHaveBeenCalledTimes(2);
  });

  it("gives up after the attempts and rethrows the conflict", async () => {
    const run = vi.fn().mockRejectedValue(conflict());
    await expect(retryWhileNotReady(run, { attempts: 3, sleep: async () => {} })).rejects.toMatchObject({
      status: 409,
    });
    expect(run).toHaveBeenCalledTimes(3);
  });

  it("recognises both not-ready details and no other conflict", () => {
    expect(isAgentNotReady(new api.ApiError("the agent is not ready for a reply to this question yet", 409))).toBe(true);
    expect(isAgentNotReady(new api.ApiError("session is not running; resume it to act on this question", 409))).toBe(
      false,
    );
    expect(isAgentNotReady(new api.ApiError("question operation already in progress", 409))).toBe(false);
  });

  it("does not retry other errors", async () => {
    const run = vi.fn().mockRejectedValue(new api.ApiError("nope", 400));
    await expect(retryWhileNotReady(run, { sleep: async () => {} })).rejects.toThrow("nope");
    expect(run).toHaveBeenCalledTimes(1);
    expect(isAgentNotReady(new Error("409"))).toBe(false);
  });
});

describe("answering and deciding", () => {
  beforeEach(() => vi.clearAllMocks());
  const base = { session_id: "s1", availability: "actionable", title: "t", body: null } as const;

  it("sends an answer to answer-question with the tool use id", async () => {
    const ask = {
      ...base,
      kind: "question",
      question: { tool_use_id: "tu1", questions: [prompt()] },
    } as unknown as PendingQuestionAsk;
    await answerQuestion("h", "t", ask, [{ labels: ["Retry on conflict"], other: "" }]);
    expect(api.answerAskQuestion).toHaveBeenCalledWith(
      "h",
      "t",
      "s1",
      '"Which fix should I ship?"="Retry on conflict"',
      "tu1",
      [{ question: "Which fix should I ship?", answer: "Retry on conflict" }],
    );
  });

  it("refuses to send an unanswered question", async () => {
    const ask = {
      ...base,
      kind: "question",
      question: { tool_use_id: "tu1", questions: [prompt()] },
    } as unknown as PendingQuestionAsk;
    await expect(answerQuestion("h", "t", ask, emptyPicks([prompt()]))).rejects.toThrow("nothing to send");
    expect(api.answerAskQuestion).not.toHaveBeenCalled();
  });

  it("sends an approval with its id and a trimmed note", async () => {
    const ask = {
      ...base,
      kind: "approval",
      approval: { approval_id: "r1", decisions: [], supports_note: true },
    } as unknown as PendingApprovalAsk;
    await decideAsk("h", "t", ask, "decline", "  no  ");
    expect(api.approveSession).toHaveBeenCalledWith("h", "t", "s1", "decline", "no", "r1");
  });

  it("routes a plan by resolve_via", async () => {
    const plan = (resolve_via: PendingAskPlan["resolve_via"]) =>
      ({ ...base, kind: "plan_approval", plan: { plan_id: "p1", decisions: [], resolve_via } }) as unknown as PendingPlanAsk;
    await decideAsk("h", "t", plan("approve"), "accept", undefined);
    expect(api.approveSession).toHaveBeenCalledWith("h", "t", "s1", "accept", undefined, "p1");
    await decideAsk("h", "t", plan("approve_plan"), "accept", "go");
    expect(api.approvePlan).toHaveBeenCalledWith("h", "t", "s1", "p1", "accept", "go");
    await decideAsk("h", "t", plan("approve_plan"), "decline", undefined);
    expect(api.approvePlan).toHaveBeenLastCalledWith("h", "t", "s1", "p1", "decline", undefined);
  });

  it("retries a 409 on the way", async () => {
    vi.mocked(api.approveSession)
      .mockRejectedValueOnce(new api.ApiError("the session is waiting on a dialog", 409))
      .mockResolvedValueOnce(undefined);
    const ask = {
      ...base,
      kind: "approval",
      approval: { approval_id: "r1", decisions: [], supports_note: false },
    } as unknown as PendingApprovalAsk;
    await decideAsk("h", "t", ask, "accept", undefined, { sleep: async () => {} });
    expect(api.approveSession).toHaveBeenCalledTimes(2);
  });
});

describe("isRecommendedLabel", () => {
  it("spots the trailing marker in any case", () => {
    expect(isRecommendedLabel("A (Recommended)")).toBe(true);
    expect(isRecommendedLabel("A (recommended) ")).toBe(true);
    expect(isRecommendedLabel("Recommended A")).toBe(false);
  });
});
