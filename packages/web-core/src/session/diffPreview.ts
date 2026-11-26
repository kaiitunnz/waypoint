import type { EventDiffPreview, EventDiffPreviewFile } from "../events";
import { relativeToCwd } from "../workspacePaths";

export const PHASE_LABEL: Record<EventDiffPreview["phase"], string> = {
  proposed: "Proposed changes",
  applied: "Applied changes",
  aggregate: "Turn changes",
};

const RENAME_HEADER = /^(?:diff --git .*|similarity index 100%|rename (?:from|to) .*|--- .*|\+\+\+ .*)?$/;

// A file that only moved: no line changed, so its two paths are the whole change.
export function isBareRename(file: EventDiffPreviewFile): boolean {
  return (
    Boolean(file.oldPath) &&
    file.oldPath !== file.path &&
    file.additions === 0 &&
    file.deletions === 0 &&
    // Codex names the new path under the diff.
    file.diff.split("\n").every((line) => RENAME_HEADER.test(line) || line === `Moved to: ${file.path}`)
  );
}

// A file's change in a word. A backend that could not tell leaves it "unknown",
// so it is read off the diff, else called an edit.
export function displayChangeType(file: EventDiffPreviewFile): {
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

// "old -> new" for a move, else the path; relative to `cwd` when given.
export function diffFilePathLabel(file: EventDiffPreviewFile, cwd?: string | null): string {
  const path = relativeToCwd(file.path, cwd);
  return file.oldPath && file.oldPath !== file.path ? `${relativeToCwd(file.oldPath, cwd)} -> ${path}` : path;
}
