import type { DiffLine } from "../highlight";

export type CompactDiffRow =
  | { kind: "line"; line: DiffLine }
  // Unchanged lines left out between (or around) the changes.
  | { kind: "fold"; count: number }
  // Rows past the cap.
  | { kind: "more"; count: number };

export interface CompactDiffOptions {
  // At most this many rows (lines and folds). Unbounded when omitted.
  maxRows?: number;
  // Unchanged lines kept on each side of a change.
  context?: number;
}

// A diff reduced to its changes: file and hunk headers dropped, unchanged context
// folded to one line, the whole capped to `maxRows`.
export function compactDiff(lines: readonly DiffLine[], options: CompactDiffOptions = {}): CompactDiffRow[] {
  const { maxRows = Infinity, context = 1 } = options;
  const body = lines.filter((line) => line.kind !== "meta" && line.kind !== "hunk");
  const changed = body.map((line) => line.kind === "add" || line.kind === "del");
  const keep = body.map(() => false);
  changed.forEach((isChange, index) => {
    if (!isChange) return;
    for (let at = Math.max(0, index - context); at <= Math.min(body.length - 1, index + context); at += 1) {
      keep[at] = true;
    }
  });

  const rows: CompactDiffRow[] = [];
  let pending: DiffLine[] = [];
  const settle = () => {
    // A lone unchanged line reads better than a fold that hides one line.
    if (pending.length === 1) rows.push({ kind: "line", line: pending[0] });
    else if (pending.length > 1) rows.push({ kind: "fold", count: pending.length });
    pending = [];
  };
  body.forEach((line, index) => {
    if (keep[index]) {
      settle();
      rows.push({ kind: "line", line });
    } else {
      pending.push(line);
    }
  });
  settle();

  if (rows.length <= maxRows) return rows;
  const shown = rows.slice(0, maxRows);
  const hidden = rows.slice(maxRows).reduce((sum, row) => sum + (row.kind === "fold" ? row.count : 1), 0);
  return [...shown, { kind: "more", count: hidden }];
}
