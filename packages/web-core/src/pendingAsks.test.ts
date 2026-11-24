import { describe, expect, it } from "vitest";

import {
  applyPendingAsksSnapshot,
  parsePendingAsksSnapshot,
  pendingAsksCount,
} from "./pendingAsks";
import type { PendingAsksSnapshot } from "./types";

const snap = (epoch: string, revision: number, count = 0): PendingAsksSnapshot => ({
  epoch,
  revision,
  items: Array.from({ length: count }, (_, i) => ({ id: `question:s:${i}` }) as never),
});

describe("applyPendingAsksSnapshot", () => {
  it("takes the first snapshot", () => {
    const incoming = snap("a", 1);
    expect(applyPendingAsksSnapshot(null, incoming)).toBe(incoming);
  });

  it("takes a higher revision of the same epoch", () => {
    const incoming = snap("a", 3);
    expect(applyPendingAsksSnapshot(snap("a", 2), incoming)).toBe(incoming);
  });

  it("ignores an equal or lower revision of the same epoch", () => {
    const held = snap("a", 5, 2);
    expect(applyPendingAsksSnapshot(held, snap("a", 5))).toBe(held);
    expect(applyPendingAsksSnapshot(held, snap("a", 4))).toBe(held);
  });

  it("takes a different epoch even with a lower revision", () => {
    const incoming = snap("b", 1);
    expect(applyPendingAsksSnapshot(snap("a", 9), incoming)).toBe(incoming);
  });
});

describe("parsePendingAsksSnapshot", () => {
  it("accepts a snapshot payload", () => {
    expect(parsePendingAsksSnapshot({ epoch: "a", revision: 1, items: [] })).toEqual({
      epoch: "a",
      revision: 1,
      items: [],
    });
  });

  it("rejects payloads that are not snapshots", () => {
    expect(parsePendingAsksSnapshot(null)).toBeNull();
    expect(parsePendingAsksSnapshot({ epoch: 1, revision: 1, items: [] })).toBeNull();
    expect(parsePendingAsksSnapshot({ epoch: "a", revision: 1 })).toBeNull();
  });
});

describe("pendingAsksCount", () => {
  it("counts listed items", () => {
    expect(pendingAsksCount(null)).toBe(0);
    expect(pendingAsksCount(snap("a", 1, 3))).toBe(3);
  });
});
