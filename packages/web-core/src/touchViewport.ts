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

export interface BoxHost {
  // A hidden box to measure against: the fixed `inset: 0` frame the full-screen layouts fill, or the
  // physical screen (`100lvh`).
  measure(kind: "frame" | "screen"): Box;
  // Makes the document taller than any screen; the returned function undoes it.
  lift(): () => void;
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

// Publishes the on-screen keyboard, reveals the focused field when the keyboard rises, its visible
// height changes or focus moves to another field, and in a home-screen app restores the frame the
// keyboard leaves short. Each reading `watchKeyboard` takes drives all three; the restore gate
// measures the boxes live. Returns a stop.
export function watchTouchViewport({ win, doc, host, homeScreenApp, publish, reveal }: TouchViewportEnv): () => void {
  const viewport = win.visualViewport;
  if (!viewport) {
    publish(KEYBOARD_CLOSED);
    return () => {};
  }
  const frame = host.measure("frame");
  const screen = homeScreenApp ? host.measure("screen") : null;
  const restore = screen
    ? createFrameRestore({
        // Not while the keyboard is in the way, whether or not a field has focus: a keyboard dismissed
        // with its field still focused is the case to restore.
        isShort: () => !viewportShrunk(frame.height(), viewport.height) && frameIsShort(screen.height(), frame.height()),
        lift: () => host.lift(),
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
