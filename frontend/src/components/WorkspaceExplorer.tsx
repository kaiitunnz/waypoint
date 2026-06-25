"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  fetchWorkspaceFile,
  fetchWorkspaceFind,
  fetchWorkspaceGitDiff,
  fetchWorkspaceGitStatus,
  workspaceRawUrl,
  type WorkspaceFile,
  type WorkspaceFindMatch,
  type WorkspaceGitFileStatus,
  type WorkspaceGitStatus,
  type WorkspaceTreePage,
} from "@/lib/api";
import { FileIcon, formatBytes } from "@/components/AttachmentTray";
import { FilePreview } from "@/components/FilePreview";
import { WorkspaceDiff } from "@/components/WorkspaceDiff";
import { WorkspaceTree } from "@/components/WorkspaceTree";
import type { EventDiffPreview } from "@/lib/events";
import { copyText } from "@/lib/clipboard";

function gitChangeLabel(status: WorkspaceGitFileStatus): {
  letter: string;
  kind: string;
} {
  if (status.untracked) return { letter: "U", kind: "untracked" };
  const code = status.indexStatus !== " " ? status.indexStatus : status.worktreeStatus;
  switch (code) {
    case "A":
      return { letter: "A", kind: "added" };
    case "D":
      return { letter: "D", kind: "deleted" };
    case "R":
      return { letter: "R", kind: "renamed" };
    case "C":
      return { letter: "C", kind: "added" };
    default:
      return { letter: "M", kind: "modified" };
  }
}

function isStaged(status: WorkspaceGitFileStatus): boolean {
  return !status.untracked && status.indexStatus !== " ";
}

function isUnstaged(status: WorkspaceGitFileStatus): boolean {
  return status.untracked || status.worktreeStatus !== " ";
}

