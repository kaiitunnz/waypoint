"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { isExternalFileDrag } from "../fileDrop";

// Browsers repeat `dragover` every few hundred ms while a drag hovers the page,
// so this much silence means it ended without a leave the counter saw.
const SILENCE_MS = 1000;

// Whether an external file drag is over the page. `hide` ends it early.
export function useFileDragVisible(): [boolean, () => void] {
  const [visible, setVisible] = useState(false);
  // Enter and leave bubble from every child the cursor crosses.
  const depth = useRef(0);
  const watchdog = useRef<number | undefined>(undefined);

  const hide = useCallback(() => {
    depth.current = 0;
    window.clearTimeout(watchdog.current);
    setVisible(false);
  }, []);

  useEffect(() => {
    const arm = () => {
      window.clearTimeout(watchdog.current);
      watchdog.current = window.setTimeout(hide, SILENCE_MS);
    };
    const onDragEnter = (event: DragEvent) => {
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

  return [visible, hide];
}
