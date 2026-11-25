import type { DiffLine } from "../highlight";

export type CompactDiffRow =
  | { kind: "line"; line: DiffLine }
  // Unchanged lines left out between (or around) the changes.
  | { kind: "fold"; count: number }
  // Rows past the cap.
  | { kind: "more"; count: number };

export interface CompactDiffOptions {
  // At most this many diff lines; folds do not count. Unbounded when omitted.
  maxLines?: number;
}

// A diff reduced to its changes: file and hunk headers dropped, unchanged context
// folded to one line between changes (never around them), the whole capped to
// `maxLines` lines.
export function compactDiff(lines: readonly DiffLine[], options: CompactDiffOptions = {}): CompactDiffRow[] {
  const { maxLines = Infinity } = options;
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
  // A fold just before the cut joins what is hidden.
  const from = rows[cut - 1]?.kind === "fold" ? cut - 1 : cut;
  const hidden = rows.slice(from).reduce((sum, row) => sum + (row.kind === "fold" ? row.count : 1), 0);
  return [...rows.slice(0, from), { kind: "more", count: hidden }];
}
