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
  // Unchanged lines kept on each side of a change.
  context?: number;
}

// A diff reduced to its changes: file and hunk headers dropped, unchanged context
// folded to one line between changes (never around them), the whole capped to
// `maxLines` lines.
export function compactDiff(lines: readonly DiffLine[], options: CompactDiffOptions = {}): CompactDiffRow[] {
  const { maxLines = Infinity, context = 0 } = options;
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
  const settle = (between: boolean) => {
    // Unchanged lines before the first change or after the last are not shown.
    if (between && pending.length === 1) rows.push({ kind: "line", line: pending[0] });
    else if (between && pending.length > 1) rows.push({ kind: "fold", count: pending.length });
    pending = [];
  };
  body.forEach((line, index) => {
    if (keep[index]) {
      settle(rows.length > 0);
      rows.push({ kind: "line", line });
    } else {
      pending.push(line);
    }
  });
  settle(false);

  let shown = 0;
  const cut = rows.findIndex((row) => row.kind === "line" && ++shown > maxLines);
  if (cut === -1) return rows;
  // A fold just before the cut joins what is hidden.
  const from = rows[cut - 1]?.kind === "fold" ? cut - 1 : cut;
  const hidden = rows.slice(from).reduce((sum, row) => sum + (row.kind === "fold" ? row.count : 1), 0);
  return [...rows.slice(0, from), { kind: "more", count: hidden }];
}
