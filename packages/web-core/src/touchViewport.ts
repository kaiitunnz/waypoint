import { createFrameRestore, frameIsShort } from "./frameRestore";
import {
  KEYBOARD_CLOSED,
  viewportShrunk,
  watchKeyboard,
  type FocusTarget,
  type KeyboardDocument,
  type KeyboardState,
  type KeyboardWindow,
} from "./keyboardInset";

export interface Box {
  height(): number;
  remove(): void;
}

// Makes the hidden, non-interactive boxes the viewport is measured and restored with.
export interface BoxHost {
  createBox(css: string): Box;
}

export interface TouchViewportWindow extends KeyboardWindow {
  setTimeout(callback: () => void, ms: number): number;
  clearTimeout(handle: number): void;
}

export interface TouchViewportEnv {
  win: TouchViewportWindow;
  doc: KeyboardDocument;
  host: BoxHost;
  // An iOS home-screen app, the one client that leaves the frame short of the screen.
  homeScreenApp: boolean;
  publish(state: KeyboardState): void;
  reveal(): void;
}

// The frame the full-screen layouts fill, which `innerHeight` is not while iOS has the keyboard up.
const FRAME_CSS = "position:fixed;inset:0;visibility:hidden;pointer-events:none";
const SCREEN_CSS = "position:fixed;top:0;height:100lvh;visibility:hidden;pointer-events:none";
// 3000px is taller than any screen.
const LIFT_CSS = "position:absolute;top:0;left:0;width:1px;height:3000px;opacity:0;pointer-events:none";

// Publishes the on-screen keyboard, reveals the focused field when the keyboard rises, its visible
// height changes or focus moves to another field, and in a home-screen app restores the frame the
// keyboard leaves short. Every part runs off the one reading `watchKeyboard` takes. Returns a stop.
export function watchTouchViewport({ win, doc, host, homeScreenApp, publish, reveal }: TouchViewportEnv): () => void {
  const viewport = win.visualViewport;
  if (!viewport) {
    publish(KEYBOARD_CLOSED);
    return () => {};
  }
  const frame = host.createBox(FRAME_CSS);
  const screen = homeScreenApp ? host.createBox(SCREEN_CSS) : null;
  const restore = screen
    ? createFrameRestore({
        // Not while the keyboard is in the way, whether or not a field has focus: a keyboard dismissed
        // with its field still focused is the case to restore.
        isShort: () => !viewportShrunk(frame.height(), viewport.height) && frameIsShort(screen.height(), frame.height()),
        lift: () => {
          const tall = host.createBox(LIFT_CSS);
          return () => tall.remove();
        },
        setTimeout: (callback, ms) => win.setTimeout(callback, ms),
        clearTimeout: (handle) => win.clearTimeout(handle),
      })
    : null;

  let state = KEYBOARD_CLOSED;
  let focus: FocusTarget | null = null;
  let revealFrame: number | null = null;
  const stop = watchKeyboard(win, doc, () => frame.height(), (reading) => {
    const next = reading.state;
    const resized = next.visibleHeight !== state.visibleHeight;
    if (resized || next.inset !== state.inset) publish(next);
    restore?.check();
    if (next.visibleHeight !== null) {
      if (resized) {
        // The published values reach the layout a frame later.
        if (revealFrame !== null) win.cancelAnimationFrame(revealFrame);
        revealFrame = win.requestAnimationFrame(() => {
          revealFrame = null;
          reveal();
        });
      } else if (reading.focus !== focus) {
        reveal();
      }
    }
    state = next;
    focus = reading.focus;
  });

  return () => {
    stop();
    restore?.dispose();
    if (revealFrame !== null) win.cancelAnimationFrame(revealFrame);
    frame.remove();
    screen?.remove();
    publish(KEYBOARD_CLOSED);
  };
}
