import { describe, expect, it } from "vitest";

import { buildCatalog } from "../backends";
import type { BackendDescriptor, SessionRecord } from "../types";
import { canFork, canInterrupt, composerAvailability, draftAfterFailedSend } from "./send";

const catalog = buildCatalog([
  {
    id: "claude_code",
    transport_id: "claude_tty",
    supported_transports: ["claude_tty", "tmux"],
    agent_capabilities: { supports_fork: true },
    transport_capabilities: { supports_reattach_after_exit: true, live_terminal: false },
  } as unknown as BackendDescriptor,
  {
    id: "tmux",
    transport_id: "tmux",
    supported_transports: ["tmux"],
    agent_capabilities: { supports_fork: false },
    transport_capabilities: { supports_reattach_after_exit: false, live_terminal: true },
  } as unknown as BackendDescriptor,
]);

const session = (patch: Partial<SessionRecord> = {}) =>
  ({ id: "s1", backend: "claude_code", transport: "claude_tty", status: "idle", ...patch }) as SessionRecord;

describe("composerAvailability", () => {
  it("disables until the session loads and for a dead one that cannot restart", () => {
    expect(composerAvailability(null, catalog)).toEqual({ disabled: true, dormant: false });
    expect(composerAvailability(session(), catalog)).toEqual({ disabled: false, dormant: false });
    expect(composerAvailability(session({ status: "exited" }), catalog)).toEqual({ disabled: false, dormant: true });
    expect(composerAvailability(session({ backend: "tmux", transport: "tmux", status: "error" }), catalog)).toEqual({
      disabled: true,
      dormant: false,
    });
  });
});

describe("canInterrupt", () => {
  it("covers a running or parked turn but not an answered-later question", () => {
    expect(canInterrupt(session({ status: "running" }))).toBe(true);
    expect(canInterrupt(session({ status: "waiting_input" }))).toBe(true);
    expect(canInterrupt(session({ status: "waiting_input", awaiting_answer: true }))).toBe(false);
    expect(canInterrupt(session())).toBe(false);
    expect(canInterrupt(null)).toBe(false);
  });
});

describe("canFork", () => {
  it("needs the agent and a forkable transport", () => {
    expect(canFork(session(), catalog)).toBe(true);
    expect(canFork(session({ transport: "tmux" }), catalog)).toBe(false);
    expect(canFork(null, catalog)).toBe(false);
  });
});

describe("draftAfterFailedSend", () => {
  const immediate = { immediate: true, sent: "hello", before: " hello ", kept: "" };

  it("puts an untouched field back as it was, and a retry empties it", () => {
    expect(draftAfterFailedSend({ ...immediate, current: "" })).toEqual({ draft: " hello ", rest: "" });
  });

  it("keeps what was typed since after the message, and a retry leaves it", () => {
    expect(draftAfterFailedSend({ ...immediate, current: "next" })).toEqual({ draft: "hello\nnext", rest: "next" });
  });

  it("adds no blank line for files sent without text", () => {
    expect(draftAfterFailedSend({ ...immediate, sent: "", before: "", current: "next" })).toEqual({
      draft: "next",
      rest: "next",
    });
  });

  it("leaves the field of a queued send alone", () => {
    const queued = { immediate: false, sent: "hello", before: "hello", kept: "" };
    expect(draftAfterFailedSend({ ...queued, current: "hello" })).toEqual({ draft: "hello", rest: "" });
    expect(draftAfterFailedSend({ ...queued, current: "hello again" })).toEqual({
      draft: "hello again",
      rest: "hello again",
    });
  });

  it("restores a retried message that was left in the field", () => {
    // A retry starts from the restored field and keeps only what followed it.
    const retried = { immediate: true, sent: "hello", before: "hello\nnext", kept: "next" };
    expect(draftAfterFailedSend({ ...retried, current: "next" })).toEqual({ draft: "hello\nnext", rest: "next" });
  });
});
