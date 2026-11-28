// The on-screen keyboard on touch. iOS Safari (a home-screen app too) and Android Chrome with
// `interactive-widget=resizes-visual` shrink only the visual viewport when it opens; the layout
// viewport, `dvh` and `position: fixed; inset: 0` stay put, so the keyboard covers the bottom of
// every sheet and screen.

// Under this the gap between the viewports is rounding or a browser bar, not a keyboard.
const TOLERANCE = 8;

// Above this the visual viewport is pinch-zoomed, and its height is not a keyboard.
const ZOOMED = 1.01;

export interface ViewportMetrics {
  // `window.innerHeight`: the layout viewport.
  layoutHeight: number;
  // `visualViewport.height`, `.offsetTop` and `.scale`: the part of the layout viewport in view.
  visualHeight: number;
  visualOffsetTop: number;
  scale: number;
}

export interface KeyboardState {
  // Px at the bottom of the layout viewport that the keyboard covers.
  inset: number;
  // Px of the layout viewport in view while the keyboard is open, null while it is closed.
  visibleHeight: number | null;
}

export const KEYBOARD_CLOSED: KeyboardState = { inset: 0, visibleHeight: null };

// The keyboard is open when a field that takes typing has focus and the visual viewport has lost
// height. Requiring the focus keeps browser bars, zoom and scrollbars from reading as a keyboard.
//
// The visible band is [offsetTop, offsetTop + height]; what lies below it is covered. A browser that
// resizes the layout viewport itself (Android Chrome with `resizes-content`) shrinks `innerHeight`
// along with the visual viewport, which reads closed, so the keyboard is never counted twice. An iOS
// pan of the visual viewport that brings its bottom edge back to the layout bottom reads an inset
// of 0 with the keyboard still open: the layout is already where the person can see it, and
// `visibleHeight` still bounds what may be taller than the band.
export function keyboardState({ layoutHeight, visualHeight, visualOffsetTop, scale }: ViewportMetrics, typing: boolean): KeyboardState {
  if (!typing || scale > ZOOMED) return KEYBOARD_CLOSED;
  if (![layoutHeight, visualHeight, visualOffsetTop, scale].every(Number.isFinite)) return KEYBOARD_CLOSED;
  const lost = layoutHeight - visualHeight;
  if (lost < TOLERANCE) return KEYBOARD_CLOSED;
  return { inset: Math.max(0, Math.round(lost - visualOffsetTop)), visibleHeight: Math.round(visualHeight) };
}

const NO_KEYBOARD = new Set(["button", "checkbox", "color", "file", "hidden", "image", "radio", "range", "reset", "submit"]);

export interface FocusTarget {
  tagName: string;
  type?: string;
  isContentEditable?: boolean;
}

export function isTextEntry(element: FocusTarget | null): boolean {
  if (!element) return false;
  const tag = element.tagName.toLowerCase();
  if (tag === "textarea" || tag === "select") return true;
  if (tag === "input") return !NO_KEYBOARD.has((element.type ?? "text").toLowerCase());
  return element.isContentEditable === true;
}

interface Listenable {
  addEventListener(type: string, listener: () => void): void;
  removeEventListener(type: string, listener: () => void): void;
}

// What the watcher reads; narrow so tests run without a DOM.
export interface KeyboardWindow extends Listenable {
  innerHeight: number;
  visualViewport: (Listenable & { height: number; offsetTop: number; scale: number }) | null;
  requestAnimationFrame(callback: () => void): number;
  cancelAnimationFrame(handle: number): void;
}

export interface KeyboardDocument extends Listenable {
  activeElement: FocusTarget | null;
}

export function currentKeyboardState(win: KeyboardWindow, doc: KeyboardDocument): KeyboardState {
  const viewport = win.visualViewport;
  if (!viewport) return KEYBOARD_CLOSED;
  return keyboardState(
    { layoutHeight: win.innerHeight, visualHeight: viewport.height, visualOffsetTop: viewport.offsetTop, scale: viewport.scale },
    isTextEntry(doc.activeElement),
  );
}

export function watchKeyboard(win: KeyboardWindow, doc: KeyboardDocument, onChange: (state: KeyboardState) => void): () => void {
  let last = currentKeyboardState(win, doc);
  onChange(last);
  const viewport = win.visualViewport;
  if (!viewport) return () => {};

  let frame: number | null = null;
  const read = () => {
    frame = null;
    const next = currentKeyboardState(win, doc);
    if (next.inset === last.inset && next.visibleHeight === last.visibleHeight) return;
    last = next;
    onChange(next);
  };
  // Moving from one field to the next blurs before it focuses, so the focus is read a frame later.
  const schedule = () => {
    frame ??= win.requestAnimationFrame(read);
  };
  viewport.addEventListener("resize", schedule);
  viewport.addEventListener("scroll", schedule);
  // A rotation resizes the layout viewport, which `visualViewport` may report before or after.
  win.addEventListener("resize", schedule);
  win.addEventListener("orientationchange", schedule);
  doc.addEventListener("focusin", schedule);
  doc.addEventListener("focusout", schedule);
  return () => {
    if (frame !== null) win.cancelAnimationFrame(frame);
    viewport.removeEventListener("resize", schedule);
    viewport.removeEventListener("scroll", schedule);
    win.removeEventListener("resize", schedule);
    win.removeEventListener("orientationchange", schedule);
    doc.removeEventListener("focusin", schedule);
    doc.removeEventListener("focusout", schedule);
  };
}

const MARGIN = 8;

interface Band {
  top: number;
  bottom: number;
}

// How far to scroll a scroller so `field` lies inside `view`, both in the same coordinates: positive
// is down. A field taller than the view lines up its top edge instead.
export function scrollDelta(field: Band, view: Band, margin = MARGIN): number {
  if (field.bottom + margin > view.bottom) return Math.min(field.bottom + margin - view.bottom, field.top - view.top);
  if (field.top - margin < view.top) return field.top - margin - view.top;
  return 0;
}

// Brings the focused field into the part of every scroller above it that the keyboard leaves free.
// A sheet or screen has already shrunk to that part, so this scrolls its body; a field inside a fixed
// layout has no document scroll to follow, and the document scrolls only for a field in its flow.
// xterm's hidden helper textarea follows the terminal cursor and is left alone.
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
