import { Fragment, useEffect, useMemo, useState } from "react";

import type { EventDiffPreview, EventDiffPreviewFile } from "@waypoint/web-core/events";
import {
  buildPlainDiffLines,
  DIFF_MARKER,
  highlightDiffToLines,
  type DiffLine,
} from "@waypoint/web-core/highlight";
import { diffFilePathLabel, displayChangeType, PHASE_LABEL } from "@waypoint/web-core/session/diffPreview";

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
  const pathLabel = diffFilePathLabel(file);
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
