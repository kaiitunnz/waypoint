// Ranked matching for composer completions; a lower tier ranks first.
// Smart-case: a query with an uppercase letter matches case-sensitively, so
// `$HOME` never hits a lowercase skill name.
const MatchTier = {
  Exact: 0,
  Prefix: 1,
  WordStart: 2,
  // Letters in order from the first character: `fe` → `frontend`.
  Abbreviation: 3,
  Substring: 4,
  Subsequence: 5,
  Description: 6,
} as const;
type MatchTier = (typeof MatchTier)[keyof typeof MatchTier];

interface TextMatch {
  tier: MatchTier;
  score: number;
  positions: number[];
}

export interface RankedItem<T> {
  item: T;
  positions: number[];
  descriptionPositions: number[];
}

export interface RankOptions {
  // Exact and prefix hits only.
  prefixOnly?: boolean;
}

// One letter appears inside most names, and a short description word matches
// almost anything.
const MIN_INNER_QUERY = 2;
const MIN_DESCRIPTION_QUERY = 3;

const SEPARATOR = /[-_:./\s]/;
// Outranks any start-offset difference, so whole-segment hits sort first.
const WHOLE_SEGMENT_BONUS = 10_000;

function isWordStart(text: string, index: number): boolean {
  if (index === 0) return true;
  const prev = text[index - 1];
  if (SEPARATOR.test(prev)) return true;
  const ch = text[index];
  return prev >= "a" && prev <= "z" && ch >= "A" && ch <= "Z";
}

export function span(start: number, length: number): number[] {
  return Array.from({ length }, (_, offset) => start + offset);
}

function fold(query: string, text: string): [string, string] {
  return query === query.toLowerCase()
    ? [query, text.toLowerCase()]
    : [query, text];
}

function isSubsequenceFrom(
  needle: string,
  from: number,
  hay: string,
  after: number,
): boolean {
  let at = after;
  for (let qi = from; qi < needle.length; qi++) {
    at = hay.indexOf(needle[qi], at + 1);
    if (at === -1) return false;
  }
  return true;
}

function isSegmentEnd(text: string, index: number): boolean {
  return index === text.length || SEPARATOR.test(text[index]);
}

// Prefers a hit spanning a whole segment: `pr` in `create-pr` over `project`.
function wordStartIndex(
  needle: string,
  hay: string,
  text: string,
): { index: number; whole: boolean } | null {
  let first: number | null = null;
  for (
    let index = hay.indexOf(needle, 1);
    index !== -1;
    index = hay.indexOf(needle, index + 1)
  ) {
    if (!isWordStart(text, index)) continue;
    if (isSegmentEnd(text, index + needle.length)) return { index, whole: true };
    first ??= index;
  }
  return first === null ? null : { index: first, whole: false };
}

// A late start costs a little, so `review-pr` beats `ns:review-pr`.
function alignFrom(
  needle: string,
  hay: string,
  text: string,
  start: number,
): TextMatch | null {
  const positions = [start];
  let score = 1 + (isWordStart(text, start) ? 10 : 0) - start / 10;
  let prev = start;
  for (let qi = 1; qi < needle.length; qi++) {
    const ch = needle[qi];
    const next = hay.indexOf(ch, prev + 1);
    if (next === -1) return null;
    // Jump to a later word-start hit unless the nearest one is consecutive or
    // the jump would strand the rest of the query.
    let pick = next;
    if (next !== prev + 1 && !isWordStart(text, next)) {
      for (let j = hay.indexOf(ch, next + 1); j !== -1; j = hay.indexOf(ch, j + 1)) {
        if (isWordStart(text, j)) {
          if (isSubsequenceFrom(needle, qi + 1, hay, j)) pick = j;
          break;
        }
      }
    }
    score += 1;
    if (isWordStart(text, pick)) score += 10;
    if (pick === prev + 1) score += 5;
    positions.push(pick);
    prev = pick;
  }
  return { tier: MatchTier.Subsequence, score, positions };
}

function subsequence(
  needle: string,
  hay: string,
  text: string,
  anyStart: boolean,
): TextMatch | null {
  let best: TextMatch | null = null;
  for (
    let start = hay.indexOf(needle[0]);
    start !== -1;
    start = hay.indexOf(needle[0], start + 1)
  ) {
    if (!anyStart && !isWordStart(text, start)) continue;
    const match = alignFrom(needle, hay, text, start);
    if (match && (!best || match.score > best.score)) best = match;
  }
  return best;
}

