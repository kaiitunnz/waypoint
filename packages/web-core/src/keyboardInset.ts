// The on-screen keyboard on touch. iOS Safari (a home-screen app too) and Android Chrome with
// `interactive-widget=resizes-visual` shrink only the visual viewport when it opens; the layout
// viewport, `dvh` and `position: fixed; inset: 0` stay put, so the keyboard covers the bottom of
// every sheet and screen.

// Under this the gap between the viewports is rounding or a browser bar, not a keyboard.
const TOLERANCE = 8;

// Above this the visual viewport is pinch-zoomed, and its height is not a keyboard.
const ZOOMED = 1.01;

export interface ViewportMetrics {
  layoutHeight: number;
  visualHeight: number;
  visualOffsetTop: number;
  scale: number;
  // The gap between the two heights with no keyboard.
  restGap: number;
}

// An iOS home-screen app shows a gap between `innerHeight` and the visual viewport's height with no
// keyboard up, about the safe-area insets. Only a keyboard widens it, so the smallest gap seen
// since the window last changed width is the gap at rest.
export interface RestGap {
  width: number;
  gap: number;
}

export const UNKNOWN_REST_GAP: RestGap = { width: Number.NaN, gap: Number.POSITIVE_INFINITY };

export function trackRestGap(
  rest: RestGap,
  { layoutWidth, layoutHeight, visualHeight, scale }: { layoutWidth: number; layoutHeight: number; visualHeight: number; scale: number },
): RestGap {
  if (scale > ZOOMED) return rest;
  const known = layoutWidth === rest.width ? rest.gap : Number.POSITIVE_INFINITY;
  return { width: layoutWidth, gap: Math.min(known, Math.max(0, layoutHeight - visualHeight)) };
}

export interface KeyboardState {
  // Px at the bottom of the layout viewport that the keyboard covers.
  inset: number;
  // Px of the layout viewport in view while the keyboard is open, null while it is closed.
  visibleHeight: number | null;
}

export const KEYBOARD_CLOSED: KeyboardState = { inset: 0, visibleHeight: null };

// The keyboard is open when a field that takes typing has focus and the visual viewport has lost
// height; the focus keeps browser bars, zoom and scrollbars from reading as a keyboard.
//
// The visible band is [offsetTop, offsetTop + height]; what lies below it is covered. A browser
// that resizes the layout viewport itself (Android Chrome with `resizes-content`) shrinks
// `innerHeight` with the visual viewport and reads closed, so the keyboard is never counted twice.
// An iOS pan that brings the band's bottom to the layout bottom reads an inset of 0 with the
// keyboard still open; `visibleHeight` still bounds anything taller than the band.
export function keyboardState({ layoutHeight, visualHeight, visualOffsetTop, scale, restGap }: ViewportMetrics, typing: boolean): KeyboardState {
  if (!typing || scale > ZOOMED) return KEYBOARD_CLOSED;
  const lost = layoutHeight - visualHeight - restGap;
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
  innerWidth: number;
  innerHeight: number;
  visualViewport: (Listenable & { height: number; offsetTop: number; scale: number }) | null;
  requestAnimationFrame(callback: () => void): number;
  cancelAnimationFrame(handle: number): void;
}

export interface KeyboardDocument extends Listenable {
  activeElement: FocusTarget | null;
}

export function watchKeyboard(win: KeyboardWindow, doc: KeyboardDocument, onChange: (state: KeyboardState) => void): () => void {
  const viewport = win.visualViewport;
  if (!viewport) {
    onChange(KEYBOARD_CLOSED);
    return () => {};
  }
  let rest = UNKNOWN_REST_GAP;
  const measure = () => {
    const layout = { layoutWidth: win.innerWidth, layoutHeight: win.innerHeight, visualHeight: viewport.height, scale: viewport.scale };
    rest = trackRestGap(rest, layout);
    return keyboardState({ ...layout, visualOffsetTop: viewport.offsetTop, restGap: rest.gap }, isTextEntry(doc.activeElement));
  };

  let last = measure();
  onChange(last);
  let frame: number | null = null;
  const read = () => {
    frame = null;
    const next = measure();
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