function GitChangeGroup({
  label,
  files,
  staged,
  openPath,
  active,
  showLabel,
  onOpen,
}: {
  label: string;
  files: WorkspaceGitFileStatus[];
  staged: boolean;
  openPath: string | null;
  active: boolean;
  showLabel: boolean;
  onOpen: (status: WorkspaceGitFileStatus, staged: boolean) => void;
}) {
  return (
    <div className="wp-changes-group">
      {showLabel ? (
        <p className="wp-changes-label">
          {label}
          <span className="wp-changes-count">{files.length}</span>
        </p>
      ) : null}
      <ul className="wp-changes-list">
        {files.map((file) => {
          const { letter, kind } = gitChangeLabel(file);
          const selected = active && openPath === file.path;
          const slash = file.path.lastIndexOf("/");
          const name = slash >= 0 ? file.path.slice(slash + 1) : file.path;
          const dir = slash >= 0 ? file.path.slice(0, slash) : "";
          return (
            <li key={file.path}>
              <button
                type="button"
                className={`wp-changes-item${selected ? " selected" : ""}`}
                title={file.path}
                onClick={() => onOpen(file, staged)}
              >
                <span className={`wp-git-badge ${kind}`} aria-hidden="true">
                  {letter}
                </span>
                <span className="wp-changes-name">{name}</span>
                {dir ? <span className="wp-changes-dir">{dir}</span> : null}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// Bold the greedy subsequence of `query` within `text` so finder results show
// which characters matched. Non-matching spans render plain.
function highlightSubsequence(text: string, query: string): ReactNode {
  if (!query) return text;
  const needle = query.toLowerCase();
  const lowered = text.toLowerCase();
  const out: ReactNode[] = [];
  let qi = 0;
  let plain = "";
  let hit = "";
  const flushPlain = () => {
    if (plain) {
      out.push(plain);
      plain = "";
    }
  };
  const flushHit = () => {
    if (hit) {
      out.push(
        <mark key={out.length} className="wp-find-hl">
          {hit}
        </mark>,
      );
      hit = "";
    }
  };
  for (let i = 0; i < text.length; i += 1) {
    if (qi < needle.length && lowered[i] === needle[qi]) {
      flushPlain();
      hit += text[i];
      qi += 1;
    } else {
      flushHit();
      plain += text[i];
    }
  }
  flushPlain();
  flushHit();
  return out;
}

function WorkspaceFindResults({
  matches,
  truncated,
  loading,
  error,
  query,
  activeIndex,
  gitFileMap,
  onPick,
}: {
  matches: WorkspaceFindMatch[];
  truncated: boolean;
  loading: boolean;
  error: string | null;
  query: string;
  activeIndex: number;
  gitFileMap: Map<string, WorkspaceGitFileStatus>;
  onPick: (path: string) => void;
}) {
  if (error) return <div className="wp-find-status error">{error}</div>;
  if (loading && matches.length === 0)
    return <div className="wp-find-status">Searching…</div>;
  if (matches.length === 0)
    return <div className="wp-find-status">No files match “{query}”.</div>;
  return (
    <ul className="wp-find-list" role="listbox" aria-label="File search results">
      {matches.map((match, index) => {
        const slash = match.path.lastIndexOf("/");
        const name = slash >= 0 ? match.path.slice(slash + 1) : match.path;
        const dir = slash >= 0 ? match.path.slice(0, slash) : "";
        const status = gitFileMap.get(match.path);
        const badge = status ? gitChangeLabel(status) : null;
        return (
          <li key={match.path} role="option" aria-selected={index === activeIndex}>
            <button
              type="button"
              className={`wp-find-item${index === activeIndex ? " active" : ""}`}
              title={match.path}
              onClick={() => onPick(match.path)}
            >
              <span className="wp-find-icon" aria-hidden="true">
                <FileIcon />
              </span>
              <span className="wp-find-name">{highlightSubsequence(name, query)}</span>
              {dir ? <span className="wp-find-dir">{dir}</span> : null}
              {badge ? (
                <span className={`wp-git-badge ${badge.kind}`} aria-hidden="true">
                  {badge.letter}
                </span>
              ) : null}
            </button>
          </li>
        );
      })}
      {truncated ? (
        <li className="wp-find-more">More matches — refine the search.</li>
      ) : null}
    </ul>
  );
}

interface WorkspaceExplorerProps {
  host: string;
  token: string;
  sessionId: string;
  initialPath?: string;
  initialDir?: string;
  revealSeq?: number;
  headerActions?: ReactNode;
  showFullPageLink?: boolean;
  // When false (e.g. the dock is closed but kept mounted to preserve state),
  // the focus auto-refresh is suspended.
  active?: boolean;
}

function useWorkspacePreview(host: string, token: string, sessionId: string) {
  const [openPath, setOpenPathState] = useState<string | null>(null);
  const [fileData, setFileData] = useState<WorkspaceFile | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const [root, setRoot] = useState<WorkspaceTreePage["root"] | null>(null);

  // Tracks the most recent request so a slow fetch for a previously-selected
  // file can't overwrite the content of the file the user switched to.
  const latestRequest = useRef<string | null>(null);

  const openFile = useCallback(
    async (path: string) => {
      latestRequest.current = path;
      setOpenPathState(path);
      setFileData(null);
      setFileError(null);
      setFileLoading(true);
      try {
        const data = await fetchWorkspaceFile(host, token, sessionId, path);
        if (latestRequest.current !== path) return;
        setFileData(data);
      } catch (e) {
        if (latestRequest.current !== path) return;
        setFileError(e instanceof Error ? e.message : "Failed to load file");
      } finally {
        if (latestRequest.current === path) setFileLoading(false);
      }
    },
    [host, token, sessionId],
  );

  // Background re-fetch of the open file with no loading flash; swaps content
  // only when mtime/size actually changed, so an auto-refresh that finds nothing
  // new never disturbs the reader's scroll position.
  const silentRefreshFile = useCallback(async () => {
    const path = latestRequest.current;
    if (!path) return;
    try {
      const data = await fetchWorkspaceFile(host, token, sessionId, path);
      if (latestRequest.current !== path) return;
      setFileData((prev) =>
        prev && prev.mtime === data.mtime && prev.size === data.size ? prev : data,
      );
    } catch {
      // Silent: leave the current content in place on a transient failure.
    }
  }, [host, token, sessionId]);

  const reset = useCallback(() => {
    latestRequest.current = null;
    setOpenPathState(null);
    setFileData(null);
    setFileError(null);
    setFileLoading(false);
  }, []);

  return {
    openPath,
    openFile,
    silentRefreshFile,
    fileData,
    fileLoading,
    fileError,
    root,
    setRoot,
    reset,
  };
}

export function WorkspaceExplorer({
  host,
  token,
  sessionId,
  initialPath,
  initialDir,
  revealSeq,
  headerActions,
  showFullPageLink = false,
  active = true,
}: WorkspaceExplorerProps) {
  const [mobileView, setMobileView] = useState<"tree" | "preview">("tree");
  const [revealDir, setRevealDir] = useState<string | null>(null);
  const [changesCollapsed, setChangesCollapsed] = useState(false);
  const [treeRefreshSeq, setTreeRefreshSeq] = useState(0);
  const [treeRefreshing, setTreeRefreshing] = useState(false);
  const [fileRefreshing, setFileRefreshing] = useState(false);
  const {
    openPath,
    openFile,
    silentRefreshFile,
    fileData,
    fileLoading,
    fileError,
    root,
    setRoot,
    reset,
  } = useWorkspacePreview(host, token, sessionId);

  const [gitStatus, setGitStatus] = useState<WorkspaceGitStatus | null>(null);
  const [filterQuery, setFilterQuery] = useState("");
  const [findMatches, setFindMatches] = useState<WorkspaceFindMatch[]>([]);
  const [findTruncated, setFindTruncated] = useState(false);
  const [findLoading, setFindLoading] = useState(false);
  const [findError, setFindError] = useState<string | null>(null);
  const [findActive, setFindActive] = useState(0);
  const filtering = filterQuery.trim().length > 0;
  const [previewMode, setPreviewMode] = useState<"content" | "diff">("content");
  const [diffStaged, setDiffStaged] = useState(false);
  const [diffData, setDiffData] = useState<EventDiffPreview | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [diffError, setDiffError] = useState<string | null>(null);

  const gitFiles = gitStatus?.enabled ? gitStatus.files : null;
  const gitFileMap = useMemo(() => {
    const map = new Map<string, WorkspaceGitFileStatus>();
    for (const file of gitFiles ?? []) map.set(file.path, file);
    return map;
  }, [gitFiles]);
  const dirtyDirs = useMemo(() => {
    const dirs = new Set<string>();
    for (const file of gitFiles ?? []) {
      const parts = file.path.split("/");
      parts.pop();
      let acc = "";
      for (const part of parts) {
        acc = acc ? `${acc}/${part}` : part;
        dirs.add(acc);
      }
    }
    return dirs;
  }, [gitFiles]);
  const stagedFiles = useMemo(() => (gitFiles ?? []).filter(isStaged), [gitFiles]);
  const unstagedFiles = useMemo(() => (gitFiles ?? []).filter(isUnstaged), [gitFiles]);
  const changedCount = useMemo(
    () => new Set((gitFiles ?? []).map((file) => file.path)).size,
    [gitFiles],
  );

  // Refresh git status alongside the tree (mount, refresh button, tab focus).
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    void fetchWorkspaceGitStatus(host, token, sessionId)
      .then((status) => {
        if (!cancelled) setGitStatus(status);
      })
      .catch(() => {
        if (!cancelled) setGitStatus(null);
      });
    return () => {
      cancelled = true;
    };
  }, [host, token, sessionId, treeRefreshSeq, active]);

  // Debounced fuzzy file search. Re-runs when the file set may have changed
  // (treeRefreshSeq) so results stay fresh after an agent edit.
  useEffect(() => {
    const q = filterQuery.trim();
    if (!q) {
      setFindMatches([]);
      setFindTruncated(false);
      setFindError(null);
      setFindLoading(false);
      return;
    }
    setFindLoading(true);
    let cancelled = false;
    const handle = window.setTimeout(() => {
      void fetchWorkspaceFind(host, token, sessionId, q)
        .then((res) => {
          if (cancelled) return;
          setFindMatches(res.matches);
          setFindTruncated(res.truncated);
          setFindError(null);
          setFindActive(0);
        })
        .catch((e) => {
          if (cancelled) return;
          setFindMatches([]);
          setFindError(e instanceof Error ? e.message : "Search failed");
        })
        .finally(() => {
          if (!cancelled) setFindLoading(false);
        });
    }, 150);
    return () => {
      cancelled = true;
      window.clearTimeout(handle);
    };
  }, [filterQuery, host, token, sessionId, treeRefreshSeq]);

  // Fetch the diff when the diff view is showing. Keyed on the open file's mtime
  // so a silent content refresh (agent edited it) re-pulls the diff too.
  const openMtime = fileData?.mtime ?? null;
  useEffect(() => {
    if (previewMode !== "diff" || !openPath) return;
    let cancelled = false;
    setDiffData(null);
    setDiffError(null);
    setDiffLoading(true);
    void fetchWorkspaceGitDiff(host, token, sessionId, openPath, diffStaged)
      .then((data) => {
        if (!cancelled) setDiffData(data);
      })
      .catch((e) => {
        if (!cancelled) setDiffError(e instanceof Error ? e.message : "Failed to load diff");
      })
      .finally(() => {
        if (!cancelled) setDiffLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [previewMode, openPath, diffStaged, host, token, sessionId, treeRefreshSeq, openMtime]);

  useEffect(() => {
    if (initialPath) {
      reset();
      setPreviewMode("content");
      void openFile(initialPath);
      setRevealDir(null);
      setMobileView("preview");
    } else if (initialDir != null) {
      setRevealDir(initialDir);
      setMobileView("tree");
    } else {
      // No specific target (e.g. "Browse workspace"): keep the preserved open
      // file and tree expansion, but clear any stale directory reveal so the
      // tree doesn't re-expand/scroll to a previously-clicked directory.
      setRevealDir(null);
      setMobileView("tree");
    }
    // revealSeq is the retrigger knob: it bumps on every open request so an
    // identical path still re-reveals.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialPath, initialDir, revealSeq]);

  const refreshTree = useCallback(() => {
    setTreeRefreshSeq((s) => s + 1);
    setTreeRefreshing(true);
    window.setTimeout(() => setTreeRefreshing(false), 600);
  }, []);
  const refreshFile = useCallback(() => {
    if (!openPath) return;
    void silentRefreshFile();
    setFileRefreshing(true);
    window.setTimeout(() => setFileRefreshing(false), 600);
  }, [openPath, silentRefreshFile]);

  // Auto-refresh when the tab regains focus — the common case is the user
  // switching to the terminal, the agent editing files, then switching back.
  // Throttled so visibilitychange + focus firing together only refresh once.
  const lastFocusRefresh = useRef(0);
  useEffect(() => {
    if (!active) return;
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      const now = Date.now();
      if (now - lastFocusRefresh.current < 1000) return;
      lastFocusRefresh.current = now;
      void silentRefreshFile();
      setTreeRefreshSeq((s) => s + 1);
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, [silentRefreshFile, active]);

  const handleSelectFile = useCallback(
    async (path: string) => {
      setPreviewMode("content");
      setMobileView("preview");
      await openFile(path);
    },
    [openFile],
  );

  // Opening from the Changes list jumps straight to the diff view. The staged
  // group shows the index-vs-HEAD slice; the unstaged group shows the combined
  // working-tree-vs-HEAD diff. openFile still runs to populate the path/meta and
  // back the "Content" tab (it no-ops visibly for a deleted file).
  const openFromChanges = useCallback(
    (status: WorkspaceGitFileStatus, staged: boolean) => {
      setDiffStaged(staged);
      setPreviewMode("diff");
      setMobileView("preview");
      void openFile(status.path);
    },
    [openFile],
  );

  // Selecting a finder result opens it and reveals its directory in the tree,
  // then clears the query so the tree returns with the file selected.
  const pickFindResult = useCallback(
    (path: string) => {
      const slash = path.lastIndexOf("/");
      setRevealDir(slash >= 0 ? path.slice(0, slash) : "");
      setFilterQuery("");
      void handleSelectFile(path);
    },
    [handleSelectFile],
  );

  const onFilterKey = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setFindActive((i) => Math.min(i + 1, findMatches.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setFindActive((i) => Math.max(i - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        const match = findMatches[findActive];
        if (match) pickFindResult(match.path);
      } else if (e.key === "Escape") {
        e.preventDefault();
        setFilterQuery("");
      }
    },
    [findMatches, findActive, pickFindResult],
  );

  const copyPath = useCallback(() => {
    if (!openPath) return;
    void copyText(openPath);
  }, [openPath]);

  const copyContents = useCallback(() => {
    if (fileData?.content) {
      void copyText(fileData.content);
    }
  }, [fileData]);

  const rawUrl = openPath ? workspaceRawUrl(host, token, sessionId, openPath) : null;
  const mtime = fileData ? new Date(fileData.mtime * 1000).toLocaleString() : null;

  const fullPageHref = showFullPageLink
    ? `/session/${sessionId}/files${openPath ? `?path=${encodeURIComponent(openPath)}` : ""}`
    : null;

  return (
    <div className="wp-explorer">
      <div className="wp-panel-header">
        <div className="wp-panel-title">
          <span>Workspace files</span>
          {root ? (
            <span className="wp-panel-cwd" title={root.worktreePath ?? root.cwd}>
              {root.worktreePath ?? root.cwd}
            </span>
          ) : null}
        </div>
        <div className="wp-panel-mobile-toggle">
          <button
            type="button"
            className={`wp-toggle-btn${mobileView === "tree" ? " active" : ""}`}
            onClick={() => setMobileView("tree")}
          >
            Tree
          </button>
          <button
            type="button"
            className={`wp-toggle-btn${mobileView === "preview" ? " active" : ""}`}
            onClick={() => setMobileView("preview")}
          >
            Preview
          </button>
        </div>
        {fullPageHref ? (
          <Link
            href={fullPageHref}
            target="_blank"
            rel="noreferrer"
            className="wp-panel-fullpage-link"
          >
            Open in full page ↗
          </Link>
        ) : null}
        {headerActions}
      </div>

      <div className="wp-panel-body">
        <div className={`wp-tree-pane${mobileView === "preview" ? " wp-mobile-hidden" : ""}`}>
          <div className="wp-tree-toolbar">
            <span className="wp-tree-toolbar-label">Files</span>
            {gitStatus?.enabled && gitStatus.branch ? (
              <span
                className={`wp-branch${gitStatus.detached ? " detached" : ""}`}
                title={
                  gitStatus.detached
                    ? `Detached HEAD at ${gitStatus.branch}`
                    : `On branch ${gitStatus.branch}`
                }
              >
                <span className="wp-branch-icon" aria-hidden="true">
                  {gitStatus.detached ? "➤" : "⎇"}
                </span>
                <span className="wp-branch-name">{gitStatus.branch}</span>
              </span>
            ) : null}
            <button
              type="button"
              className={`wp-refresh-btn${treeRefreshing ? " spinning" : ""}`}
              onClick={refreshTree}
              title="Refresh files"
              aria-label="Refresh files"
            >
              <span className="wp-refresh-icon" aria-hidden="true">⟳</span>
            </button>
          </div>
          <div className={`wp-tree-filter${filtering ? " active" : ""}`}>
            <svg
              className="wp-tree-filter-icon"
              viewBox="0 0 16 16"
              width="13"
              height="13"
              aria-hidden="true"
            >
              <circle
                cx="6.5"
                cy="6.5"
                r="4.5"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.4"
              />
              <line
                x1="10"
                y1="10"
                x2="14"
                y2="14"
                stroke="currentColor"
                strokeWidth="1.4"
                strokeLinecap="round"
              />
            </svg>
            <input
              className="wp-tree-filter-input"
              type="text"
              placeholder="Go to file…"
              value={filterQuery}
              onChange={(e) => setFilterQuery(e.target.value)}
              onKeyDown={onFilterKey}
              aria-label="Filter files"
              spellCheck={false}
              autoComplete="off"
            />
            {filterQuery ? (
              <button
                type="button"
                className="wp-tree-filter-clear"
                onClick={() => setFilterQuery("")}
                aria-label="Clear filter"
              >
                ×
              </button>
            ) : null}
          </div>
          {filtering ? (
            <div className="wp-tree-scroll">
              <WorkspaceFindResults
                matches={findMatches}
                truncated={findTruncated}
                loading={findLoading}
                error={findError}
                query={filterQuery.trim()}
                activeIndex={findActive}
                gitFileMap={gitFileMap}
                onPick={pickFindResult}
              />
            </div>
          ) : (
            <>
          {stagedFiles.length > 0 || unstagedFiles.length > 0 ? (
            <div className={`wp-changes${changesCollapsed ? " collapsed" : ""}`}>
              <button
                type="button"
                className="wp-changes-header"
                onClick={() => setChangesCollapsed((c) => !c)}
                aria-expanded={!changesCollapsed}
              >
                <span className="wp-changes-chevron" aria-hidden="true">
                  {changesCollapsed ? "▸" : "▾"}
                </span>
                <span>Changes</span>
                <span className="wp-changes-total">{changedCount}</span>
              </button>
              {!changesCollapsed ? (
                <div className="wp-changes-groups">
                  {stagedFiles.length > 0 ? (
                    <GitChangeGroup
                      label="Staged"
                      files={stagedFiles}
                      staged
                      openPath={openPath}
                      active={previewMode === "diff" && diffStaged}
                      showLabel={stagedFiles.length > 0 && unstagedFiles.length > 0}
                      onOpen={openFromChanges}
                    />
                  ) : null}
                  {unstagedFiles.length > 0 ? (
                    <GitChangeGroup
                      label="Unstaged"
                      files={unstagedFiles}
                      staged={false}
                      openPath={openPath}
                      active={previewMode === "diff" && !diffStaged}
                      showLabel={stagedFiles.length > 0 && unstagedFiles.length > 0}
                      onOpen={openFromChanges}
                    />
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : null}
          <div className="wp-tree-scroll">
            <WorkspaceTree
              host={host}
              token={token}
              sessionId={sessionId}
              selectedPath={openPath}
              revealPath={revealDir}
              revealSeq={revealSeq}
              refreshSeq={treeRefreshSeq}
              gitStatus={gitFileMap}
              dirtyDirs={dirtyDirs}
              onSelectFile={handleSelectFile}
              onRootLoaded={setRoot}
            />
          </div>
            </>
          )}
        </div>

        <div className={`wp-preview-pane${mobileView === "tree" ? " wp-mobile-hidden" : ""}`}>
          {openPath ? (
            <>
              <div className="wp-preview-header">
                <span className="wp-preview-path" title={openPath}>
                  {openPath}
                </span>
                <div className="wp-preview-actions">
                  {gitFileMap.has(openPath) ? (
                    <div className="wp-preview-modetoggle">
                      <button
                        type="button"
                        className={`wp-mode-btn${previewMode === "content" ? " active" : ""}`}
                        onClick={() => setPreviewMode("content")}
                      >
                        Content
                      </button>
                      <button
                        type="button"
                        className={`wp-mode-btn${previewMode === "diff" ? " active" : ""}`}
                        onClick={() => setPreviewMode("diff")}
                      >
                        Diff
                      </button>
                    </div>
                  ) : null}
                  {previewMode === "content" && fileData ? (
                    <span className="wp-preview-meta">
                      {formatBytes(fileData.size)} · {mtime}
                    </span>
                  ) : null}
                  <button
                    type="button"
                    className={`wp-preview-btn wp-refresh-btn${fileLoading || fileRefreshing ? " spinning" : ""}`}
                    onClick={refreshFile}
                    title="Refresh file"
                    aria-label="Refresh file"
                  >
                    <span className="wp-refresh-icon" aria-hidden="true">⟳</span>
                  </button>
                  <button
                    type="button"
                    className="wp-preview-btn"
                    onClick={copyPath}
                    title="Copy path"
                  >
                    Copy path
                  </button>
                  {previewMode === "content" && fileData?.content ? (
                    <button
                      type="button"
                      className="wp-preview-btn"
                      onClick={copyContents}
                      title="Copy contents"
                    >
                      Copy contents
                    </button>
                  ) : null}
                  {previewMode === "content" && rawUrl ? (
                    <a
                      href={rawUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="wp-preview-btn"
                      title="Open raw"
                    >
                      Open raw ↗
                    </a>
                  ) : null}
                </div>
              </div>
              <div className="wp-preview-body">
                {previewMode === "diff" ? (
                  diffLoading ? (
                    <div className="wp-preview-loading">Loading…</div>
                  ) : diffError ? (
                    <div className="wp-preview-error">{diffError}</div>
                  ) : diffData && diffData.files.length > 0 ? (
                    <WorkspaceDiff preview={diffData} path={openPath} />
                  ) : (
                    <div className="wp-preview-empty">No changes against HEAD</div>
                  )
                ) : fileLoading ? (
                  <div className="wp-preview-loading">Loading…</div>
                ) : fileError ? (
                  <div className="wp-preview-error">{fileError}</div>
                ) : fileData ? (
                  <FilePreview
                    key={fileData.path}
                    file={fileData}
                    rawUrl={rawUrl ?? ""}
                  />
                ) : null}
              </div>
            </>
          ) : (
            <div className="wp-preview-empty">Select a file to preview</div>
          )}
        </div>
      </div>
    </div>
  );
}
