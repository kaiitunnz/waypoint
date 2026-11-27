"use client";

import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type Dispatch,
  type ReactNode,
  type RefObject,
  type SetStateAction,
} from "react";
import { createPortal } from "react-dom";

import { filesFromDataTransfer, installFileDropGuard } from "@waypoint/web-core/fileDrop";
import { useFileDragVisible } from "@waypoint/web-core/hooks/useFileDragVisible";

// Cancels file drops nobody accepted, which the browser would open in the
// tab.
export function FileDropGuard() {
  useEffect(() => installFileDropGuard(), []);
  return null;
}

export type FileDropBlock = "unsupported" | "exited";

const BLOCK_MESSAGES: Record<FileDropBlock, string> = {
  unsupported: "This session doesn't accept attachments",
  exited: "This session has exited",
};

interface FileDropTarget {
  blocked: FileDropBlock | null;
  // Completes "to … in <session>", e.g. "your reply".
  label: string;
  onFiles: RefObject<(files: File[]) => void>;
}

type SetFileDropTarget = Dispatch<SetStateAction<FileDropTarget | null>>;

// A session page mounts at most one composer, so it is the drop target.
const FileDropContext = createContext<SetFileDropTarget | null>(null);

export function useFileDropTarget(
  blocked: FileDropBlock | null,
  label: string,
  onFiles: (files: File[]) => void,
) {
  const setTarget = useContext(FileDropContext);
  const onFilesRef = useRef(onFiles);

  useEffect(() => {
    onFilesRef.current = onFiles;
  });

  useEffect(() => {
    if (!setTarget) return;
    const target = { blocked, label, onFiles: onFilesRef };
    setTarget(target);
    return () => setTarget((current) => (current === target ? null : current));
  }, [setTarget, blocked, label]);
}

interface OverlayBox {
  top: number;
  left: number;
  right: number;
  bottom: number;
}

export function FileDropZone({
  containerRef,
  title,
  children,
}: {
  containerRef: RefObject<HTMLElement | null>;
  title?: string;
  children: ReactNode;
}) {
  const [target, setTarget] = useState<FileDropTarget | null>(null);
  const visible = useFileDragVisible();
  const [box, setBox] = useState<OverlayBox | null>(null);

  useLayoutEffect(() => {
    if (!visible) return;
    const measure = () => {
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect) return;
      setBox({
        top: Math.max(rect.top, 0),
        left: Math.max(rect.left, 0),
        right: Math.max(document.documentElement.clientWidth - rect.right, 0),
        bottom: Math.max(window.innerHeight - rect.bottom, 0),
      });
    };
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [visible, containerRef]);

  const accepting = target?.blocked === null;

  const overlay =
    visible && box && target ? (
      <div
        className={`file-drop-overlay${accepting ? "" : " is-muted"}`}
        style={box}
        aria-hidden="true"
        onDragOver={(event) => {
          event.preventDefault();
          event.dataTransfer.dropEffect = accepting ? "copy" : "none";
        }}
        onDrop={(event) => {
          event.preventDefault();
          if (!accepting) return;
          const files = filesFromDataTransfer(event.dataTransfer);
          if (files.length) target.onFiles.current(files);
        }}
      >
        <div className="file-drop-frame">
          {target.blocked === null ? (
            <>
              <span className="file-drop-glyph">⤓</span>
              <p className="file-drop-title">Drop to attach</p>
              <p className="file-drop-target">
                to {target.label}
                {title ? (
                  <>
                    {" in "}
                    <span className="file-drop-session">{title}</span>
                  </>
                ) : null}
              </p>
            </>
          ) : (
            <>
              <p className="file-drop-title">Can&apos;t attach here</p>
              <p className="file-drop-target">{BLOCK_MESSAGES[target.blocked]}</p>
            </>
          )}
        </div>
      </div>
    ) : null;

  return (
    <FileDropContext.Provider value={setTarget}>
      {children}
      {overlay ? createPortal(overlay, document.body) : null}
    </FileDropContext.Provider>
  );
}
