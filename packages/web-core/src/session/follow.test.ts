import { describe, expect, it } from "vitest";

import { FOLLOWING, moved, scrolled, sent, settled, toggled, type Follow } from "./follow";

const away: Follow = { following: false, hold: null };

describe("a toggled fold", () => {
  it("stops following at once, remembering that the person was at the end", () => {
    expect(toggled(FOLLOWING, 500)).toEqual({ following: false, hold: { scrollTop: 500, resumeOnSend: true } });
    expect(toggled(away, 500).hold?.resumeOnSend).toBe(false);
    expect(toggled(toggled(FOLLOWING, 500), 500).hold?.resumeOnSend).toBe(true);
  });

  it("stays held once opened, even when nothing is below", () => {
    expect(settled(true, false, 500, 0).following).toBe(false);
  });

  it("follows again when closing leaves the view at the end, and not otherwise", () => {
    expect(settled(true, true, 500, 0)).toEqual(FOLLOWING);
    expect(settled(true, true, 500, 40).following).toBe(false);
  });

  it("ignores a scroll that leaves the view where it was held", () => {
    const held = settled(true, false, 500, 40);
    expect(scrolled(held, 500, 40)).toBe(held);
  });

  it("gives way to a scroll of the person's own", () => {
    const held = settled(true, false, 500, 400);
    expect(scrolled(held, 880, 20)).toEqual({ following: true, hold: null });
    expect(scrolled(held, 100, 800)).toEqual({ following: false, hold: null });
  });

  it("keeps holding through the transcript's own moves", () => {
    const held = moved(settled(true, false, 500, 400), 640);
    expect(scrolled(held, 640, 100)).toBe(held);
    expect(moved(FOLLOWING, 640)).toBe(FOLLOWING);
  });

  it("brings a person who was at the end to what they send, and no one else", () => {
    expect(sent(settled(true, false, 500, 400))).toEqual(FOLLOWING);
    expect(sent(settled(false, false, 500, 400)).following).toBe(false);
    expect(sent(scrolled(settled(true, false, 500, 400), 100, 800)).following).toBe(false);
  });
});

describe("scrolled", () => {
  it("follows near the end", () => {
    expect(scrolled(away, 900, 159).following).toBe(true);
    expect(scrolled(FOLLOWING, 600, 160).following).toBe(false);
  });
});
