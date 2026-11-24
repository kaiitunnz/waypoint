import type { LaunchTargetSummary } from "./types";

// Returns `targets` unchanged (same array) when the target is missing or already
// in the requested state, so callers that depend on the array skip a refetch.
export function markTargetConnected(
  targets: LaunchTargetSummary[],
  targetId: string,
  connected: boolean,
): LaunchTargetSummary[] {
  const target = targets.find((candidate) => candidate.id === targetId);
  if (!target || target.connected === connected) {
    return targets;
  }
  return targets.map((candidate) =>
    candidate.id === targetId ? { ...candidate, connected } : candidate,
  );
}

// The stored target id when the backend still lists it, else "" (the host itself).
export function resolveActiveTargetId(
  targets: LaunchTargetSummary[],
  storedId: string,
): string {
  return targets.some((target) => target.id === storedId) ? storedId : "";
}
