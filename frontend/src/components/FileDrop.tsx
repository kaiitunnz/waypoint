"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type DragEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";

import { filesFromDataTransfer } from "@/components/AttachmentTray";

// Set while a drag that started inside this document is in flight (e.g. a
// transcript image, which Chrome exposes with a "Files" type) so it never
// reads as a file arriving from the OS.
let internalDrag = false;

function isFileDrag(data: DataTransfer | null): boolean {
  return !!data && Array.from(data.types).includes("Files");
}

function isExternalFileDrag(data: DataTransfer | null): boolean {
  return !internalDrag && isFileDrag(data);
}

// Mounted once app-wide: a file dropped where nothing accepts it is cancelled
// instead of letting the browser navigate away to open it. Window listeners
// run after React's root listeners, so a drop zone that already accepted the
// drag (`defaultPrevented`) keeps its drop effect.
export function FileDropGuard() {
  useEffect(() => {
    const clearInternal = () => {
      internalDrag = false;
    };
    const onDragStart = () => {
      internalDrag = true;
    };
    const onDragOver = (event: globalThis.DragEvent) => {
      if (event.defaultPrevented || !isExternalFileDrag(event.dataTransfer)) return;
      event.preventDefault();
      event.dataTransfer!.dropEffect = "none";
    };
    const onDrop = (event: globalThis.DragEvent) => {
      if (isExternalFileDrag(event.dataTransfer)) event.preventDefault();
      internalDrag = false;
    };
    window.addEventListener("dragstart", onDragStart);
    window.addEventListener("dragend", clearInternal);
    // A drag source removed mid-drag never delivers `dragend` to the window.
    window.addEventListener("pointerdown", clearInternal);
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragstart", onDragStart);
      window.removeEventListener("dragend", clearInternal);
      window.removeEventListener("pointerdown", clearInternal);
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("drop", onDrop);
    };
  }, []);
  return null;
}

// Element-scoped drop target. Enter/leave are depth-counted because both
// bubble from every child the cursor crosses.
export function useElementFileDrop(enabled: boolean, onFiles: (files: File[]) => void) {
  const [dragActive, setDragActive] = useState(false);
  const depth = useRef(0);

  const reset = useCallback(() => {
    depth.current = 0;
    setDragActive(false);
  }, []);

  useEffect(() => {
    if (!dragActive) return;
    window.addEventListener("dragend", reset);
    window.addEventListener("drop", reset);
    return () => {
      window.removeEventListener("dragend", reset);
      window.removeEventListener("drop", reset);
    };
  }, [dragActive, reset]);

  useEffect(() => {
    if (!enabled) reset();
  }, [enabled, reset]);

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

interface FileDropTarget {
  id: number;
  // Why the composer can't take files right now; null when it can.
  blocked: string | null;
  // Completes "to … in <session>", e.g. "your reply".
  label: string;
  onFiles: RefObject<(files: File[]) => void>;
}

interface FileDropRegistry {
  register: (target: FileDropTarget) => () => void;
}

const FileDropContext = createContext<FileDropRegistry | null>(null);

let targetSeq = 0;

export const NO_ATTACHMENTS = "This session doesn't accept attachments";

// Registers a composer as the drop target of the enclosing FileDropZone.
export function useFileDropTarget(
  blocked: string | null,
  label: string,
  onFiles: (files: File[]) => void,
) {
  const registry = useContext(FileDropContext);
  const onFilesRef = useRef(onFiles);
  const [id] = useState(() => ++targetSeq);

  useEffect(() => {
    onFilesRef.current = onFiles;
  });

  useEffect(() => {
    if (!registry) return;
    return registry.register({ id, blocked, label, onFiles: onFilesRef });
  }, [registry, id, blocked, label]);
}

interface OverlayBox {
  top: number;
  left: number;
  right: number;
  bottom: number;
}

// Page-wide drop zone: while files from the OS are dragged anywhere in the
// window, an overlay covers the visible part of `containerRef` and routes the
// drop to the registered composer (a session page mounts at most one).
export function FileDropZone({
  containerRef,
  title,
  children,
}: {
  containerRef: RefObject<HTMLElement | null>;
  title?: string;
  children: ReactNode;
}) {
  const [targets, setTargets] = useState<FileDropTarget[]>([]);
  const [visible, setVisible] = useState(false);
  const [box, setBox] = useState<OverlayBox | null>(null);
  const depth = useRef(0);
  const watchdog = useRef<number | undefined>(undefined);

  const [registry] = useState<FileDropRegistry>(() => ({
    register: (target) => {
      setTargets((prev) => [...prev.filter((t) => t.id !== target.id), target]);
      return () => setTargets((prev) => prev.filter((t) => t.id !== target.id));
    },
  }));

  const hide = useCallback(() => {
    depth.current = 0;
    window.clearTimeout(watchdog.current);
    setVisible(false);
  }, []);

  useEffect(() => {
    // Backstop for a leave the counter never sees (a drag cancelled outside
    // the window, Safari quirks): browsers repeat `dragover` every few hundred
    // ms while a drag hovers the page, so silence means it is gone.
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
    window.addEventListener("dragend", hide);
    window.addEventListener("blur", hide);
    return () => {
      window.removeEventListener("dragenter", onDragEnter, true);
      window.removeEventListener("dragleave", onDragLeave, true);
      window.removeEventListener("dragover", onDragOver, true);
      window.removeEventListener("drop", hide);
      window.removeEventListener("dragend", hide);
      window.removeEventListener("blur", hide);
      window.clearTimeout(watchdog.current);
    };
  }, [hide]);

  useLayoutEffect(() => {
    if (!visible) return;
    const measure = () => {
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect) return;
      const viewportWidth = document.documentElement.clientWidth;
      const viewportHeight = window.innerHeight;
      setBox({
        top: Math.max(rect.top, 0),
        left: Math.max(rect.left, 0),
        right: Math.max(viewportWidth - rect.right, 0),
        bottom: Math.max(viewportHeight - rect.bottom, 0),
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

  const target = targets.at(-1) ?? null;
  const accepting = target !== null && target.blocked === null;

  const overlay =
    visible && box ? (
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
          hide();
          if (!accepting || !isExternalFileDrag(event.dataTransfer)) return;
          const files = filesFromDataTransfer(event.dataTransfer);
          if (files.length) target.onFiles.current(files);
        }}
      >
        <div className="file-drop-frame">
          {accepting ? (
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
              <p className="file-drop-target">{target?.blocked ?? NO_ATTACHMENTS}</p>
            </>
          )}
        </div>
      </div>
    ) : null;

  return (
    <FileDropContext.Provider value={registry}>
      {children}
      {overlay ? createPortal(overlay, document.body) : null}
    </FileDropContext.Provider>
  );
}
