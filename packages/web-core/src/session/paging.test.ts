import { describe, expect, it } from "vitest";

import { pageOf, stepPage } from "./paging";

describe("pageOf", () => {
  const approvals = ["a", "b", "c"];

  it("reports an empty queue", () => {
    expect(pageOf([], 0)).toEqual({ count: 0, page: 0, current: null });
    expect(pageOf([], 4)).toEqual({ count: 0, page: 0, current: null });
  });

  it("selects the page at the index", () => {
    expect(pageOf(approvals, 1)).toEqual({ count: 3, page: 1, current: approvals[1] });
  });

  it("clamps an index past the end when the queue has shrunk", () => {
    expect(pageOf(approvals.slice(0, 2), 2)).toEqual({ count: 2, page: 1, current: approvals[1] });
    expect(pageOf(approvals, 99).page).toBe(2);
  });
});

describe("stepPage", () => {
  it("moves one page and stops at both ends", () => {
    expect(stepPage(1, "next", 3)).toBe(2);
    expect(stepPage(2, "next", 3)).toBe(2);
    expect(stepPage(1, "prev", 3)).toBe(0);
    expect(stepPage(0, "prev", 3)).toBe(0);
  });

  it("steps from the raw index, so a stale index past the end only moves down by one", () => {
    expect(stepPage(5, "prev", 2)).toBe(4);
    expect(stepPage(5, "next", 2)).toBe(1);
  });
});
