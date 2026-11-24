import { describe, expect, it } from "vitest";

import {
  PLAN_DECISIONS,
  capturedTexts,
  inlineAttachmentIds,
  isModelChangeEvent,
  isModelSwitchEvent,
  isPlanEvent,
  isTaskNotificationEvent,
  itemIdForEvent,
  normalizeToolName,
  parseDiffPreviewPayload,
  parseEvent,
  parseTaskNotification,
  planForEvent,
  planTextForEvent,
  readToolName,
} from "./events";
import { ev } from "./test-helpers";

describe("normalizeToolName", () => {
  it("is null for empty input", () => {
    expect(normalizeToolName(null)).toBeNull();
    expect(normalizeToolName(undefined)).toBeNull();
    expect(normalizeToolName("")).toBeNull();
  });

  it("canonicalises known names case-insensitively", () => {
    expect(normalizeToolName("bash")).toBe("Bash");
    expect(normalizeToolName("MULTIEDIT")).toBe("MultiEdit");
    expect(normalizeToolName("todowrite")).toBe("TodoWrite");
    expect(normalizeToolName("question")).toBe("AskUserQuestion");
    expect(normalizeToolName("askuserquestion")).toBe("AskUserQuestion");
  });

  it("strips the default_api: prefix and canonicalises or capitalises the rest", () => {
    expect(normalizeToolName("default_api:read")).toBe("Read");
    expect(normalizeToolName("default_api:custom_tool")).toBe("Custom_tool");
    expect(normalizeToolName("default_api:")).toBe("");
  });

  it("passes unknown names through untouched", () => {
    expect(normalizeToolName("mcp__server__tool")).toBe("mcp__server__tool");
    expect(normalizeToolName("Custom")).toBe("Custom");
  });
});

describe("readToolName", () => {
  it("reads and normalises metadata.tool_name", () => {
    expect(readToolName(ev(1, "tool_call", "", { tool_name: "bash" }))).toBe("Bash");
    expect(readToolName(ev(1, "tool_call", "", { tool_name: "Mcp" }))).toBe("Mcp");
  });

  it("is null without a usable name", () => {
    expect(readToolName(ev(1, "tool_call", "", {}))).toBeNull();
    expect(readToolName(ev(1, "tool_call", "", { tool_name: "" }))).toBeNull();
    expect(readToolName(ev(1, "tool_call", "", { tool_name: 4 }))).toBeNull();
    expect(readToolName({ ...ev(1, "tool_call"), metadata: undefined } as never)).toBeNull();
  });
});

describe("parseDiffPreviewPayload", () => {
  it("is null without files", () => {
    expect(parseDiffPreviewPayload(null)).toBeNull();
    expect(parseDiffPreviewPayload({})).toBeNull();
    expect(parseDiffPreviewPayload({ files: "x" })).toBeNull();
    expect(parseDiffPreviewPayload({ files: [] })).toBeNull();
    expect(parseDiffPreviewPayload({ files: [null, 3, []] })).toBeNull();
  });

  it("converts snake_case payloads to the camelCase shape", () => {
    expect(
      parseDiffPreviewPayload({
        phase: "applied",
        total_additions: 3,
        total_deletions: 1,
        truncated: true,
        files: [
          {
            path: "a.ts",
            old_path: "b.ts",
            change_type: "move",
            diff: "@@",
            additions: 3,
            deletions: 1,
            truncated: true,
            binary: true,
            unavailable_reason: "too big",
          },
        ],
      }),
    ).toEqual({
      schemaVersion: 1,
      phase: "applied",
      totalAdditions: 3,
      totalDeletions: 1,
      truncated: true,
      files: [
        {
          path: "a.ts",
          oldPath: "b.ts",
          changeType: "move",
          diff: "@@",
          additions: 3,
          deletions: 1,
          truncated: true,
          binary: true,
          unavailableReason: "too big",
        },
      ],
    });
  });

  it("defaults missing and invalid fields", () => {
    const preview = parseDiffPreviewPayload({
      phase: "weird",
      files: [{ change_type: "rename", additions: Infinity, deletions: "2" }],
    });
    expect(preview).toEqual({
      schemaVersion: 1,
      phase: "proposed",
      totalAdditions: 0,
      totalDeletions: 0,
      truncated: false,
      files: [
        {
          path: "changes",
          oldPath: null,
          changeType: "unknown",
          diff: "",
          additions: 0,
          deletions: 0,
          truncated: false,
          binary: false,
          unavailableReason: null,
        },
      ],
    });
  });

  it("accepts every known phase and skips non-object files", () => {
    for (const phase of ["proposed", "applied", "aggregate"] as const) {
      expect(parseDiffPreviewPayload({ phase, files: [{ path: "x" }] })?.phase).toBe(phase);
    }
    expect(parseDiffPreviewPayload({ files: [null, { path: "x" }] })?.files).toHaveLength(1);
  });
});

