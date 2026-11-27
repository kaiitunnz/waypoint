import { rankItems } from "./fuzzy";
import { byRecency } from "./sessionHistory";
import { byPin } from "./sessionSections";
import type { SessionRecord } from "./types";

const SWITCHER_SECTIONS = ["needs", "pinned", "live", "exited"] as const;

export type SwitcherSection = (typeof SWITCHER_SECTIONS)[number];

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
  if (session.status === "exited") return "exited";
  if (askSessionIds.has(session.id)) return "needs";
  return session.pinned_at ? "pinned" : "live";
}

const where = (session: SessionRecord) =>
  [session.repo_name, session.branch, session.cwd].filter(Boolean).join(" ");

// Fuzzy over title, then repo, branch and directory. A blank query keeps every
// session, newest first, except that pinned ones head Exited, newest pin first; a
// query ranks within each section, so the section order never changes.
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
  // Unpinned ones tie, so they stay newest first.
  bySection.get("exited")?.sort(byPin);
  return SWITCHER_SECTIONS.flatMap((section) =>
    rankItems(bySection.get(section) ?? [], needle, (s) => s.title, where).map(
      ({ item, positions }): SwitcherEntry => ({ session: item, section, positions }),
    ),
  );
}

// Server-paged history, already matched by the server, follows the ranked live
// entries as Exited, minus sessions the list already holds.
export function mergeSwitcherEntries(ranked: SwitcherEntry[], history: SessionRecord[]): SwitcherEntry[] {
  const known = new Set(ranked.map((entry) => entry.session.id));
  const older = history
    .filter((session) => !known.has(session.id))
    .map((session): SwitcherEntry => ({ session, section: "exited", positions: [] }));
  return [...ranked, ...older];
}

export function stepSelection(index: number, step: 1 | -1, count: number): number | null {
  if (count === 0) return null;
  return (index + step + count) % count;
}
