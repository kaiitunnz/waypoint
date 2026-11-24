import { describe, expect, it } from "vitest";

import { ev } from "../test-helpers";
import { approvalPager, findPendingApprovals, stepApprovalPage } from "./approvals";

const request = (seq: number, approvalId?: string) =>
  ev(seq, "approval_request", "allow?", approvalId ? { approval_id: approvalId } : {});
const resolved = (seq: number, approvalId?: string, text = "Approval response sent") =>
  ev(seq, "system_note", text, approvalId ? { approval_id: approvalId } : {});

describe("findPendingApprovals", () => {
  it("is empty without events", () => {
    expect(findPendingApprovals([])).toEqual([]);
  });

  it("queues approval requests in arrival order", () => {
    const events = [request(1, "a"), ev(2, "agent_output", "x"), request(3, "b")];
    expect(findPendingApprovals(events).map((e) => e.sequence)).toEqual([1, 3]);
  });

  it("dequeues the matching approval id, wherever it sits in the queue", () => {
    const events = [request(1, "a"), request(2, "b"), request(3, "c"), resolved(4, "b")];
    expect(findPendingApprovals(events).map((e) => e.metadata.approval_id)).toEqual(["a", "c"]);
  });

  it("dequeues the oldest request when the resolution carries no approval id", () => {
    const events = [request(1, "a"), request(2, "b"), resolved(3)];
    expect(findPendingApprovals(events).map((e) => e.metadata.approval_id)).toEqual(["b"]);
  });

  it("leaves the queue alone when the resolution id matches nothing", () => {
    const events = [request(1, "a"), resolved(2, "zzz")];
    expect(findPendingApprovals(events)).toHaveLength(1);
  });

  it("treats timeouts and invalidations as resolutions", () => {
    expect(findPendingApprovals([request(1, "a"), resolved(2, "a", "Approval timed out")])).toEqual([]);
    const invalidated = ev(2, "system_note", "gone", { approval_id: "a", method: "approval.invalidated" });
    expect(findPendingApprovals([request(1, "a"), invalidated])).toEqual([]);
  });

  it("ignores resolution wording on non-system events and unrelated notes", () => {
    const events = [request(1, "a"), ev(2, "agent_output", "Approval response sent"), resolved(3, "a", "something else")];
    expect(findPendingApprovals(events)).toHaveLength(1);
  });

  it("is a no-op for a resolution that arrives with nothing queued", () => {
    expect(findPendingApprovals([resolved(1)])).toEqual([]);
  });

  it("matches resolution wording case-insensitively", () => {
    expect(findPendingApprovals([request(1, "a"), resolved(2, "a", "APPROVAL TIMED OUT")])).toEqual([]);
  });
});

describe("approvalPager", () => {
  const approvals = [request(1, "a"), request(2, "b"), request(3, "c")];

  it("reports an empty queue", () => {
    expect(approvalPager([], 0)).toEqual({ count: 0, page: 0, current: null });
    expect(approvalPager([], 4)).toEqual({ count: 0, page: 0, current: null });
  });

  it("selects the page at the index", () => {
    expect(approvalPager(approvals, 1)).toEqual({ count: 3, page: 1, current: approvals[1] });
  });

  it("clamps an index past the end when the queue has shrunk", () => {
    expect(approvalPager(approvals.slice(0, 2), 2)).toEqual({ count: 2, page: 1, current: approvals[1] });
    expect(approvalPager(approvals, 99).page).toBe(2);
  });
});

describe("stepApprovalPage", () => {
  it("moves one page and stops at both ends", () => {
    expect(stepApprovalPage(1, "next", 3)).toBe(2);
    expect(stepApprovalPage(2, "next", 3)).toBe(2);
    expect(stepApprovalPage(1, "prev", 3)).toBe(0);
    expect(stepApprovalPage(0, "prev", 3)).toBe(0);
  });

  it("steps from the raw index, so a stale index past the end only moves down by one", () => {
    expect(stepApprovalPage(5, "prev", 2)).toBe(4);
    expect(stepApprovalPage(5, "next", 2)).toBe(1);
  });
});