describe("planForEvent", () => {
  it("reads a plan envelope with its own id, falling back to the item id", () => {
    const plan = planForEvent(
      ev(1, "agent_output", "", { plan: { id: "p1", text: "  Do it  ", source: "claude_code" } }),
    );
    expect(plan).toEqual({ id: "p1", text: "Do it", source: "claude_code", decisions: PLAN_DECISIONS });
    expect(planForEvent(ev(1, "agent_output", "", { item_id: "i9", plan: { text: "x" } }))?.id).toBe("i9");
  });

  it("defaults the source to codex and filters decisions", () => {
    const plan = planForEvent(
      ev(1, "agent_output", "", { plan: { id: "p", text: "x", decisions: ["accept", "bogus", 3, "cancel"] } }),
    );
    expect(plan?.source).toBe("codex");
    expect(plan?.decisions).toEqual(["accept", "cancel"]);
    expect(
      planForEvent(ev(1, "agent_output", "", { plan: { id: "p", text: "x", decisions: ["bogus"] } }))?.decisions,
    ).toEqual(PLAN_DECISIONS);
    expect(
      planForEvent(ev(1, "agent_output", "", { plan: { id: "p", text: "x", decisions: "no" } }))?.decisions,
    ).toEqual(PLAN_DECISIONS);
  });

  it("needs both text and an id in the envelope", () => {
    expect(planForEvent(ev(1, "agent_output", "", { plan: { id: "p", text: "   " } }))).toBeNull();
    expect(planForEvent(ev(1, "agent_output", "", { plan: { text: "x" } }))).toBeNull();
  });

  it("reads Codex plan items", () => {
    const event = ev(1, "agent_output", "", {
      item_type: "plan",
      item_id: "i1",
      payload: { item: { text: " the plan " } },
    });
    expect(planForEvent(event)).toEqual({ id: "i1", text: "the plan", source: "codex", decisions: PLAN_DECISIONS });
    const viaItemId = ev(1, "agent_output", "", { item_type: "plan", payload: { item: { id: "x", text: "t" } } });
    expect(planForEvent(viaItemId)?.id).toBe("x");
  });

  it("reads Claude ExitPlanMode approval requests", () => {
    const event = ev(1, "approval_request", "", {
      tool_name: "ExitPlanMode",
      approval_id: "a1",
      tool_input: { plan: " ship it " },
    });
    expect(planForEvent(event)).toEqual({
      id: "a1",
      text: "ship it",
      source: "claude_code",
      decisions: PLAN_DECISIONS,
    });
    expect(planForEvent(ev(1, "approval_request", "", { tool_name: "Bash", approval_id: "a1" }))).toBeNull();
    expect(planForEvent(ev(1, "approval_request", "", { tool_name: "ExitPlanMode", tool_input: { plan: "x" } }))).toBeNull();
    expect(planForEvent(ev(1, "tool_call", "", { tool_name: "ExitPlanMode", approval_id: "a", tool_input: { plan: "x" } }))).toBeNull();
  });

  it("is null for ordinary events", () => {
    expect(planForEvent(ev(1, "agent_output", "hello"))).toBeNull();
  });
});

