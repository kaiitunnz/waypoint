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
  onSelectFile: (path: string) => void;
  onRootLoaded?: (root: WorkspaceTreePage["root"]) => void;
}

export function WorkspaceTree({
  host,
  token,
  sessionId,
  selectedPath,
  onSelectFile,
  onRootLoaded,
}: WorkspaceTreeProps) {
  const [dirCache, setDirCache] = useState<Map<string, DirState>>(new Map());
  const [expanded, setExpanded] = useState<Set<string>>(new Set([""]));
  const onRootLoadedRef = useRef(onRootLoaded);
  onRootLoadedRef.current = onRootLoaded;

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

  const toggleDir = useCallback(
    (dirPath: string) => {
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
          onSelectFile={onSelectFile}
          onToggleDir={toggleDir}
          onFetchDir={fetchDir}
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
  onSelectFile,
  onToggleDir,
}: {
  entry: WorkspaceTreeEntry;
  parentPath: string;
  depth: number;
  dirCache: Map<string, DirState>;
  expanded: Set<string>;
  selectedPath: string | null;
  onSelectFile: (path: string) => void;
  onToggleDir: (dirPath: string) => void;
  onFetchDir: (dirPath: string) => void;
}) {
  const fullPath = parentPath ? `${parentPath}/${entry.name}` : entry.name;
  const isDir = entry.kind === "dir";
  const isExpanded = expanded.has(fullPath);
  const dirState = isDir ? dirCache.get(fullPath) : undefined;
  const isSelected = !isDir && selectedPath === fullPath;

  return (
    <li
      role="treeitem"
      aria-expanded={isDir ? isExpanded : undefined}
      aria-selected={isSelected}
      style={{ "--depth": depth } as CSSProperties}
    >
      <button
        type="button"
        className={`wp-tree-node${isSelected ? " selected" : ""}${isDir ? " is-dir" : ""}`}
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
                  onSelectFile={onSelectFile}
                  onToggleDir={onToggleDir}
                  onFetchDir={() => {}}
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
