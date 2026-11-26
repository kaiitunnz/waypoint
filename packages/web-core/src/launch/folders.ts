import { mergeRecentCwds } from "../store";
import type { ScheduledSession, SessionRecord } from "../types";

export const FOLDER_SUGGEST_DELAY_MS = 200;

// The server completes absolute and home-relative paths only.
export function isCompletable(cwd: string): boolean {
  return cwd.startsWith("/") || cwd.startsWith("~");
}

export interface FolderSuggestions {
  // The launch target the directories came from; "" or null is this host.
  targetId: string | null;
  dirs: string[];
}

// Recent folders first, then server completions that still extend the input.
// Completions from another target are dropped while the new fetch is in flight.
export function folderOptions(
  recents: string[],
  suggested: FolderSuggestions,
  cwd: string,
  targetId: string | null,
): string[] {
  const dirs =
    isCompletable(cwd) && suggested.targetId === targetId ? suggested.dirs.filter((dir) => dir.startsWith(cwd)) : [];
  return [...new Set([...recents, ...dirs])];
}

// Seeds the recent-folder lists from folders the server already knows, newest
// first, so a returning person has suggestions before launching anything.
export function seedRecentCwdsFromHistory(
  host: string,
  sessions: SessionRecord[],
  schedules: ScheduledSession[],
): void {
  const entries: { targetId: string; cwd: string; ts: number }[] = [];
  for (const session of sessions) {
    entries.push({
      targetId: session.launch_target_id ?? "",
      cwd: session.cwd,
      ts: Date.parse(session.updated_at || session.created_at) || 0,
    });
  }
  for (const schedule of schedules) {
    entries.push({
      targetId: schedule.launch_target_id ?? "",
      cwd: schedule.cwd,
      ts: Date.parse(schedule.created_at) || 0,
    });
  }
  entries.sort((a, b) => b.ts - a.ts);
  mergeRecentCwds(
    host,
    entries.map(({ targetId, cwd }) => ({ targetId, cwd })),
  );
}
