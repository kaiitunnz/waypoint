"use client";

import { useMemo, useState } from "react";

import type { EventDiffPreview, EventDiffPreviewFile } from "@waypoint/web-core/events";
import { DIFF_MARKER } from "@waypoint/web-core/highlight";
import { useHighlightedDiff } from "@waypoint/web-core/hooks/useHighlighted";
import { compactDiff } from "@waypoint/web-core/session/diffCompact";
import { displayChangeType, diffFilePathLabel } from "@waypoint/web-core/session/diffPreview";
import { splitPath } from "@waypoint/web-core/workspacePaths";

import { cx } from "@/lib/cx";

import { useSessionContext } from "../SessionContext";
import styles from "./DiffBlock.module.css";
import highlight from "./Highlight.module.css";
import { DiffStat } from "./DiffStat";
import { PathLabel } from "./PathLabel";
import { Tokens } from "./Tokens";

const COMPACT_LINES = 6;

function FileDiff({ file, showHeader }: { file: EventDiffPreviewFile; showHeader: boolean }) {
  const lines = useHighlightedDiff(file.diff, file.path);
  const [all, setAll] = useState(false);
  const rows = useMemo(() => compactDiff(lines, all ? Infinity : COMPACT_LINES), [lines, all]);
  const change = displayChangeType(file);
  const { cwd } = useSessionContext();
  const shown = diffFilePathLabel(file, cwd);
  const moved = Boolean(file.oldPath) && file.oldPath !== file.path;
  const split = splitPath(shown);

  return (
    <div className={styles.file}>
      {showHeader ? (
        <div className={styles.head}>
          <span className={styles.change}>{change.label}</span>
          {moved ? (
            <span className={styles.path} title={diffFilePathLabel(file)}>
              {shown}
            </span>
          ) : (
            <PathLabel dir={split.dir} base={split.base} className={styles.path} title={diffFilePathLabel(file)} />
          )}
          <span className={styles.stats}>
            <DiffStat additions={file.additions} deletions={file.deletions} />
          </span>
        </div>
      ) : null}
      {file.unavailableReason ? (
        <p className={styles.note}>{file.unavailableReason}</p>
      ) : file.diff ? (
        <div className={cx(styles.code, highlight.hl)} role="group" aria-label={`Diff for ${shown}`}>
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
                <span className={cx(line.kind === "add" && styles.addSign, line.kind === "del" && styles.delSign)}>
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

export function DiffBlock({ preview, headers }: { preview: EventDiffPreview; headers?: boolean }) {
  return (
    <>
      {preview.files.map((file, index) => (
        <FileDiff key={`${file.path}-${index}`} file={file} showHeader={headers ?? preview.files.length > 1} />
      ))}
    </>
  );
}
