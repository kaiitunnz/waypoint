import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "./api";
import {
  approvalActions,
  approvalPreview,
  buildAnswer,
  emptyPicks,
  isAnswerable,
  isAgentNotReady,
  planActions,
  sessionScopedAllow,
  planPreview,
  respondToAsk,
  retryWhileNotReady,
  stripRecommendedSuffix,
  toggleOption,
  type PromptPick,
} from "./pendingAskActions";
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
  const approval = (overrides: Partial<PendingAskApproval> = {}): PendingAskApproval => ({
    approval_id: "r1",
    tool_name: "Bash",
    tool_input: { command: "git push -u origin feat/x" },
    tool_input_truncated: false,
    decisions: ["accept", "decline"],
    supports_note: true,
    ...overrides,
  });

  it("always offers approve and decline, and an escalation only when listed", () => {
    expect(approvalActions(approval()).map((a) => a.decision)).toEqual(["accept", "decline"]);
    expect(
      approvalActions(approval({ decisions: ["accept", "acceptForSession", "decline", "acceptAlways"] })).map(
        (a) => a.decision,
      ),
    ).toEqual(["accept", "acceptForSession", "decline"]);
    expect(planActions({ decisions: ["acceptForSession"] } as PendingAskPlan).map((a) => a.label)).toEqual([
      "Approve",
      "For this session",
      "Decline",
    ]);
  });

  it("sends the agent's own id for the session-scoped allow, whatever its spelling", () => {
    for (const id of ["acceptForSession", "allow_for_session", "approve-for-this-session", "AllowForSession"]) {
      const actions = approvalActions(approval({ decisions: ["approve", id, "decline"] }));
      expect(actions.map((a) => [a.decision, a.label])).toEqual([
        ["accept", "Approve"],
        [id, "For this session"],
        ["decline", "Decline"],
      ]);
    }
    expect(sessionScopedAllow(["approve", "acceptAlways", "decline"])).toBeUndefined();
    expect(sessionScopedAllow([])).toBeUndefined();
  });

  it("keeps Approve primary and Decline last", () => {
    const actions = approvalActions(approval({ decisions: ["approve", "acceptForSession", "decline"] }));
    expect(actions.map((a) => a.tone)).toEqual(["primary", "quiet", "danger"]);
  });

  it("previews the command or the edited file", () => {
    expect(approvalPreview(approval())).toEqual({ kind: "command", text: "git push -u origin feat/x" });
    expect(approvalPreview(approval({ tool_name: "Edit", tool_input: { file_path: "/a/b.py" } }))).toEqual({
      kind: "path",
      tool: "Edit",
      path: "/a/b.py",
    });
    expect(approvalPreview(approval({ tool_name: "WebFetch", tool_input: { url: "x" } }))).toEqual({
      kind: "none",
    });
    expect(approvalPreview(approval({ tool_input: null }))).toEqual({ kind: "none" });
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

  it("does not retry other errors", async () => {
    const run = vi.fn().mockRejectedValue(new api.ApiError("nope", 400));
    await expect(retryWhileNotReady(run, { sleep: async () => {} })).rejects.toThrow("nope");
    expect(run).toHaveBeenCalledTimes(1);
    expect(isAgentNotReady(new Error("409"))).toBe(false);
  });
});

describe("respondToAsk", () => {
  beforeEach(() => vi.clearAllMocks());
  const base = { session_id: "s1", availability: "actionable", title: "t", body: null } as const;

  it("sends an answer to answer-question with the tool use id", async () => {
    const ask = {
      ...base,
      kind: "question",
      question: { tool_use_id: "tu1", questions: [prompt()] },
    } as unknown as PendingQuestionAsk;
    await respondToAsk("h", "t", ask, { kind: "answer", picks: [{ labels: ["Retry on conflict"], other: "" }] });
    expect(api.answerAskQuestion).toHaveBeenCalledWith(
      "h",
      "t",
      "s1",
      '"Which fix should I ship?"="Retry on conflict"',
      "tu1",
      [{ question: "Which fix should I ship?", answer: "Retry on conflict" }],
    );
  });

  it("sends an approval with its id and a trimmed note", async () => {
    const ask = {
      ...base,
      kind: "approval",
      approval: { approval_id: "r1", decisions: [], supports_note: true },
    } as unknown as PendingApprovalAsk;
    await respondToAsk("h", "t", ask, { kind: "decision", decision: "decline", note: "  no  " });
    expect(api.approveSession).toHaveBeenCalledWith("h", "t", "s1", "decline", "no", "r1");
  });

  it("routes a plan by resolve_via", async () => {
    const plan = (resolve_via: PendingAskPlan["resolve_via"]) =>
      ({ ...base, kind: "plan_approval", plan: { plan_id: "p1", decisions: [], resolve_via } }) as unknown as PendingPlanAsk;
    await respondToAsk("h", "t", plan("approve"), { kind: "decision", decision: "accept" });
    expect(api.approveSession).toHaveBeenCalledWith("h", "t", "s1", "accept", undefined, "p1");
    await respondToAsk("h", "t", plan("approve_plan"), { kind: "decision", decision: "accept", note: "go" });
    expect(api.approvePlan).toHaveBeenCalledWith("h", "t", "s1", "p1", "accept", "go");
    await respondToAsk("h", "t", plan("approve_plan"), { kind: "decision", decision: "allow_for_session" });
    expect(api.approvePlan).toHaveBeenLastCalledWith("h", "t", "s1", "p1", "acceptForSession", undefined);
  });

  it("retries a 409 on the way", async () => {
    vi.mocked(api.approveSession)
      .mockRejectedValueOnce(new api.ApiError("dialog", 409))
      .mockResolvedValueOnce(undefined);
    const ask = {
      ...base,
      kind: "approval",
      approval: { approval_id: "r1", decisions: [], supports_note: false },
    } as unknown as PendingApprovalAsk;
    await respondToAsk("h", "t", ask, { kind: "decision", decision: "accept" }, { sleep: async () => {} });
    expect(api.approveSession).toHaveBeenCalledTimes(2);
  });
});
