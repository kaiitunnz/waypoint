"use client";

import { type CSSProperties, useCallback, useEffect, useRef, useState } from "react";

import {
  fetchWorkspaceTree,
  type WorkspaceTreeEntry,
  type WorkspaceTreePage,
} from "@/lib/api";
import { FileIcon, FolderIcon } from "@/components/AttachmentTray";

interface DirState {
  entries: WorkspaceTreeEntry[];
  overflow: number | null;
  loading: boolean;
  error: string | null;
}

function sortEntries(entries: WorkspaceTreeEntry[]): WorkspaceTreeEntry[] {
  return [...entries].sort((a, b) => {
    if (a.kind === "dir" && b.kind !== "dir") return -1;
    if (a.kind !== "dir" && b.kind === "dir") return 1;
    return a.name.localeCompare(b.name);
  });
}

interface WorkspaceTreeProps {
  host: string;
  token: string;
  sessionId: string;
  selectedPath: string | null;
  revealPath?: string | null;
  revealSeq?: number;
  onSelectFile: (path: string) => void;
  onRootLoaded?: (root: WorkspaceTreePage["root"]) => void;
}

export function WorkspaceTree({
  host,
  token,
  sessionId,
  selectedPath,
  revealPath,
  revealSeq,
  onSelectFile,
  onRootLoaded,
}: WorkspaceTreeProps) {
  const [dirCache, setDirCache] = useState<Map<string, DirState>>(new Map());
  const [expanded, setExpanded] = useState<Set<string>>(new Set([""]));
  // The directory currently highlighted by a reveal. Cleared on the next user
  // interaction so the highlight doesn't outlive its purpose.
  const [activeReveal, setActiveReveal] = useState<string | null>(null);
  const onRootLoadedRef = useRef(onRootLoaded);
  onRootLoadedRef.current = onRootLoaded;

  const selectFile = useCallback(
    (path: string) => {
      setActiveReveal(null);
      onSelectFile(path);
    },
    [onSelectFile],
  );

  const fetchDir = useCallback(
    async (dirPath: string) => {
      setDirCache((prev) => {
        const next = new Map(prev);
        next.set(dirPath, { entries: [], overflow: null, loading: true, error: null });
        return next;
      });
      try {
        const page = await fetchWorkspaceTree(host, token, sessionId, dirPath);
        if (dirPath === "") {
          onRootLoadedRef.current?.(page.root);
        }
        setDirCache((prev) => {
          const next = new Map(prev);
          next.set(dirPath, {
            entries: sortEntries(page.entries),
            overflow: page.overflow,
            loading: false,
            error: null,
          });
          return next;
        });
      } catch (e) {
        setDirCache((prev) => {
          const next = new Map(prev);
          next.set(dirPath, {
            entries: [],
            overflow: null,
            loading: false,
            error: e instanceof Error ? e.message : "Failed to load",
          });
          return next;
        });
      }
    },
    [host, token, sessionId],
  );

  useEffect(() => {
    setDirCache(new Map());
    setExpanded(new Set([""]));
    void fetchDir("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [host, token, sessionId]);

  // Reveal a directory: expand its full ancestor chain (lazy-fetching any dir
  // not yet cached) so the target node renders, then it scrolls itself into
  // view (see TreeNode).
  useEffect(() => {
    if (revealPath == null || revealPath === "") {
      setActiveReveal(null);
      return;
    }
    const parts = revealPath.split("/").filter(Boolean);
    // Ancestor dirs to expand, plus the target itself. The root ("") is always
    // fetched by the session-reset effect, so it's excluded from the chain.
    const chain: string[] = [];
    let acc = "";
    for (const part of parts) {
      acc = acc ? `${acc}/${part}` : part;
      chain.push(acc);
    }
    setExpanded((prev) => {
      const next = new Set(prev);
      for (const dir of chain) next.add(dir);
      return next;
    });
    for (const dir of chain) {
      if (!dirCache.has(dir)) void fetchDir(dir);
    }
    setActiveReveal(revealPath);
    // revealSeq lets an unchanged revealPath re-trigger a reveal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revealPath, revealSeq]);

  const toggleDir = useCallback(
    (dirPath: string) => {
      setActiveReveal(null);
      setExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(dirPath)) {
          next.delete(dirPath);
        } else {
          next.add(dirPath);
          if (!dirCache.has(dirPath)) {
            void fetchDir(dirPath);
          }
        }
        return next;
      });
    },
    [dirCache, fetchDir],
  );

  const rootState = dirCache.get("");
  if (!rootState || rootState.loading) {
    return <div className="wp-tree-loading">Loading…</div>;
  }
  if (rootState.error) {
    return <div className="wp-tree-error">{rootState.error}</div>;
  }

  return (
    <ul className="wp-tree-root" role="tree">
      {rootState.entries.map((entry) => (
        <TreeNode
          key={entry.name}
          entry={entry}
          parentPath=""
          depth={0}
          dirCache={dirCache}
          expanded={expanded}
          selectedPath={selectedPath}
          revealTarget={activeReveal}
          onSelectFile={selectFile}
          onToggleDir={toggleDir}
        />
      ))}
      {rootState.overflow ? (
        <li className="wp-tree-overflow">+{rootState.overflow} more</li>
      ) : null}
    </ul>
  );
}

function TreeNode({
  entry,
  parentPath,
  depth,
  dirCache,
  expanded,
  selectedPath,
  revealTarget,
  onSelectFile,
  onToggleDir,
}: {
  entry: WorkspaceTreeEntry;
  parentPath: string;
  depth: number;
  dirCache: Map<string, DirState>;
  expanded: Set<string>;
  selectedPath: string | null;
  revealTarget: string | null;
  onSelectFile: (path: string) => void;
  onToggleDir: (dirPath: string) => void;
}) {
  const fullPath = parentPath ? `${parentPath}/${entry.name}` : entry.name;
  const isDir = entry.kind === "dir";
  const isExpanded = expanded.has(fullPath);
  const dirState = isDir ? dirCache.get(fullPath) : undefined;
  const isSelected = !isDir && selectedPath === fullPath;
  const isRevealed = revealTarget != null && revealTarget === fullPath;
  const nodeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (isRevealed) {
      nodeRef.current?.scrollIntoView({ block: "center" });
    }
  }, [isRevealed]);

  return (
    <li
      role="treeitem"
      aria-expanded={isDir ? isExpanded : undefined}
      aria-selected={isSelected}
      style={{ "--depth": depth } as CSSProperties}
    >
      <button
        ref={nodeRef}
        type="button"
        className={`wp-tree-node${isSelected || isRevealed ? " selected" : ""}${isDir ? " is-dir" : ""}`}
        onClick={() => {
          if (isDir) {
            onToggleDir(fullPath);
          } else {
            onSelectFile(fullPath);
          }
        }}
      >
        <span className="wp-tree-indent" style={{ width: `${depth * 16}px` }} />
        <span className="wp-tree-toggle" aria-hidden="true">
          {isDir ? (isExpanded ? "▾" : "▸") : ""}
        </span>
        <span className="wp-tree-icon" aria-hidden="true">
          {isDir ? <FolderIcon /> : <FileIcon />}
        </span>
        <span className="wp-tree-name">{entry.name}</span>
      </button>
      {isDir && isExpanded ? (
        <ul role="group">
          {dirState?.loading ? (
            <li
              className="wp-tree-loading-child"
              style={{ "--depth": depth + 1 } as CSSProperties}
            >
              Loading…
            </li>
          ) : dirState?.error ? (
            <li
              className="wp-tree-error-child"
              style={{ "--depth": depth + 1 } as CSSProperties}
            >
              {dirState.error}
            </li>
          ) : dirState ? (
            <>
              {dirState.entries.map((child) => (
                <TreeNode
                  key={child.name}
                  entry={child}
                  parentPath={fullPath}
                  depth={depth + 1}
                  dirCache={dirCache}
                  expanded={expanded}
                  selectedPath={selectedPath}
                  revealTarget={revealTarget}
                  onSelectFile={onSelectFile}
                  onToggleDir={onToggleDir}
                />
              ))}
              {dirState.overflow ? (
                <li
                  className="wp-tree-overflow"
                  style={{ "--depth": depth + 1 } as CSSProperties}
                >
                  +{dirState.overflow} more
                </li>
              ) : null}
            </>
          ) : null}
        </ul>
      ) : null}
    </li>
  );
}
