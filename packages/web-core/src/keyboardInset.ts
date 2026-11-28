// The on-screen keyboard on touch. iOS Safari and Android Chrome with
// `interactive-widget=resizes-visual` shrink the visual viewport when it opens but leave the frame
// that `dvh` and `position: fixed; inset: 0` fill, so the keyboard covers the bottom of every sheet
// and screen. An iOS home-screen app also shrinks that frame and scrolls the page.

// Under this the gap between the viewports is rounding, not a keyboard.
const TOLERANCE = 8;

// Above this the visual viewport is pinch-zoomed, and its height is not a keyboard.
const ZOOMED = 1.01;

// The visual viewport is shorter than the frame: a keyboard is up, or the page is pinch-zoomed.
// Focus and zoom are no part of it.
export function viewportShrunk(layoutHeight: number, visualHeight: number): boolean {
  return layoutHeight - visualHeight >= TOLERANCE;
}

export interface ViewportMetrics {
  // The height of the frame the full-screen layouts fill: a fixed box with `inset: 0`.
  layoutHeight: number;
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

// The keyboard is open when a field that takes typing has focus and the visual viewport is shorter
// than the layout frame; the focus keeps browser bars, zoom and scrollbars from reading as a keyboard.
//
// The visible band is [offsetTop, offsetTop + height]; what lies below it is covered. A browser
// that resizes the frame itself (Android Chrome with `resizes-content`) shrinks it with the visual
// viewport and reads closed, so the keyboard is never counted twice. An iOS home-screen app scrolls
// the page so the band's bottom meets the frame's bottom: that reads an inset of 0 with the
// keyboard still open, and `visibleHeight` bounds anything taller than the band.
export function keyboardState({ layoutHeight, visualHeight, visualOffsetTop, scale }: ViewportMetrics, typing: boolean): KeyboardState {
  if (!typing || scale > ZOOMED || !viewportShrunk(layoutHeight, visualHeight)) return KEYBOARD_CLOSED;
  return { inset: Math.max(0, Math.round(layoutHeight - visualHeight - visualOffsetTop)), visibleHeight: Math.round(visualHeight) };
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
  visualViewport: (Listenable & { height: number; offsetTop: number; scale: number }) | null;
  requestAnimationFrame(callback: () => void): number;
  cancelAnimationFrame(handle: number): void;
}

export interface KeyboardDocument extends Listenable {
  activeElement: FocusTarget | null;
}

export interface KeyboardReading {
  state: KeyboardState;
  focus: FocusTarget | null;
}

// Reports a reading now and after every viewport or focus event, a frame later and once however
// many events arrive; the caller keeps what it needs of the previous one.
export function watchKeyboard(
  win: KeyboardWindow,
  doc: KeyboardDocument,
  layoutHeight: () => number,
  onRead: (reading: KeyboardReading) => void,
): () => void {
  const viewport = win.visualViewport;
  if (!viewport) {
    onRead({ state: KEYBOARD_CLOSED, focus: doc.activeElement });
    return () => {};
  }
  const read = (): KeyboardReading => ({
    state: keyboardState(
      { layoutHeight: layoutHeight(), visualHeight: viewport.height, visualOffsetTop: viewport.offsetTop, scale: viewport.scale },
      isTextEntry(doc.activeElement),
    ),
    focus: doc.activeElement,
  });

  onRead(read());
  let frame: number | null = null;
  // Moving from one field to the next blurs before it focuses, so the focus is read a frame later.
  const schedule = () => {
    frame ??= win.requestAnimationFrame(() => {
      frame = null;
      onRead(read());
    });
  };
  viewport.addEventListener("resize", schedule);
  viewport.addEventListener("scroll", schedule);
  win.addEventListener("resize", schedule);
  doc.addEventListener("focusin", schedule);
  doc.addEventListener("focusout", schedule);
  return () => {
    if (frame !== null) win.cancelAnimationFrame(frame);
    viewport.removeEventListener("resize", schedule);
    viewport.removeEventListener("scroll", schedule);
    win.removeEventListener("resize", schedule);
    doc.removeEventListener("focusin", schedule);
    doc.removeEventListener("focusout", schedule);
  };
}

export interface KeyboardRoot {
  style: { setProperty(name: string, value: string): void; removeProperty(name: string): unknown };
  dataset: Record<string, string | undefined>;
}

// Sets the values tokens.css reads while the keyboard is up: `--keyboard-inset`, `--visible-height`
// and `data-keyboard`.
export function publishKeyboard(root: KeyboardRoot, state: KeyboardState): void {
  if (state.visibleHeight === null) {
    root.style.removeProperty("--keyboard-inset");
    root.style.removeProperty("--visible-height");
    delete root.dataset.keyboard;
    return;
  }
  root.style.setProperty("--keyboard-inset", `${state.inset}px`);
  root.style.setProperty("--visible-height", `${state.visibleHeight}px`);
  root.dataset.keyboard = "";
}
