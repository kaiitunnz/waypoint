import type { PendingAsksSnapshot } from "./types";

// A pending_asks frame (or a REST snapshot) replaces the held one when the
// backend restarted (a different epoch) or the item list changed (a higher
// revision); anything else is stale or a repeat.
export function applyPendingAsksSnapshot(
  held: PendingAsksSnapshot | null,
  incoming: PendingAsksSnapshot,
): PendingAsksSnapshot {
  if (held === null || held.epoch !== incoming.epoch || incoming.revision > held.revision) {
    return incoming;
  }
  return held;
}

// Validates a `pending_asks` frame payload; null when it is not a snapshot.
export function parsePendingAsksSnapshot(payload: unknown): PendingAsksSnapshot | null {
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const { epoch, revision, items } = payload as Record<string, unknown>;
  if (typeof epoch !== "string" || typeof revision !== "number" || !Array.isArray(items)) {
    return null;
  }
  return { epoch, revision, items: items as PendingAsksSnapshot["items"] };
}

export function pendingAsksCount(snapshot: PendingAsksSnapshot | null): number {
  return snapshot?.items.length ?? 0;
}
