import type { DiffLine } from "../highlight";

export type CompactDiffRow =
  | { kind: "line"; line: DiffLine }
  // Unchanged lines left out between two changes; null when the diff does not say how many.
  // `lines` are those lines where the diff carries them all, as it does within one hunk.
  | { kind: "fold"; count: number | null; lines?: DiffLine[] };

// A diff reduced to its changes: file and hunk headers dropped, unchanged context
// before the first and after the last change dropped, the unchanged lines between
// changes folded to one row, and the whole capped to `maxLines` diff lines (folds
// do not count).
export function compactDiff(lines: readonly DiffLine[], maxLines = Infinity): CompactDiffRow[] {
  const rows: CompactDiffRow[] = [];
  let pending: DiffLine[] = [];
  // Where the next line sits in the new file (null after a hunk header), where the last
  // change ended, and whether a hunk header has come since: lines between hunks are not in the diff.
  let at: number | null = null;
  let resume: number | null = null;
  let jumped = false;
  for (const line of lines) {
    if (line.kind === "meta") continue;
    if (line.kind === "hunk") {
      jumped = true;
      at = null;
      continue;
    }
    const here = line.kind === "add" ? line.newNo : at;
    if (line.newNo !== null) at = line.newNo + 1;
    if (line.kind !== "add" && line.kind !== "del") {
      pending.push(line);
      continue;
    }
    if (rows.length > 0) {
      if (jumped) {
        const count = resume !== null && here !== null ? here - resume : null;
        if (count !== 0) rows.push({ kind: "fold", count });
      } else if (pending.length === 1) rows.push({ kind: "line", line: pending[0] });
      else if (pending.length > 1) rows.push({ kind: "fold", count: pending.length, lines: pending });
    }
    pending = [];
    jumped = false;
    rows.push({ kind: "line", line });
    resume = at;
  }

  let shown = 0;
  const cut = rows.findIndex((row) => row.kind === "line" && ++shown > maxLines);
  if (cut === -1) return rows;
  // A fold just before the cut goes with what is hidden.
  return rows.slice(0, rows[cut - 1]?.kind === "fold" ? cut - 1 : cut);
}

// How many diff lines these rows can show: the lines in them and those their folds carry.
export function reachableLines(rows: readonly CompactDiffRow[]): number {
  return rows.reduce((count, row) => count + (row.kind === "line" ? 1 : (row.lines?.length ?? 0)), 0);
}

// Every line of a diff but its file and hunk headers, with nothing folded or capped.
export function fullDiff(lines: readonly DiffLine[]): CompactDiffRow[] {
  return lines.flatMap((line): CompactDiffRow[] => (line.kind === "meta" || line.kind === "hunk" ? [] : [{ kind: "line", line }]));
}
