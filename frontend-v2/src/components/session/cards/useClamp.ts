import { useLayoutEffect, useRef } from "react";

// Hides the `chip` wrapper while `content` fits the box around it and `cut` says nothing
// else is left out. It writes the DOM directly, so the chip's height is already there
// when the transcript restores its place in the same commit.
export function useClamp<T extends HTMLElement>(cut: boolean) {
  const content = useRef<T>(null);
  const chip = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const inner = content.current;
    const wrap = chip.current;
    const box = inner?.parentElement;
    if (!inner || !wrap || !box) return;
    const measure = () => {
      wrap.hidden = !cut && inner.offsetHeight <= box.clientHeight;
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(inner);
    return () => observer.disconnect();
  });
  return { content, chip };
}
