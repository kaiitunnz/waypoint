import { rankItems } from "./fuzzy";
import type { SessionRecord } from "./types";

// The same order as the home: Needs you, Pinned, Live, Exited.
export type SwitcherSection = "needs" | "pinned" | "live" | "exited";

export const SWITCHER_SECTIONS: SwitcherSection[] = ["needs", "pinned", "live", "exited"];

export const SWITCHER_SECTION_LABELS: Record<SwitcherSection, string> = {
  needs: "Needs you",
  pinned: "Pinned",
  live: "Live",
  exited: "Exited",
};

export interface SwitcherEntry {
  session: SessionRecord;
  section: SwitcherSection;
  // Matched character positions in the title, for highlighting.
  positions: number[];
}

function sectionOf(session: SessionRecord, askSessionIds: ReadonlySet<string>): SwitcherSection {
  if (session.status === "exited") return session.pinned_at ? "pinned" : "exited";
  if (askSessionIds.has(session.id)) return "needs";
  return session.pinned_at ? "pinned" : "live";
}

function byRecency(a: SessionRecord, b: SessionRecord): number {
  return b.last_event_at.localeCompare(a.last_event_at) || b.id.localeCompare(a.id);
}

const where = (session: SessionRecord) =>
  [session.repo_name, session.branch, session.cwd].filter(Boolean).join(" ");

// Fuzzy over title, then repo, branch and directory. A blank query keeps every
// session, newest first; a query ranks within each section, so the section order
// never changes.
export function rankSessionsForSwitcher(
  sessions: SessionRecord[],
  askSessionIds: ReadonlySet<string>,
  query: string,
): SwitcherEntry[] {
  const needle = query.trim();
  const bySection = new Map<SwitcherSection, SessionRecord[]>(SWITCHER_SECTIONS.map((s) => [s, []]));
  for (const session of [...sessions].sort(byRecency)) {
    bySection.get(sectionOf(session, askSessionIds))?.push(session);
  }
  return SWITCHER_SECTIONS.flatMap((section) =>
    rankItems(bySection.get(section) ?? [], needle, (s) => s.title, where).map(
      ({ item, positions }): SwitcherEntry => ({ session: item, section, positions }),
    ),
  );
}

// The entry one step away, wrapping at both ends; null when there is none.
export function stepSelection(index: number, step: 1 | -1, count: number): number | null {
  if (count === 0) return null;
  return (index + step + count) % count;
}
