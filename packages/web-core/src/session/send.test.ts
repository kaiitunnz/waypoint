import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, AuthError, cloneSession, forkSession, postAction, sendInput } from "../api";
import { buildCatalog } from "../backends";
import type { BackendDescriptor, SessionRecord } from "../types";
import { canFork, canInterrupt, composerAvailability, deliverInput, FollowUpError } from "./send";

vi.mock("../api", async (original) => ({
  ...(await original<typeof import("../api")>()),
  cloneSession: vi.fn(async () => ({ id: "child" })),
  forkSession: vi.fn(async () => ({ id: "fork" })),
  postAction: vi.fn(async () => {}),
  sendInput: vi.fn(async () => {}),
}));

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

const deliver = (text: string, patch: Partial<Parameters<typeof deliverInput>[0]> = {}) =>
  deliverInput({ host: "h", token: "t", sessionId: "s1", session: session(), catalog, text, ...patch });

beforeEach(() => vi.clearAllMocks());
afterEach(() => vi.clearAllMocks());

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

describe("deliverInput", () => {
  it("sends ordinary text with its command and attachments", async () => {
    await expect(deliver("hi", { attachments: ["a"] })).resolves.toEqual({ openSessionId: null });
    expect(sendInput).toHaveBeenCalledWith("h", "t", "s1", "hi", undefined, ["a"]);
    expect(postAction).not.toHaveBeenCalled();
  });

  it("clones for /new and sends the rest there", async () => {
    await expect(deliver("/new do it")).resolves.toEqual({ openSessionId: "child" });
    expect(cloneSession).toHaveBeenCalled();
    expect(sendInput).toHaveBeenCalledWith("h", "t", "child", "do it");
  });

  it("forks only when allowed", async () => {
    await expect(deliver("/fork")).resolves.toEqual({ openSessionId: "fork" });
    expect(sendInput).not.toHaveBeenCalled();
    await expect(deliver("/fork", { allowFork: false })).resolves.toEqual({ openSessionId: null });
    expect(sendInput).toHaveBeenCalledWith("h", "t", "s1", "/fork", undefined, undefined);
  });

  it("treats /news and attachment turns as text", async () => {
    await deliver("/news");
    await deliver("/new", { attachments: ["a"] });
    expect(cloneSession).not.toHaveBeenCalled();
    expect(forkSession).not.toHaveBeenCalled();
  });

  it("restarts a stopped session first when asked", async () => {
    await deliver("hi", { session: session({ status: "exited" }), reattachFirst: true });
    expect(postAction).toHaveBeenCalledWith("h", "t", "s1", "reattach");
    expect(vi.mocked(postAction).mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(sendInput).mock.invocationCallOrder[0],
    );
  });

  it("keeps the server's error and does not send after a failed restart", async () => {
    vi.mocked(postAction).mockRejectedValueOnce(new ApiError("session is still being restored", 409));
    await expect(
      deliver("hi", { session: session({ status: "exited" }), reattachFirst: true }),
    ).rejects.toMatchObject({ message: "session is still being restored", status: 409 });
    expect(sendInput).not.toHaveBeenCalled();
  });

  it("sends both commands as text where neither is allowed", async () => {
    await deliver("/new x", { allowNew: false, allowFork: false });
    expect(cloneSession).not.toHaveBeenCalled();
    expect(sendInput).toHaveBeenCalledWith("h", "t", "s1", "/new x", undefined, undefined);
  });

  it("names the created session when the follow-up send fails", async () => {
    vi.mocked(sendInput).mockRejectedValueOnce(new ApiError("agent is not ready", 409));
    const failure = await deliver("/new do it").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(FollowUpError);
    expect(failure).toMatchObject({ sessionId: "child", status: 409, message: "agent is not ready" });
  });

  it("keeps an expired token's error", async () => {
    vi.mocked(sendInput).mockRejectedValueOnce(new AuthError());
    await expect(deliver("/new do it")).rejects.toBeInstanceOf(AuthError);
  });
});
