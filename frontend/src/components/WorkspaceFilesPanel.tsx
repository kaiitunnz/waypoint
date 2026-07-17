"use client";

import { useCallback, useEffect, useState } from "react";
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
  width: number;
  sheet?: boolean;
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
  width,
  sheet = false,
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

  // Mount the dock lazily on first open (no eager tree fetch before then), and
  // keep it mounted afterwards — hidden via CSS when closed — so its tree
  // expansion and open file survive a close/reopen.
  const [hasOpened, setHasOpened] = useState(false);
  useEffect(() => {
    if (open) setHasOpened(true);
  }, [open]);

  // In sheet mode focus may sit outside the portal, where the dock-local keydown
  // never fires; a window handler makes Escape reach it. defaultPrevented yields
  // to a higher-priority modal or the explorer filter.
  useEffect(() => {
    if (!open || !sheet) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, sheet, onClose]);

  if (typeof document === "undefined" || !hasOpened) return null;

  return createPortal(
    <aside
      className={`wp-dock${open ? "" : " wp-dock-closed"}${sheet ? " wp-dock-sheet" : ""}`}
      role="complementary"
      aria-label="Workspace files"
      aria-hidden={!open}
      onKeyDown={(e) => {
        // defaultPrevented lets the explorer consume Escape first; preventDefault
        // keeps the window-level sheet handler from closing a second time.
        if (e.key === "Escape" && !e.defaultPrevented) {
          e.preventDefault();
          onClose();
        }
      }}
    >
      <WorkspaceExplorer
        host={host}
        token={token}
        sessionId={sessionId}
        initialPath={initialPath}
        initialDir={initialDir}
        revealSeq={revealSeq}
        active={open}
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
        aria-valuenow={Math.round(width)}
        aria-valuemin={300}
        aria-valuemax={Math.round(window.innerWidth * 0.6)}
        tabIndex={0}
        onPointerDown={startResize}
        onKeyDown={onResizeKey}
      />
    </aside>,
    document.body,
  );
}
