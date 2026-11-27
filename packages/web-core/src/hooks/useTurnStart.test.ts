import { describe, expect, it } from "vitest";

import { seeksTurnStart } from "./useTurnStart";

// A running turn that began before the loaded events.
const turn = { hasOlder: true, bounded: false, running: true };
const paused = { ...turn, running: false };

describe("seeksTurnStart", () => {
  it("starts for a running turn only", () => {
    expect(seeksTurnStart(false, turn)).toBe(true);
    expect(seeksTurnStart(false, paused)).toBe(false);
  });

  it("holds through an approval prompt and the turn's resumption", () => {
    expect(seeksTurnStart(true, paused)).toBe(true);
    expect(seeksTurnStart(true, turn)).toBe(true);
  });

  it("ends once the turn before has its end loaded, or nothing is older", () => {
    expect(seeksTurnStart(true, { ...turn, bounded: true })).toBe(false);
    expect(seeksTurnStart(true, { ...paused, hasOlder: false })).toBe(false);
  });
});
