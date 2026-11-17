// Ranked matching for composer completions. Tiers order how a query hit a
// name; lower is better. Smart-case: a query with an uppercase letter matches
// case-sensitively, so `$HOME` never fuzzy-hits a lowercase skill name.
export const MatchTier = {
  Exact: 0,
  Prefix: 1,
  WordStart: 2,
  Substring: 3,
  Subsequence: 4,
  Description: 5,
} as const;
export type MatchTier = (typeof MatchTier)[keyof typeof MatchTier];

export interface TextMatch {
  tier: MatchTier;
  score: number;
  positions: number[];
}

export interface RankedItem<T> {
  item: T;
  tier: MatchTier | null;
  positions: number[];
  descriptionPositions: number[];
}

export interface RankOptions {
  // Restrict to exact/prefix hits, e.g. for a query with characters outside
  // the command alphabet.
  prefixOnly?: boolean;
}

// Shorter queries are too permissive for these tiers: a single letter appears
// inside nearly every name, and a 1–2-char description word matches almost
// everything.
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

function span(start: number, length: number): number[] {
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

// The first word-start occurrence of ``needle`` past index 0, preferring one
// that spans a whole segment (`pr` in `create-pr` over `pr` in `project`).
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

// Best-scoring in-order alignment of ``needle`` in ``hay`` starting at
// ``start``: +1 per char, +10 per word-start hit, +5 per consecutive hit, and a
// small penalty for a late start so `review-pr` beats `ns:review-pr`.
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
    // Prefer a later word-start occurrence over a mid-word one, unless the
    // nearest hit is consecutive or the jump would strand the rest of the query.
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

export function matchText(
  query: string,
  text: string,
  options: RankOptions = {},
): TextMatch | null {
  if (!query) return { tier: MatchTier.Exact, score: 0, positions: [] };
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

// Rank ``items`` against ``query``. An empty query keeps every item in its
// original order; otherwise non-matches drop out and ties keep original order,
// so a backend's grouping (e.g. Waypoint built-ins first) survives.
export function rankItems<T>(
  items: ReadonlyArray<T>,
  query: string,
  name: (item: T) => string,
  description?: (item: T) => string | null | undefined,
  options: RankOptions = {},
): RankedItem<T>[] {
  if (!query) {
    return items.map((item) => ({
      item,
      tier: null,
      positions: [],
      descriptionPositions: [],
    }));
  }
  const scored: Array<RankedItem<T> & { score: number; index: number }> = [];
  items.forEach((item, index) => {
    const hit = matchText(query, name(item), options);
    if (hit) {
      scored.push({
        item,
        tier: hit.tier,
        score: hit.score,
        index,
        positions: hit.positions,
        descriptionPositions: [],
      });
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
  scored.sort(
    (a, b) =>
      (a.tier ?? 0) - (b.tier ?? 0) || b.score - a.score || a.index - b.index,
  );
  return scored.map(({ item, tier, positions, descriptionPositions }) => ({
    item,
    tier,
    positions,
    descriptionPositions,
  }));
}

// Highlight positions for a server-ranked path: the last contiguous hit
// (usually the basename), else the best in-order alignment from any start.
// Case-insensitive, matching the server's ranking.
export function highlightPositions(text: string, query: string): number[] {
  if (!query) return [];
  const needle = query.toLowerCase();
  const hay = text.toLowerCase();
  const last = hay.lastIndexOf(needle);
  if (last !== -1) return span(last, needle.length);
  return subsequence(needle, hay, text, true)?.positions ?? [];
}
