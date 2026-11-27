import { describe, expect, it } from "vitest";

import { planTurnSearch } from "./useTurnStart";

// A running turn that began before the loaded events.
const turn = { hasOlder: true, bounded: false, running: true };
const paused = { ...turn, running: false };

describe("planTurnSearch", () => {
  it("searches only while the turn runs and no search has answered", () => {
    expect(planTurnSearch(turn, false)).toEqual({ drop: false, search: true });
    expect(planTurnSearch(paused, false)).toEqual({ drop: false, search: false });
  });

  it("keeps an answer through an approval prompt and the turn's resumption", () => {
    expect(planTurnSearch(paused, true)).toEqual({ drop: false, search: false });
    expect(planTurnSearch(turn, true)).toEqual({ drop: false, search: false });
  });

  it("drops the answer once the turn before has its end loaded, or nothing is older", () => {
    expect(planTurnSearch({ ...turn, bounded: true }, true)).toEqual({ drop: true, search: false });
    expect(planTurnSearch({ ...paused, hasOlder: false }, true)).toEqual({ drop: true, search: false });
  });
});
