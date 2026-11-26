import { useLayoutEffect, useRef } from "react";

// Hides the `chip` wrapper while `content` fits its box, unless `keepChip`. It writes the
// DOM directly, so the chip's height is in place before the transcript's layout effect
// restores its scroll position.
export function useClamp<T extends HTMLElement>(keepChip: boolean) {
  const content = useRef<T>(null);
  const chip = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const inner = content.current;
    const wrap = chip.current;
    const box = inner?.parentElement;
    if (!inner || !wrap || !box) return;
    const measure = () => {
      const hidden = !keepChip && inner.offsetHeight <= box.clientHeight;
      if (wrap.hidden !== hidden) wrap.hidden = hidden;
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(inner);
    return () => observer.disconnect();
  }, [keepChip]);
  return { content, chip };
}