describe("plan, model and notification predicates", () => {
  it("only counts backend-emitted plan items as plan text", () => {
    const codex = ev(1, "agent_output", "", { item_type: "plan", item_id: "i", payload: { item: { text: "t" } } });
    const approval = ev(1, "approval_request", "", { tool_name: "ExitPlanMode", approval_id: "a", tool_input: { plan: "t" } });
    expect(planTextForEvent(codex)).toBe("t");
    expect(isPlanEvent(codex)).toBe(true);
    expect(planTextForEvent(approval)).toBe("");
    expect(isPlanEvent(approval)).toBe(false);
    expect(isPlanEvent(ev(1, "agent_output", "x"))).toBe(false);
  });

  it("recognises model changes and switches", () => {
    const change = (reason: string) => ev(1, "system_note", "", { method: "model.change", reason });
    expect(isModelChangeEvent(change("switch"))).toBe(true);
    expect(isModelChangeEvent(change("initial_mismatch"))).toBe(true);
    expect(isModelSwitchEvent(change("switch"))).toBe(true);
    expect(isModelSwitchEvent(change("initial_mismatch"))).toBe(false);
    expect(isModelChangeEvent(ev(1, "system_note", ""))).toBe(false);
  });

  it("recognises both task notification methods", () => {
    expect(isTaskNotificationEvent(ev(1, "system_note", "", { method: "task_notification" }))).toBe(true);
    expect(isTaskNotificationEvent(ev(1, "system_note", "", { method: "claude.task_notification" }))).toBe(true);
    expect(isTaskNotificationEvent(ev(1, "system_note", "", { method: "other" }))).toBe(false);
    expect(isTaskNotificationEvent(ev(1, "system_note", ""))).toBe(false);
  });
});

describe("parseTaskNotification", () => {
  const notification = (payload: unknown, method = "task_notification") =>
    ev(1, "system_note", "", { method, task_notification: payload });

  it("is null for other events and malformed payloads", () => {
    expect(parseTaskNotification(ev(1, "system_note", "", {}))).toBeNull();
    expect(parseTaskNotification(notification(null))).toBeNull();
    expect(parseTaskNotification(notification({ version: 2, id: "a" }))).toBeNull();
    expect(parseTaskNotification(notification({ version: 1 }))).toBeNull();
    expect(parseTaskNotification(notification({ version: 1, id: "" }))).toBeNull();
  });

  it("applies defaults for a minimal payload", () => {
    expect(parseTaskNotification(notification({ version: 1, id: "n1" }))).toEqual({
      id: "n1",
      kind: "unknown",
      status: null,
      summary: null,
      event: null,
      note: null,
      taskId: null,
      toolUseId: null,
      resultPreview: null,
      resultTruncated: false,
      eventTruncated: false,
      noteTruncated: false,
      outputAvailable: false,
      outputUnavailableReason: null,
      usage: null,
    });
  });

  it("reads a full payload under either method name", () => {
    const payload = {
      version: 1,
      id: "n1",
      kind: "agent",
      status: "completed",
      summary: "done",
      event: "e",
      note: "n",
      task_id: "t1",
      tool_use_id: "u1",
      result_preview: "r",
      result_truncated: true,
      event_truncated: true,
      note_truncated: true,
      output_available: true,
      output_unavailable_reason: "gone",
      usage: { subagent_tokens: 10, tool_uses: 2, duration_ms: "x" },
    };
    for (const method of ["task_notification", "claude.task_notification"]) {
      expect(parseTaskNotification(notification(payload, method))).toMatchObject({
        id: "n1",
        kind: "agent",
        status: "completed",
        taskId: "t1",
        toolUseId: "u1",
        resultTruncated: true,
        eventTruncated: true,
        noteTruncated: true,
        outputAvailable: true,
        outputUnavailableReason: "gone",
        usage: { subagentTokens: 10, toolUses: 2, durationMs: null },
      });
    }
  });

  it("maps an unrecognised kind to unknown and accepts each known kind", () => {
    expect(parseTaskNotification(notification({ version: 1, id: "n", kind: "alien" }))?.kind).toBe("unknown");
    for (const kind of ["agent", "monitor", "background_command", "message", "unknown"]) {
      expect(parseTaskNotification(notification({ version: 1, id: "n", kind }))?.kind).toBe(kind);
    }
  });
});

