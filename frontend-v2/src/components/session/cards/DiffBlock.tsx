"use client";

import { useMemo, useState } from "react";

import type { EventDiffPreview, EventDiffPreviewFile } from "@waypoint/web-core/events";
import { DIFF_MARKER, type DiffLine } from "@waypoint/web-core/highlight";
import { useHighlightedDiff } from "@waypoint/web-core/hooks/useHighlighted";
import { compactDiff, fullDiff, reachableLines } from "@waypoint/web-core/session/diffCompact";
import { displayChangeType, diffFilePathLabel } from "@waypoint/web-core/session/diffPreview";
import { relativeToCwd, splitPath } from "@waypoint/web-core/workspacePaths";

import { cx } from "@/lib/cx";

import { useSessionContext } from "../SessionContext";
import styles from "./DiffBlock.module.css";
import highlight from "./Highlight.module.css";
import { DiffStat } from "./DiffStat";
import { Disclosure } from "./Disclosure";
import { PathLabel } from "./PathLabel";
import { Tokens } from "./Tokens";

const COMPACT_LINES = 6;

function Line({ line }: { line: DiffLine }) {
  return (
    <div className={cx(styles.line, line.kind === "add" && styles.addLine, line.kind === "del" && styles.delLine)}>
      <span className={styles.no}>{line.kind === "del" ? line.oldNo : line.newNo}</span>
      <span className={cx(line.kind === "add" && styles.addSign, line.kind === "del" && styles.delSign)}>{DIFF_MARKER[line.kind]}</span>
      <span>
        <Tokens tokens={line.tokens} />
      </span>
    </div>
  );
}

// Unchanged lines between two changes that the diff carries.
function ContextFold({ lines }: { lines: DiffLine[] }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      {open ? lines.map((line, index) => <Line key={index} line={line} />) : null}
      <div className={styles.context}>
        <Disclosure open={open} total={lines.length} noun="unchanged lines" onToggle={() => setOpen(!open)} />
      </div>
    </>
  );
}

function FileDiff({ file, showHeader }: { file: EventDiffPreviewFile; showHeader: boolean }) {
  const lines = useHighlightedDiff(file.diff, file.path);
  const [open, setOpen] = useState(false);
  const compact = useMemo(() => compactDiff(lines, COMPACT_LINES), [lines]);
  const full = useMemo(() => fullDiff(lines), [lines]);
  const folded = reachableLines(compact) < full.length;
  const rows = open ? full : compact;
  const change = displayChangeType(file);
  const { cwd } = useSessionContext();
  const shown = diffFilePathLabel(file, cwd);
  const from = file.oldPath && file.oldPath !== file.path ? file.oldPath : null;
  const label = (path: string) => <PathLabel {...splitPath(relativeToCwd(path, cwd))} full={path} className={styles.path} />;

  return (
    <div className={styles.file}>
      {showHeader ? (
        <div className={styles.head}>
          <span className={styles.change}>{change.label}</span>
          {from ? (
            <span className={styles.moved}>
              {label(from)}
              <span className={styles.to}>
                <span role="img" aria-label="renamed to">
                  →
                </span>
                {label(file.path)}
              </span>
            </span>
          ) : (
            label(file.path)
          )}
          <span className={styles.stats}>
            <DiffStat additions={file.additions} deletions={file.deletions} />
          </span>
        </div>
      ) : null}
      {file.unavailableReason ? (
        <p className={styles.note}>{file.unavailableReason}</p>
      ) : full.length > 0 ? (
        <div className={cx(styles.code, highlight.hl)} role="group" aria-label={`Diff for ${shown}`}>
          {rows.map((row, index) => {
            if (row.kind === "line") return <Line key={index} line={row.line} />;
            if (row.lines) return <ContextFold key={index} lines={row.lines} />;
            return (
              <div key={index} className={styles.fold}>
                {row.count === null ? "Unchanged lines" : `${row.count} unchanged ${row.count === 1 ? "line" : "lines"}`}
              </div>
            );
          })}
        </div>
      ) : (
        <p className={styles.note}>{file.diff ? "No line changes." : "No diff content available."}</p>
      )}
      {folded ? (
        <div className={styles.foot}>
          <Disclosure open={open} total={full.length} onToggle={() => setOpen(!open)} />
        </div>
      ) : null}
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
      {preview.truncated && !preview.files.some((file) => file.truncated) ? <p className={styles.note}>Diff truncated.</p> : null}
    </>
  );
}
