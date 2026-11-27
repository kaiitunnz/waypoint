import { describe, expect, it, vi } from "vitest";

import { ApiError, AuthError } from "../api";
import { ev } from "../test-helpers";
import { searchTurnStart } from "./useTurnStart";

const message = ev(1, "user_input", "go", { status: "running" });
const held = [ev(5, "agent_output", "x", { status: "running" })];
const page = (has_more: boolean) => ({ events: [ev(2, "agent_output", "x", { status: "running" })], has_more });

// Lets the search's promises settle.
const settle = () => new Promise((resolve) => setTimeout(resolve));

const outcome = () => ({ onFound: vi.fn(), onAuthFailure: vi.fn() });

describe("searchTurnStart", () => {
  it("reports the start it pages back to", async () => {
    const seen = outcome();
    searchTurnStart(held, 5, async () => ({ events: [message], has_more: false }), seen);
    await settle();
    expect(seen.onFound).toHaveBeenCalledWith(Date.parse(message.ts));
  });

  it("reads no further page and reports nothing once dropped", async () => {
    const seen = outcome();
    let release: () => void = () => {};
    const fetch = vi.fn(
      () =>
        new Promise<ReturnType<typeof page>>((resolve) => {
          release = () => resolve(page(true));
        }),
    );
    const drop = searchTurnStart(held, 5, fetch, seen);
    drop();
    release();
    await settle();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(seen.onFound).not.toHaveBeenCalled();
    expect(seen.onAuthFailure).not.toHaveBeenCalled();
  });

  it("passes on an auth failure and swallows any other", async () => {
    const seen = outcome();
    searchTurnStart(held, 5, () => Promise.reject(new ApiError("boom", 500)), seen);
    await settle();
    expect(seen.onAuthFailure).not.toHaveBeenCalled();
    searchTurnStart(held, 5, () => Promise.reject(new AuthError()), seen);
    await settle();
    expect(seen.onAuthFailure).toHaveBeenCalledTimes(1);
    expect(seen.onFound).not.toHaveBeenCalled();
  });
});
