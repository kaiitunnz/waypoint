import { describe, expect, it } from "vitest";

import { ApiError, AuthError, CWD_NOT_FOUND_DETAIL, SSH_MASTER_REQUIRED_DETAIL } from "../api";
import { classifyLaunchFailure } from "./failure";

describe("classifyLaunchFailure", () => {
  it("routes the server's detail tokens and keeps the message on every kind", () => {
    expect(classifyLaunchFailure(new AuthError(), "x").kind).toBe("auth");
    expect(classifyLaunchFailure(new ApiError(CWD_NOT_FOUND_DETAIL, 400), "x")).toEqual({ kind: "cwd", message: CWD_NOT_FOUND_DETAIL });
    expect(classifyLaunchFailure(new ApiError(SSH_MASTER_REQUIRED_DETAIL, 409), "x")).toEqual({
      kind: "ssh",
      message: SSH_MASTER_REQUIRED_DETAIL,
    });
    expect(classifyLaunchFailure(new ApiError("model not found", 400), "x")).toEqual({ kind: "detail", message: "model not found" });
    expect(classifyLaunchFailure("boom", "failed")).toEqual({ kind: "detail", message: "failed" });
  });
});
