import { rankItems, type RankedItem } from "./fuzzy";
import { matchesQuery, parseQuery, type SearchGroup } from "./search";
import { byRecency, SESSION_SEARCH_FIELDS } from "./sessionHistory";
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

interface QueryGroup {
  // Matched as written.
  filters: SearchGroup;
  // The free text, cut from the query as typed, which is fuzzy-ranked.
  needle: string;
}

// A term is a filter when it names a field sessions have, or is a /regex/ or a quoted
// phrase on its own. A term glued to the one before is the same word (the parser
// splits a path), so it goes the same way.
function readGroup(query: string, group: SearchGroup, fields: ReadonlySet<string>): QueryGroup {
  const filters: SearchGroup = [];
  const runs: { start: number; end: number }[] = [];
  let previousEnd = -1;
  let previousFilter = false;
  for (const term of group) {
    const alone = /(^|\s)$/.test(query.slice(0, term.start)) && /^(\s|$)/.test(query.slice(term.end));
    let filter: boolean;
    if (term.field) filter = fields.has(term.field);
    else if (term.start === previousEnd) filter = previousFilter;
    else filter = alone && (term.isRegex || /^["']/.test(query.slice(term.start)));
    if (filter) filters.push(term);
    else if (runs.length > 0 && !previousFilter) runs[runs.length - 1].end = term.end;
    else runs.push({ start: term.start, end: term.end });
    previousEnd = term.end;
    previousFilter = filter;
  }
  return { filters, needle: runs.map((run) => query.slice(run.start, run.end)).join(" ") };
}

// The session query syntax, per OR group. A query with no term, such as a lone "or",
// is searched for as typed.
function readQuery(query: string, sessions: SessionRecord[]): QueryGroup[] {
  const fields = new Set([...SESSION_SEARCH_FIELDS, ...sessions.flatMap((session) => Object.keys(session))]);
  const groups = parseQuery(query).map((group) => readGroup(query, group, fields));
  return groups.length > 0 ? groups : [{ filters: [], needle: query.trim() }];
}

function rankGroup(sessions: SessionRecord[], { filters, needle }: QueryGroup): RankedItem<SessionRecord>[] {
  const matched =
    filters.length > 0 ? sessions.filter((s) => matchesQuery(s, [filters], SESSION_SEARCH_FIELDS)) : sessions;
  return rankItems(matched, needle, (s) => s.title, where);
}

// One group ranks by its free text; an OR query lists each match once, in the order given.
function rankQuery(sessions: SessionRecord[], groups: QueryGroup[]): Pick<RankedItem<SessionRecord>, "item" | "positions">[] {
  if (groups.length === 1) return rankGroup(sessions, groups[0]);
  const found = new Map<string, number[]>();
  for (const { item, positions } of groups.flatMap((group) => rankGroup(sessions, group))) {
    if (!found.has(item.id)) found.set(item.id, positions);
  }
  return sessions.flatMap((item) => {
    const positions = found.get(item.id);
    return positions ? [{ item, positions }] : [];
  });
}

// Field terms filter; the free text is fuzzy over title, then repo, branch and
// directory. A blank query keeps every session, newest first, except that pinned ones
// head Exited, newest pin first; a query ranks within each section, so the section
// order never changes.
export function rankSessionsForSwitcher(
  sessions: SessionRecord[],
  askSessionIds: ReadonlySet<string>,
  query: string,
): SwitcherEntry[] {
  const groups = readQuery(query, sessions);
  const bySection = new Map<SwitcherSection, SessionRecord[]>(SWITCHER_SECTIONS.map((s) => [s, []]));
  for (const session of [...sessions].sort(byRecency)) {
    bySection.get(sectionOf(session, askSessionIds))?.push(session);
  }
  // Unpinned ones tie, so they stay newest first.
  bySection.get("exited")?.sort(byPin);
  return SWITCHER_SECTIONS.flatMap((section) =>
    rankQuery(bySection.get(section) ?? [], groups).map(
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
