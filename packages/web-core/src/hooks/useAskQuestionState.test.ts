import { describe, expect, it } from "vitest";

import { ApiError } from "../api";
import { askFailure } from "./useAskQuestionState";

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
