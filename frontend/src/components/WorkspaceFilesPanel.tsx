"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

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

interface WorkspaceFilesPanelProps {
  host: string;
  token: string;
  sessionId: string;
  open: boolean;
  initialPath?: string;
  initialDir?: string;
  revealSeq?: number;
  recentPaths: string[];
  onClose: () => void;
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

  const reset = useCallback(() => {
    latestRequest.current = null;
    setOpenPathState(null);
    setFileData(null);
    setFileError(null);
    setFileLoading(false);
  }, []);

  return { openPath, openFile, fileData, fileLoading, fileError, root, setRoot, reset };
}

export function WorkspaceFilesPanel({
  host,
  token,
  sessionId,
  open,
  initialPath,
  initialDir,
  revealSeq,
  recentPaths,
  onClose,
}: WorkspaceFilesPanelProps) {
  const [mobileView, setMobileView] = useState<"tree" | "preview">("tree");
  const [revealDir, setRevealDir] = useState<string | null>(null);
  const { openPath, openFile, fileData, fileLoading, fileError, root, setRoot, reset } =
    useWorkspacePreview(host, token, sessionId);

  useEffect(() => {
    if (!open) return;
    reset();
    if (initialPath) {
      void openFile(initialPath);
      setRevealDir(null);
      setMobileView("preview");
    } else if (initialDir != null) {
      setRevealDir(initialDir);
      setMobileView("tree");
    } else {
      setRevealDir(null);
      setMobileView("tree");
    }
    // revealSeq is the retrigger knob: it bumps on every open request so an
    // identical path still re-reveals.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialPath, initialDir, revealSeq]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

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

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div
      className="wp-panel-backdrop"
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="wp-panel-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Workspace files"
      >
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
          <button
            type="button"
            className="wp-panel-close"
            aria-label="Close"
            onClick={onClose}
          >
            ×
          </button>
        </div>

        <div className="wp-panel-body">
          <div className={`wp-tree-pane${mobileView === "preview" ? " wp-mobile-hidden" : ""}`}>
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
    </div>,
    document.body,
  );
}
