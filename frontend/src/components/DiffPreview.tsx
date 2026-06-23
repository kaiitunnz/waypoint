import type { EventDiffPreview, EventDiffPreviewFile } from "@/lib/events";

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
        <pre className="diff-code" aria-label={`Diff for ${file.path}`}>
          {file.diff.split("\n").map((line, index) => (
            <span className={`diff-line ${classForDiffLine(line)}`} key={index}>
              {line || " "}
            </span>
          ))}
        </pre>
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

function classForDiffLine(line: string): string {
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("diff --git") || line.startsWith("---") || line.startsWith("+++")) {
    return "meta";
  }
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  return "context";
}
