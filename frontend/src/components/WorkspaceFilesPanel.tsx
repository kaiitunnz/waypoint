"use client";

import { useCallback } from "react";
import { createPortal } from "react-dom";

import { WorkspaceExplorer } from "@/components/WorkspaceExplorer";

interface WorkspaceFilesPanelProps {
  host: string;
  token: string;
  sessionId: string;
  open: boolean;
  initialPath?: string;
  initialDir?: string;
  revealSeq?: number;
  recentPaths: string[];
  width: number;
  onResize: (width: number) => void;
  onClose: () => void;
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
  width,
  onResize,
  onClose,
}: WorkspaceFilesPanelProps) {
  const clampWidth = useCallback(
    (w: number) => Math.max(300, Math.min(w, Math.round(window.innerWidth * 0.6))),
    [],
  );
  const startResize = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      const startX = e.clientX;
      const startWidth = width;
      const onMove = (ev: PointerEvent) => onResize(clampWidth(startWidth + (ev.clientX - startX)));
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        document.body.classList.remove("wp-dock-resizing");
      };
      document.body.classList.add("wp-dock-resizing");
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [clampWidth, onResize, width],
  );
  const onResizeKey = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "ArrowRight") {
        e.preventDefault();
        onResize(clampWidth(width + 24));
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        onResize(clampWidth(width - 24));
      }
    },
    [clampWidth, onResize, width],
  );

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <aside
      className="wp-dock"
      role="complementary"
      aria-label="Workspace files"
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      <WorkspaceExplorer
        host={host}
        token={token}
        sessionId={sessionId}
        recentPaths={recentPaths}
        initialPath={initialPath}
        initialDir={initialDir}
        revealSeq={revealSeq}
        showFullPageLink
        headerActions={
          <button
            type="button"
            className="wp-panel-close"
            aria-label="Close"
            onClick={onClose}
          >
            ×
          </button>
        }
      />
      <div
        className="wp-dock-resizer"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize files panel"
        tabIndex={0}
        onPointerDown={startResize}
        onKeyDown={onResizeKey}
      />
    </aside>,
    document.body,
  );
}
