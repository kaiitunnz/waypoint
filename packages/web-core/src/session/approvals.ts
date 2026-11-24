import type { EventRecord } from "../types";

export function findPendingApprovals(events: EventRecord[]): EventRecord[] {
  // Track approvals as a queue: every approval_request enqueues, every
  // "Approval response sent" / "Approval timed out" system note dequeues
  // its matching entry (by approval_id, falling back to oldest-first).
  const queue: EventRecord[] = [];
  for (const event of events) {
    if (event.kind === "approval_request") {
      queue.push(event);
    } else if (event.kind === "system_note" && isApprovalResolutionEvent(event)) {
      const approvalId = event.metadata?.approval_id;
      if (typeof approvalId === "string") {
        const index = queue.findIndex((e) => e.metadata?.approval_id === approvalId);
        if (index !== -1) queue.splice(index, 1);
      } else {
        queue.shift();
      }
    }
  }
  return queue;
}

function isApprovalResolutionEvent(event: EventRecord): boolean {
  if (event.metadata?.method === "approval.invalidated") {
    return true;
  }
  return /(Approval response sent|Approval timed out)/i.test(event.text);
}

export interface ApprovalPager {
  count: number;
  page: number;
  current: EventRecord | null;
}

// The queue can shrink under the page index (an approval resolves while the
// pager is on a later one), so the index is clamped on every read.
export function approvalPager(approvals: EventRecord[], index: number): ApprovalPager {
  const count = approvals.length;
  const page = count > 0 ? Math.min(index, count - 1) : 0;
  return { count, page, current: approvals[page] ?? null };
}

export function stepApprovalPage(index: number, direction: "prev" | "next", count: number): number {
  return direction === "prev" ? Math.max(0, index - 1) : Math.min(count - 1, index + 1);
}