// Mirrors _segment_score in backend workspace_preview.py; keep the tiers in sync.
function matchText(
  query: string,
  text: string,
  options: RankOptions = {},
): TextMatch | null {
  const [needle, hay] = fold(query, text);
  if (hay === needle) {
    return { tier: MatchTier.Exact, score: 0, positions: span(0, needle.length) };
  }
  if (hay.startsWith(needle)) {
    return { tier: MatchTier.Prefix, score: 0, positions: span(0, needle.length) };
  }
  if (options.prefixOnly) return null;
  const wordStart = wordStartIndex(needle, hay, text);
  if (wordStart) {
    return {
      tier: MatchTier.WordStart,
      score: (wordStart.whole ? WHOLE_SEGMENT_BONUS : 0) - wordStart.index,
      positions: span(wordStart.index, needle.length),
    };
  }
  if (needle.length < MIN_INNER_QUERY) return null;
  if (hay[0] === needle[0]) {
    const abbreviation = alignFrom(needle, hay, text, 0);
    if (abbreviation) return { ...abbreviation, tier: MatchTier.Abbreviation };
  }
  const substring = hay.indexOf(needle);
  if (substring !== -1) {
    return {
      tier: MatchTier.Substring,
      score: -substring,
      positions: span(substring, needle.length),
    };
  }
  return subsequence(needle, hay, text, false);
}

function matchDescription(query: string, description: string): number[] | null {
  if (query.length < MIN_DESCRIPTION_QUERY) return null;
  const [needle, hay] = fold(query, description);
  const index = hay.startsWith(needle)
    ? 0
    : wordStartIndex(needle, hay, description)?.index;
  return index === undefined ? null : span(index, needle.length);
}

// An empty query keeps every item. Ties keep input order, so the caller's
// grouping survives.
export function rankItems<T>(
  items: ReadonlyArray<T>,
  query: string,
  name: (item: T) => string,
  description?: (item: T) => string | null | undefined,
  options: RankOptions = {},
): RankedItem<T>[] {
  if (!query) {
    return items.map((item) => ({ item, positions: [], descriptionPositions: [] }));
  }
  const scored: Array<RankedItem<T> & { tier: MatchTier; score: number; index: number }> =
    [];
  items.forEach((item, index) => {
    const hit = matchText(query, name(item), options);
    if (hit) {
      scored.push({ item, ...hit, index, descriptionPositions: [] });
      return;
    }
    if (options.prefixOnly) return;
    const text = description?.(item);
    const descriptionHit = text ? matchDescription(query, text) : null;
    if (descriptionHit) {
      scored.push({
        item,
        tier: MatchTier.Description,
        score: 0,
        index,
        positions: [],
        descriptionPositions: descriptionHit,
      });
    }
  });
  scored.sort((a, b) => a.tier - b.tier || b.score - a.score || a.index - b.index);
  return scored.map(({ item, positions, descriptionPositions }) => ({
    item,
    positions,
    descriptionPositions,
  }));
}

// Highlight positions for a rank_files hit, or null when the path doesn't
// match. Mirrors _segment_path_score for queries with `/`, binding each folder
// segment to its earliest match.
export function matchPath(path: string, query: string): number[] | null {
  const needle = query.toLowerCase();
  if (!needle.includes("/")) {
    const hay = path.toLowerCase();
    const last = hay.lastIndexOf(needle);
    if (last !== -1) return span(last, needle.length);
    return subsequence(needle, hay, path, true)?.positions ?? null;
  }
  const parts = needle.split("/").filter(Boolean);
  if (parts.length === 0) return null;
  const segments: Array<{ text: string; start: number }> = [];
  let offset = 0;
  for (const text of path.split("/")) {
    segments.push({ text, start: offset });
    offset += text.length + 1;
  }
  const name = segments[segments.length - 1];
  const folders = segments.slice(0, -1);
  const positions: number[] = [];
  let next = 0;
  for (const part of parts.slice(0, -1)) {
    let hit: TextMatch | null = null;
    for (; next < folders.length && !hit; next++) {
      hit = matchText(part, folders[next].text);
      if (hit) positions.push(...hit.positions.map((i) => i + folders[next].start));
    }
    if (!hit) return null;
  }
  const hit = matchText(parts[parts.length - 1], name.text);
  if (!hit) return null;
  positions.push(...hit.positions.map((i) => i + name.start));
  return positions;
}

// A matched label as runs of hit and plain text, for highlighting.
export function highlightRuns(text: string, positions: ReadonlyArray<number>): { hit: boolean; value: string }[] {
  const hits = new Set(positions);
  const runs: { hit: boolean; value: string }[] = [];
  for (let index = 0; index < text.length; index++) {
    const hit = hits.has(index);
    const last = runs[runs.length - 1];
    if (last && last.hit === hit) {
      last.value += text[index];
    } else {
      runs.push({ hit, value: text[index] });
    }
  }
  return runs;
}
