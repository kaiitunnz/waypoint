import type { WorkspaceGitFileStatus, WorkspaceGitStatus } from "../api";

export type ChangeLetter = "M" | "A" | "D" | "R" | "U";

export interface ChangedFile {
  path: string;
  letter: ChangeLetter;
}

export interface ChangesSummary {
  // Distinct changed paths; a file both staged and edited since counts once.
  count: number;
  branch: string | null;
  detached: boolean;
  files: ChangedFile[];
}

// The letter of a file's change: untracked is U, else the staged column when it has
// one, else the working-tree column; any other code reads as modified.
export function changeLetter(file: WorkspaceGitFileStatus): ChangeLetter {
  if (file.untracked) return "U";
  const code = file.indexStatus !== " " ? file.indexStatus : file.worktreeStatus;
  switch (code) {
    case "A":
    case "C":
      return "A";
    case "D":
      return "D";
    case "R":
      return "R";
    default:
      return "M";
  }
}

// What the inspector says of a workspace's changes. Null where git is off or the
// directory is not a repository.
export function summarizeChanges(status: WorkspaceGitStatus | null): ChangesSummary | null {
  if (!status || !status.enabled) return null;
  const seen = new Set<string>();
  const files: ChangedFile[] = [];
  for (const file of status.files) {
    if (!file.path || seen.has(file.path)) continue;
    seen.add(file.path);
    files.push({ path: file.path, letter: changeLetter(file) });
  }
  return { count: files.length, branch: status.branch, detached: status.detached, files };
}
