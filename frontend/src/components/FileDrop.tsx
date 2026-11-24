"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type Dispatch,
  type DragEvent,
  type ReactNode,
  type RefObject,
  type SetStateAction,
} from "react";
import { createPortal } from "react-dom";

import { filesFromDataTransfer } from "@/components/AttachmentTray";
import { installFileDropGuard, isExternalFileDrag } from "@waypoint/web-core/fileDrop";

// Cancels file drops nobody accepted, which the browser would open in the
// tab.
export function FileDropGuard() {
  useEffect(() => installFileDropGuard(), []);
  return null;
}

// Enter/leave bubble from every child the cursor crosses, so they are
// depth-counted.
export function useElementFileDrop(enabled: boolean, onFiles: (files: File[]) => void) {
  const [dragActive, setDragActive] = useState(false);
  const depth = useRef(0);

  const reset = useCallback(() => {
    depth.current = 0;
    setDragActive(false);
  }, []);

  useEffect(() => {
    if (!dragActive) return;
    window.addEventListener("drop", reset);
    return () => window.removeEventListener("drop", reset);
  }, [dragActive, reset]);

  if (!enabled) return { dragActive: false, dropProps: {} };
  return {
    dragActive,
    dropProps: {
      onDragEnter: (event: DragEvent<HTMLElement>) => {
        if (!isExternalFileDrag(event.dataTransfer)) return;
        depth.current += 1;
        setDragActive(true);
      },
      onDragLeave: () => {
        if (depth.current === 0) return;
        depth.current -= 1;
        if (depth.current === 0) setDragActive(false);
      },
      onDragOver: (event: DragEvent<HTMLElement>) => {
        if (!isExternalFileDrag(event.dataTransfer)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
      },
      onDrop: (event: DragEvent<HTMLElement>) => {
        if (!isExternalFileDrag(event.dataTransfer)) return;
        event.preventDefault();
        reset();
        const files = filesFromDataTransfer(event.dataTransfer);
        if (files.length) onFiles(files);
      },
    },
  };
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
  const [visible, setVisible] = useState(false);
  const [box, setBox] = useState<OverlayBox | null>(null);
  const depth = useRef(0);
  const watchdog = useRef<number | undefined>(undefined);

  const hide = useCallback(() => {
    depth.current = 0;
    window.clearTimeout(watchdog.current);
    setVisible(false);
  }, []);

  useEffect(() => {
    // Browsers repeat `dragover` every few hundred ms while a drag hovers the
    // page, so silence means it ended without a leave the counter saw.
    const arm = () => {
      window.clearTimeout(watchdog.current);
      watchdog.current = window.setTimeout(hide, 1000);
    };
    const onDragEnter = (event: globalThis.DragEvent) => {
      if (!isExternalFileDrag(event.dataTransfer)) return;
      depth.current += 1;
      setVisible(true);
      arm();
    };
    const onDragLeave = () => {
      if (depth.current === 0) return;
      depth.current -= 1;
      if (depth.current === 0) hide();
    };
    const onDragOver = () => {
      if (depth.current > 0) arm();
    };
    window.addEventListener("dragenter", onDragEnter, true);
    window.addEventListener("dragleave", onDragLeave, true);
    window.addEventListener("dragover", onDragOver, true);
    // Bubble phase: hiding during capture lets React flush the unmount at a
    // trusted drop's microtask checkpoint, before the overlay's onDrop runs.
    window.addEventListener("drop", hide);
    return () => {
      window.removeEventListener("dragenter", onDragEnter, true);
      window.removeEventListener("dragleave", onDragLeave, true);
      window.removeEventListener("dragover", onDragOver, true);
      window.removeEventListener("drop", hide);
      window.clearTimeout(watchdog.current);
    };
  }, [hide]);

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
