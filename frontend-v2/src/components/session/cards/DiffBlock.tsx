"use client";

import { useEffect, useMemo, useState } from "react";

import type { EventDiffPreview, EventDiffPreviewFile } from "@waypoint/web-core/events";
import { buildPlainDiffLines, DIFF_MARKER, highlightDiffToLines, type DiffLine } from "@waypoint/web-core/highlight";
import { compactDiff } from "@waypoint/web-core/session/diffCompact";
import { displayChangeType, diffFilePathLabel } from "@waypoint/web-core/session/diffPreview";

import { cx } from "@/lib/cx";

import styles from "./DiffBlock.module.css";
import highlight from "./Highlight.module.css";
import { Tokens } from "./Tokens";

const COMPACT_ROWS = 6;

function FileDiff({ file, showHeader }: { file: EventDiffPreviewFile; showHeader: boolean }) {
  const plain = useMemo(() => buildPlainDiffLines(file.diff), [file.diff]);
  const [highlighted, setHighlighted] = useState<{ diff: string; lines: DiffLine[] } | null>(null);
  const [all, setAll] = useState(false);
  const lines = highlighted?.diff === file.diff ? highlighted.lines : plain;

  useEffect(() => {
    if (!file.diff) return;
    let cancelled = false;
    void highlightDiffToLines(file.diff, file.path).then((result) => {
      if (!cancelled && result) setHighlighted({ diff: file.diff, lines: result });
    });
    return () => {
      cancelled = true;
    };
  }, [file.diff, file.path]);

  const rows = useMemo(() => compactDiff(lines, { maxRows: all ? Infinity : COMPACT_ROWS }), [lines, all]);
  const change = displayChangeType(file);

  return (
    <div className={styles.file}>
      {showHeader ? (
        <div className={styles.head}>
          <span className={styles.change}>{change.label}</span>
          <span className={styles.path}>{diffFilePathLabel(file)}</span>
          <span className={styles.stats}>
            <span className={styles.add}>+{file.additions}</span> <span className={styles.del}>−{file.deletions}</span>
          </span>
        </div>
      ) : null}
      {file.unavailableReason ? (
        <p className={styles.note}>{file.unavailableReason}</p>
      ) : file.diff ? (
        <div className={cx(styles.code, highlight.hl)} role="group" aria-label={`Diff for ${file.path}`}>
          {rows.map((row, index) => {
            if (row.kind === "fold") {
              return (
                <div key={index} className={styles.fold}>
                  {row.count} unchanged lines
                </div>
              );
            }
            if (row.kind === "more") {
              return (
                <button key={index} type="button" className={cx(styles.fold, styles.more)} onClick={() => setAll(true)}>
                  {row.count} more lines
                </button>
              );
            }
            const { line } = row;
            return (
              <div key={index} className={cx(styles.line, line.kind === "add" && styles.addLine, line.kind === "del" && styles.delLine)}>
                <span className={styles.no}>{line.kind === "del" ? line.oldNo : line.newNo}</span>
                <span className={cx(styles.sign, line.kind === "add" && styles.addSign, line.kind === "del" && styles.delSign)}>
                  {DIFF_MARKER[line.kind]}
                </span>
                <span>
                  <Tokens tokens={line.tokens} />
                </span>
              </div>
            );
          })}
        </div>
      ) : (
        <p className={styles.note}>No diff content available.</p>
      )}
      {file.truncated ? <p className={styles.note}>Diff truncated.</p> : null}
    </div>
  );
}

// Edited files as compact diffs: the changes with a line of context, the rest folded.
export function DiffBlock({ preview, headers }: { preview: EventDiffPreview; headers?: boolean }) {
  return (
    <>
      {preview.files.map((file, index) => (
        <FileDiff key={`${file.path}-${index}`} file={file} showHeader={headers ?? preview.files.length > 1} />
      ))}
    </>
  );
}
