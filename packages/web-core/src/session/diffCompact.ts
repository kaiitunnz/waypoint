import type { DiffLine } from "../highlight";

export type CompactDiffRow =
  | { kind: "line"; line: DiffLine }
  // Unchanged lines left out between two changes.
  | { kind: "fold"; count: number };

// A diff reduced to its changes: file and hunk headers dropped, unchanged context
// before the first and after the last change dropped, the unchanged lines between
// changes folded to one row, and the whole capped to `maxLines` diff lines (folds
// do not count).
export function compactDiff(lines: readonly DiffLine[], maxLines = Infinity): CompactDiffRow[] {
  const rows: CompactDiffRow[] = [];
  let pending: DiffLine[] = [];
  for (const line of lines) {
    if (line.kind === "meta" || line.kind === "hunk") continue;
    if (line.kind !== "add" && line.kind !== "del") {
      pending.push(line);
      continue;
    }
    // Unchanged lines before the first change or after the last are not shown.
    if (rows.length > 0 && pending.length === 1) rows.push({ kind: "line", line: pending[0] });
    else if (rows.length > 0 && pending.length > 1) rows.push({ kind: "fold", count: pending.length });
    pending = [];
    rows.push({ kind: "line", line });
  }

  let shown = 0;
  const cut = rows.findIndex((row) => row.kind === "line" && ++shown > maxLines);
  if (cut === -1) return rows;
  // A fold just before the cut goes with what is hidden.
  return rows.slice(0, rows[cut - 1]?.kind === "fold" ? cut - 1 : cut);
}

// Every line of a diff but its file and hunk headers, with nothing folded or capped.
export function fullDiff(lines: readonly DiffLine[]): CompactDiffRow[] {
  return lines.flatMap((line): CompactDiffRow[] => (line.kind === "meta" || line.kind === "hunk" ? [] : [{ kind: "line", line }]));
}
