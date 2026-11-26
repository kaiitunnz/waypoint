"use client";

import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";

import { filesFromDataTransfer, isExternalFileDrag } from "../fileDrop";

// Files dropped onto one element. Enter/leave bubble from every child the cursor
// crosses, so they are depth-counted.
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