describe("attachment helpers", () => {
  it("collects inline attachment ids, ignoring non-strings", () => {
    expect([...inlineAttachmentIds(ev(1, "agent_output", "", { inline_attachment_ids: ["a", 3, "b"] }))]).toEqual(["a", "b"]);
    expect(inlineAttachmentIds(ev(1, "agent_output", "", {})).size).toBe(0);
    expect(inlineAttachmentIds(ev(1, "agent_output", "", { inline_attachment_ids: "a" })).size).toBe(0);
  });

  it("collects captured report text", () => {
    expect(capturedTexts(ev(1, "agent_output", "", { captured_text: ["x", 1, "y"] }))).toEqual(["x", "y"]);
    expect(capturedTexts(ev(1, "agent_output", "", {}))).toEqual([]);
    expect(capturedTexts(ev(1, "agent_output", "", { captured_text: "x" }))).toEqual([]);
  });
});

describe("itemIdForEvent", () => {
  it("returns a non-empty string item id", () => {
    expect(itemIdForEvent(ev(1, "tool_call", "", { item_id: "i" }))).toBe("i");
    expect(itemIdForEvent(ev(1, "tool_call", "", { item_id: "" }))).toBeNull();
    expect(itemIdForEvent(ev(1, "tool_call", "", { item_id: 3 }))).toBeNull();
    expect(itemIdForEvent(ev(1, "tool_call", "", {}))).toBeNull();
  });
});

describe("parseEvent", () => {
  it("builds the typed envelope and keeps the raw metadata as extra", () => {
    const metadata = {
      version: 2,
      status: "ok",
      item_id: "i",
      item_type: "command",
      tool_name: "Bash",
      tool_input: { command: "ls" },
      tool_use_id: "u",
      item: { a: 1 },
      approval: { approval_id: "ap", tool_name: "Bash", tool_input: { x: 1 }, decisions: ["accept", 4] },
      unknown_field: true,
    };
    const envelope = parseEvent(ev(1, "tool_call", "text", metadata));
    expect(envelope).toMatchObject({
      version: 2,
      kind: "tool_call",
      text: "text",
      status: "ok",
      item: {
        itemId: "i",
        itemType: "command",
        toolName: "Bash",
        toolInput: { command: "ls" },
        toolUseId: "u",
        payload: { a: 1 },
      },
      approval: { approvalId: "ap", toolName: "Bash", toolInput: { x: 1 }, decisions: ["accept"] },
      diffPreview: null,
    });
    expect(envelope.extra).toEqual(metadata);
    expect(envelope.extra).not.toBe(metadata);
  });

  it("defaults everything for empty metadata", () => {
    expect(parseEvent(ev(1, "agent_output", "hi", {}))).toEqual({
      version: null,
      kind: "agent_output",
      text: "hi",
      status: null,
      item: { itemId: null, itemType: null, toolName: null, toolInput: null, toolUseId: null, payload: null },
      approval: undefined,
      diffPreview: null,
      extra: {},
    });
  });

  it("ignores non-record approval and tool input values and parses a diff preview", () => {
    const envelope = parseEvent(
      ev(1, "tool_call", "", {
        version: "1",
        approval: [1],
        tool_input: [1],
        diff_preview: { files: [{ path: "a" }] },
      }),
    );
    expect(envelope.version).toBeNull();
    expect(envelope.approval).toBeUndefined();
    expect(envelope.item?.toolInput).toBeNull();
    expect(envelope.diffPreview?.files[0].path).toBe("a");
  });
});
