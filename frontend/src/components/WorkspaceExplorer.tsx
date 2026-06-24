"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import {
  fetchWorkspaceFile,
  workspaceRawUrl,
  type WorkspaceFile,
  type WorkspaceTreePage,
} from "@/lib/api";
import { formatBytes } from "@/components/AttachmentTray";
import { FilePreview } from "@/components/FilePreview";
import { WorkspaceTree } from "@/components/WorkspaceTree";
import { copyText } from "@/lib/clipboard";

interface WorkspaceExplorerProps {
  host: string;
  token: string;
  sessionId: string;
  recentPaths: string[];
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
  recentPaths,
  initialPath,
  initialDir,
  revealSeq,
  headerActions,
  showFullPageLink = false,
  active = true,
}: WorkspaceExplorerProps) {
  const [mobileView, setMobileView] = useState<"tree" | "preview">("tree");
  const [revealDir, setRevealDir] = useState<string | null>(null);
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

  useEffect(() => {
    if (initialPath) {
      reset();
      void openFile(initialPath);
      setRevealDir(null);
      setMobileView("preview");
    } else if (initialDir != null) {
      setRevealDir(initialDir);
      setMobileView("tree");
    } else {
      // No specific target (e.g. "Browse workspace"): keep the preserved open
      // file and tree expansion; just surface the tree pane.
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
      await openFile(path);
      setMobileView("preview");
    },
    [openFile],
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
          {recentPaths.length > 0 ? (
            <div className="wp-recent">
              <p className="wp-recent-label">Recently written</p>
              <ul className="wp-recent-list">
                {recentPaths.slice(0, 10).map((p) => (
                  <li key={p}>
                    <button
                      type="button"
                      className={`wp-recent-item${openPath === p ? " selected" : ""}`}
                      title={p}
                      onClick={() => void handleSelectFile(p)}
                    >
                      {p.split("/").pop() ?? p}
                      <span className="wp-recent-full">{p}</span>
                    </button>
                  </li>
                ))}
              </ul>
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
              onSelectFile={handleSelectFile}
              onRootLoaded={setRoot}
            />
          </div>
        </div>

        <div className={`wp-preview-pane${mobileView === "tree" ? " wp-mobile-hidden" : ""}`}>
          {openPath ? (
            <>
              <div className="wp-preview-header">
                <span className="wp-preview-path" title={openPath}>
                  {openPath}
                </span>
                <div className="wp-preview-actions">
                  {fileData ? (
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
                  {fileData?.content ? (
                    <button
                      type="button"
                      className="wp-preview-btn"
                      onClick={copyContents}
                      title="Copy contents"
                    >
                      Copy contents
                    </button>
                  ) : null}
                  {rawUrl ? (
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
                {fileLoading ? (
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
