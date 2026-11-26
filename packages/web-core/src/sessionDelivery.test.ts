import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, AuthError, cloneSession, forkSession, postAction, sendInput } from "./api";
import { buildCatalog } from "./backends";
import { deliverInput, FollowUpError, submitTerminalMessage } from "./sessionDelivery";
import type { BackendDescriptor, SessionRecord } from "./types";

vi.mock("./api", async (original) => ({
  ...(await original<typeof import("./api")>()),
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

  it("forks only where the session can", async () => {
    await expect(deliver("/fork")).resolves.toEqual({ openSessionId: "fork" });
    expect(sendInput).not.toHaveBeenCalled();
    const terminal = session({ transport: "tmux" });
    await expect(deliver("/fork", { session: terminal })).resolves.toEqual({ openSessionId: null });
    expect(sendInput).toHaveBeenCalledWith("h", "t", "s1", "/fork", undefined, undefined);
  });

  it("treats /news and attachment turns as text", async () => {
    await deliver("/news");
    await deliver("/new", { attachments: ["a"] });
    expect(cloneSession).not.toHaveBeenCalled();
    expect(forkSession).not.toHaveBeenCalled();
  });

  it("restarts a stopped session first", async () => {
    await deliver("hi", { session: session({ status: "exited" }) });
    expect(postAction).toHaveBeenCalledWith("h", "t", "s1", "reattach");
    expect(vi.mocked(postAction).mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(sendInput).mock.invocationCallOrder[0],
    );
  });

  it("keeps the server's error and does not send after a failed restart", async () => {
    vi.mocked(postAction).mockRejectedValueOnce(new ApiError("session is still being restored", 409));
    await expect(deliver("hi", { session: session({ status: "exited" }) })).rejects.toMatchObject({ message: "session is still being restored", status: 409 });
    expect(sendInput).not.toHaveBeenCalled();
  });

  it("sends both commands as text where control commands are off", async () => {
    await deliver("/new x", { controlCommands: false });
    await deliver("/fork x", { controlCommands: false });
    expect(cloneSession).not.toHaveBeenCalled();
    expect(forkSession).not.toHaveBeenCalled();
    expect(sendInput).toHaveBeenCalledWith("h", "t", "s1", "/new x", undefined, undefined);
    expect(sendInput).toHaveBeenCalledWith("h", "t", "s1", "/fork x", undefined, undefined);
  });

  it("names the created session when the follow-up send fails", async () => {
    vi.mocked(sendInput).mockRejectedValueOnce(new ApiError("agent is not ready", 409));
    const failure = await deliver("/new do it").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(FollowUpError);
    expect(failure).toMatchObject({ sessionId: "child", status: 409, message: "agent is not ready" });
  });

  it("names the step when something other than an Error is thrown", async () => {
    vi.mocked(cloneSession).mockRejectedValueOnce("x");
    await expect(deliver("/new")).rejects.toThrow("failed to create session");
    vi.mocked(forkSession).mockRejectedValueOnce("x");
    await expect(deliver("/fork")).rejects.toThrow("failed to fork session");
    vi.mocked(sendInput).mockRejectedValueOnce("x");
    await expect(deliver("/fork go")).rejects.toThrow("failed to fork session");
    vi.mocked(sendInput).mockRejectedValueOnce("x");
    await expect(deliver("/new go")).rejects.toThrow("failed to create session");
    vi.mocked(postAction).mockRejectedValueOnce("x");
    await expect(deliver("hi", { session: session({ status: "exited" }) })).rejects.toThrow("failed to reconnect");
    vi.mocked(sendInput).mockRejectedValueOnce("x");
    await expect(deliver("hi")).rejects.toThrow("failed to send input");
  });

  it("keeps an expired token's error", async () => {
    vi.mocked(sendInput).mockRejectedValueOnce(new AuthError());
    await expect(deliver("/new do it")).rejects.toBeInstanceOf(AuthError);
  });
});

describe("submitTerminalMessage", () => {
  const submit = (text: string, patch: Partial<Parameters<typeof submitTerminalMessage>[0]> = {}) =>
    submitTerminalMessage({
      host: "h",
      token: "t",
      sessionId: "s1",
      session: session({ transport: "tmux", backend: "tmux" }),
      catalog,
      text,
      attachmentIds: [],
      sendOverSocket: () => true,
      ...patch,
    });

  it("types plain text into the pane over the socket", async () => {
    const sendOverSocket = vi.fn(() => true);
    await expect(submit("ls", { sendOverSocket })).resolves.toEqual({ result: "ok", openSessionId: null });
    expect(sendOverSocket).toHaveBeenCalledWith("ls");
    expect(sendInput).not.toHaveBeenCalled();
  });

  it("reports a closed socket so the draft stays", async () => {
    await expect(submit("ls", { sendOverSocket: () => false })).resolves.toEqual({
      result: "socket-closed",
      openSessionId: null,
    });
  });

  it("sends attachments over HTTP, never as a control command", async () => {
    const sendOverSocket = vi.fn(() => true);
    await expect(submit("/new x", { attachmentIds: ["a"], sendOverSocket })).resolves.toEqual({
      result: "ok",
      openSessionId: null,
    });
    expect(sendInput).toHaveBeenCalledWith("h", "t", "s1", "/new x", undefined, ["a"]);
    expect(cloneSession).not.toHaveBeenCalled();
    expect(sendOverSocket).not.toHaveBeenCalled();
  });

  it("runs /new as a control command", async () => {
    const sendOverSocket = vi.fn(() => true);
    await expect(submit("/new go", { sendOverSocket })).resolves.toEqual({ result: "ok", openSessionId: "child" });
    expect(sendOverSocket).not.toHaveBeenCalled();
  });

  it("types /fork where the agent cannot fork, and skips commands while loading", async () => {
    const sendOverSocket = vi.fn(() => true);
    await submit("/fork", { sendOverSocket });
    await submit("/new", { session: null, sendOverSocket });
    expect(forkSession).not.toHaveBeenCalled();
    expect(cloneSession).not.toHaveBeenCalled();
    expect(sendOverSocket).toHaveBeenCalledTimes(2);
  });

  it("throws request failures", async () => {
    vi.mocked(sendInput).mockRejectedValueOnce(new Error("boom"));
    await expect(submit("x", { attachmentIds: ["a"] })).rejects.toThrow("boom");
  });
});
