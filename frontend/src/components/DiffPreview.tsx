import { Fragment, useEffect, useMemo, useState } from "react";

import type { EventDiffPreview, EventDiffPreviewFile } from "@/lib/events";
import {
  buildPlainDiffLines,
  DIFF_MARKER,
  highlightDiffToLines,
  type DiffLine,
} from "@/lib/highlight";

const PHASE_LABEL: Record<EventDiffPreview["phase"], string> = {
  proposed: "Proposed changes",
  applied: "Applied changes",
  aggregate: "Turn changes",
};

export function DiffPreview({
  preview,
  onOpenWorkspaceFile,
}: {
  preview: EventDiffPreview;
  onOpenWorkspaceFile?: (path: string) => void;
}) {
  return (
    <div className="diff-preview">
      <div className="diff-preview-header">
        <span className="diff-preview-title">{PHASE_LABEL[preview.phase]}</span>
        <span className="diff-preview-stat add">+{preview.totalAdditions}</span>
        <span className="diff-preview-stat del">-{preview.totalDeletions}</span>
        {preview.truncated ? <span className="diff-preview-note">truncated</span> : null}
      </div>
      <div className="diff-preview-files">
        {preview.files.map((file, index) => (
          <DiffPreviewFileView
            file={file}
            key={`${file.path}-${index}`}
            onOpenWorkspaceFile={onOpenWorkspaceFile}
          />
        ))}
      </div>
    </div>
  );
}

function DiffPreviewFileView({
  file,
  onOpenWorkspaceFile,
}: {
  file: EventDiffPreviewFile;
  onOpenWorkspaceFile?: (path: string) => void;
}) {
  const changeType = displayChangeType(file);
  const pathLabel =
    file.oldPath && file.oldPath !== file.path
      ? `${file.oldPath} -> ${file.path}`
      : file.path;
  return (
    <section className="diff-preview-file">
      <div className="diff-file-header">
        <span className={`diff-change-type ${changeType.className}`}>
          {changeType.label}
        </span>
        {onOpenWorkspaceFile ? (
          <button
            type="button"
            className="diff-file-path diff-file-path-btn"
            title={`Open ${file.path}`}
            onClick={() => onOpenWorkspaceFile(file.path)}
          >
            {pathLabel}
          </button>
        ) : (
          <span className="diff-file-path" title={file.path}>
            {pathLabel}
          </span>
        )}
        <span className="diff-file-stats">
          +{file.additions} -{file.deletions}
        </span>
      </div>
      {file.unavailableReason ? (
        <p className="diff-unavailable">{file.unavailableReason}</p>
      ) : file.diff ? (
        <DiffCode diff={file.diff} path={file.path} />
      ) : (
        <p className="diff-unavailable">No diff content available.</p>
      )}
      {file.truncated ? <p className="diff-unavailable">Diff truncated.</p> : null}
    </section>
  );
}

function displayChangeType(file: EventDiffPreviewFile): {
  className: EventDiffPreviewFile["changeType"];
  label: string;
} {
  if (file.changeType !== "unknown") {
    return { className: file.changeType, label: file.changeType };
  }
  const inferred = inferChangeType(file.diff);
  if (inferred !== "unknown") {
    return { className: inferred, label: inferred };
  }
  return { className: "update", label: "edit" };
}

function inferChangeType(diff: string): EventDiffPreviewFile["changeType"] {
  const lines = diff.split("\n");
  const oldPath = lines.find((line) => line.startsWith("--- "))?.slice(4).trim();
  const newPath = lines.find((line) => line.startsWith("+++ "))?.slice(4).trim();
  if (oldPath === "/dev/null") return "add";
  if (newPath === "/dev/null") return "delete";
  if (oldPath && newPath && cleanDiffPath(oldPath) !== cleanDiffPath(newPath)) {
    return "move";
  }
  if (lines.some((line) => line.startsWith("+") || line.startsWith("-"))) {
    return "update";
  }
  return "unknown";
}

function cleanDiffPath(path: string): string {
  const withoutTimestamp = path.split("\t", 1)[0];
  return withoutTimestamp.startsWith("a/") || withoutTimestamp.startsWith("b/")
    ? withoutTimestamp.slice(2)
    : withoutTimestamp;
}

// Renders a unified diff with syntax-highlighted content. Starts from the plain
// classified lines, then swaps in highlighted tokens once the lazy highlighter
// resolves (falling back to plain when the language is unknown or too large).
function DiffCode({ diff, path }: { diff: string; path: string }) {
  const plain = useMemo(() => buildPlainDiffLines(diff), [diff]);
  const [lines, setLines] = useState<DiffLine[]>(plain);

  useEffect(() => {
    setLines(plain);
    let cancelled = false;
    void highlightDiffToLines(diff, path).then((highlighted) => {
      if (!cancelled && highlighted) setLines(highlighted);
    });
    return () => {
      cancelled = true;
    };
  }, [diff, path, plain]);

  return (
    <pre className="diff-code wp-hl" aria-label={`Diff for ${path}`}>
      {lines.map((line, index) => (
        <span className={`diff-line ${line.kind}`} key={index}>
          {DIFF_MARKER[line.kind]}
          {line.tokens.map((token, i) =>
            token.className ? (
              <span key={i} className={token.className}>
                {token.text}
              </span>
            ) : (
              <Fragment key={i}>{token.text}</Fragment>
            ),
          )}
        </span>
      ))}
    </pre>
  );
}
