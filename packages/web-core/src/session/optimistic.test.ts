import { describe, expect, it } from "vitest";

import { ev } from "../test-helpers";
import { userEchoes } from "./optimistic";

describe("userEchoes", () => {
  const events = [ev(1, "user_input", "old"), ev(2, "agent_output", "a"), ev(3, "user_input", "new")];

  it("reports only the human's messages after the last one seen", () => {
    expect(userEchoes(events, 1)).toEqual({ texts: ["new"], lastId: 3 });
    expect(userEchoes(events, 3)).toEqual({ texts: [], lastId: 3 });
  });

  it("keeps the cursor when older pages arrive", () => {
    expect(userEchoes([ev(0, "user_input", "older"), ...events], 3).texts).toEqual([]);
  });

  it("starts from nothing", () => {
    expect(userEchoes([], null)).toEqual({ texts: [], lastId: null });
  });
});
