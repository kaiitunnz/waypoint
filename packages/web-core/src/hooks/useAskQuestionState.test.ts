import { describe, expect, it, vi } from "vitest";

import { ApiError, AuthError } from "../api";
import { askFailure, sendAskRequest, type AskRequestPolicy } from "./useAskQuestionState";

describe("askFailure", () => {
  it("marks a not-ready 409 as retryable", () => {
    const error = new ApiError("the session is waiting on a dialog", 409);
    expect(askFailure(error, "send", "failed")).toEqual({
      operation: "send",
      notReady: true,
      message: "the session is waiting on a dialog",
    });
  });

  it("keeps the server's detail for other failures", () => {
    expect(askFailure(new ApiError("session is not running", 409), "cancel", "failed")).toEqual({
      operation: "cancel",
      notReady: false,
      message: "session is not running",
    });
    expect(askFailure("boom", "send", "failed to send answer").message).toBe("failed to send answer");
  });
});

describe("sendAskRequest", () => {
  const notReady = () => new ApiError("the session is waiting on a dialog", 409);
  const policy = (overrides: Partial<AskRequestPolicy> = {}): AskRequestPolicy => ({
    operation: "send",
    fallback: "failed to send answer",
    retry: true,
    onStart: vi.fn(),
    onRequestError: vi.fn(),
    retryOptions: { sleep: () => Promise.resolve() },
    ...overrides,
  });

  it("holds the question locked across retries and frees it once settled", async () => {
    const inFlight = new Set<string>();
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let calls = 0;
    const request = async () => {
      calls += 1;
      if (calls < 3) throw notReady();
    };
    const first = sendAskRequest(inFlight, "t", request, policy({ retryOptions: { sleep: () => gate } }));
    expect(inFlight.has("t")).toBe(true);
    expect(await sendAskRequest(inFlight, "t", request, policy())).toEqual({ status: "busy" });
    release();
    expect(await first).toEqual({ status: "sent" });
    expect(calls).toBe(3);
    expect(inFlight.size).toBe(0);
  });

  it("keeps a failure on the question once retries run out", async () => {
    const onRequestError = vi.fn();
    const result = await sendAskRequest(new Set(), "t", () => Promise.reject(notReady()), policy({ onRequestError }));
    expect(result).toEqual({
      status: "failed",
      failure: { operation: "send", notReady: true, message: "the session is waiting on a dialog" },
    });
    expect(onRequestError).not.toHaveBeenCalled();
  });

  it("reports an auth error, and every error without retry", async () => {
    const onRequestError = vi.fn();
    const auth = new AuthError();
    expect(await sendAskRequest(new Set(), "t", () => Promise.reject(auth), policy({ onRequestError }))).toEqual({
      status: "failed",
      failure: null,
    });
    expect(onRequestError).toHaveBeenLastCalledWith(auth, "failed to send answer");

    let calls = 0;
    const once = () => {
      calls += 1;
      return Promise.reject(notReady());
    };
    await sendAskRequest(new Set(), "t", once, policy({ onRequestError, retry: false }));
    expect(calls).toBe(1);
    expect(onRequestError).toHaveBeenCalledTimes(2);
  });
});
