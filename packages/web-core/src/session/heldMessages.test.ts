import { describe, expect, it } from "vitest";

import type { HeldMessage } from "../types";
import { heldMessageOriginLabel, heldMessageText, holdReasonLabel, parseHeldMessagesFrame } from "./heldMessages";

const held = (overrides: Partial<HeldMessage> = {}): HeldMessage => ({
  id: "h1",
  session_id: "s1",
  origin: "agent",
  text: "hello",
  attachments: [],
  created_at: "2026-01-01T00:00:00Z",
  hold_reason: "idle",
  ...overrides,
});

describe("heldMessageOriginLabel", () => {
  it("names scheduled and wake-up origins", () => {
    expect(heldMessageOriginLabel(held({ origin: "schedule" }))).toBe("Scheduled");
    expect(heldMessageOriginLabel(held({ origin: "wake" }))).toBe("Wake-up");
  });

  it("names the sending session by title, then id, then a generic label", () => {
    expect(heldMessageOriginLabel(held({ sender_title: "Lead", sender_session_id: "s9" }))).toBe("From Lead");
    expect(heldMessageOriginLabel(held({ sender_title: "", sender_session_id: "s9" }))).toBe("From s9");
    expect(heldMessageOriginLabel(held({ sender_title: null, sender_session_id: null }))).toBe("From a session");
    expect(heldMessageOriginLabel(held())).toBe("From a session");
  });
});

describe("heldMessageText", () => {
  it("shows the text, or a placeholder for an empty message", () => {
    expect(heldMessageText(held())).toBe("hello");
    expect(heldMessageText(held({ text: "" }))).toBe("(no text)");
  });

  it("appends the attachment count with correct pluralisation", () => {
    expect(heldMessageText(held({ attachments: ["a"] }))).toBe("hello (+1 file)");
    expect(heldMessageText(held({ attachments: ["a", "b"] }))).toBe("hello (+2 files)");
    expect(heldMessageText(held({ text: "", attachments: ["a"] }))).toBe("(no text) (+1 file)");
  });
});

describe("holdReasonLabel", () => {
  it("reports a Focus hold as manual regardless of the current Focus state", () => {
    const message = held({ hold_reason: "focus" });
    expect(holdReasonLabel(message, false)).toEqual({ text: "Held by Focus", manual: true });
    expect(holdReasonLabel(message, true)).toEqual({ text: "Held by Focus", manual: true });
  });

  it("reports other holds as paused while Focus is on", () => {
    expect(holdReasonLabel(held({ hold_reason: "idle" }), true)).toEqual({ text: "Paused by Focus", manual: true });
    expect(holdReasonLabel(held({ hold_reason: "dialog" }), true)).toEqual({ text: "Paused by Focus", manual: true });
  });

  it("explains when a non-focus hold will release", () => {
    expect(holdReasonLabel(held({ hold_reason: "dialog" }), false)).toEqual({
      text: "Sends after you respond",
      manual: false,
    });
    expect(holdReasonLabel(held({ hold_reason: "idle" }), false)).toEqual({
      text: "Sends when idle",
      manual: false,
    });
    expect(holdReasonLabel(held({ hold_reason: "restore" }), false)).toEqual({
      text: "Sends when the session restores",
      manual: false,
    });
  });

  it("treats an unknown reason like idle", () => {
    const message = held({ hold_reason: "other" as HeldMessage["hold_reason"] });
    expect(holdReasonLabel(message, false)).toEqual({ text: "Sends when idle", manual: false });
  });
});

describe("parseHeldMessagesFrame", () => {
  it("keeps this session's list and ignores another session's frame", () => {
    expect(parseHeldMessagesFrame({ session_id: "s1", held_messages: [held()] }, "s1")).toHaveLength(1);
    expect(parseHeldMessagesFrame({ session_id: "s1" }, "s1")).toEqual([]);
    expect(parseHeldMessagesFrame({ session_id: "s2", held_messages: [] }, "s1")).toBeNull();
    expect(parseHeldMessagesFrame(null, "s1")).toBeNull();
  });
});
