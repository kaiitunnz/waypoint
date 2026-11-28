import { isTextEntry } from "./keyboardInset";

const MARGIN = 8;

interface Band {
  top: number;
  bottom: number;
}

// How far to scroll so `field` lies inside `view`, both in the same coordinates: positive is down.
// A field taller than the view lines up its top edge instead.
export function scrollDelta(field: Band, view: Band): number {
  if (field.bottom + MARGIN > view.bottom) return Math.min(field.bottom + MARGIN - view.bottom, field.top - view.top);
  if (field.top - MARGIN < view.top) return field.top - MARGIN - view.top;
  return 0;
}

// Scrolls each scroller above the focused field so the field lies in the part the keyboard leaves
// free, and the document only for a field in its flow, not under a fixed ancestor. xterm's hidden
// helper textarea follows the terminal cursor and is skipped.
export function revealFocusedField(doc: Document, win: Window): void {
  const field = doc.activeElement;
  if (!(field instanceof HTMLElement) || !isTextEntry(field) || field.closest(".xterm")) return;
  const viewport = win.visualViewport;
  const top = viewport?.offsetTop ?? 0;
  const visible = { top, bottom: top + (viewport?.height ?? win.innerHeight) };

  for (let node = field.parentElement; node && node !== doc.documentElement; node = node.parentElement) {
    const style = win.getComputedStyle(node);
    const scrolls = (style.overflowY === "auto" || style.overflowY === "scroll") && node.scrollHeight > node.clientHeight;
    if (scrolls) {
      const box = node.getBoundingClientRect();
      const dy = scrollDelta(field.getBoundingClientRect(), { top: Math.max(box.top, visible.top), bottom: Math.min(box.bottom, visible.bottom) });
      if (dy !== 0) node.scrollTop += dy;
    }
    if (style.position === "fixed") return;
  }
  const root = doc.scrollingElement;
  if (root && root.scrollHeight > root.clientHeight) {
    const dy = scrollDelta(field.getBoundingClientRect(), visible);
    if (dy !== 0) win.scrollBy(0, dy);
  }
}
