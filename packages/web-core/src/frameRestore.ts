// iOS leaves the frame short of the screen in a home-screen app, at launch and after the keyboard
// has been up, and restores it once the document is taller than the screen. The restorer makes
// the document that tall until the frame is back. It tries once per shortfall: the shortfall has
// to clear before it tries again.

// Under this the gap between the frame and the screen is rounding.
const TOLERANCE = 8;

// Lets the keyboard finish closing before the frame is judged.
const SETTLE_MS = 200;
const POLL_MS = 50;
const POLLS = 20;

export function frameIsShort(screenHeight: number, frameHeight: number): boolean {
  return screenHeight - frameHeight >= TOLERANCE;
}

export interface FrameRestoreEnv {
  // The frame is short of the screen and nothing else explains it, such as a keyboard that is up.
  isShort(): boolean;
  // Makes the document taller than the screen and returns the undo.
  lift(): () => void;
  setTimeout(callback: () => void, ms: number): number;
  clearTimeout(handle: number): void;
}

export interface FrameRestore {
  // Call whenever the viewport, the keyboard or the focus may have changed.
  check(): void;
  dispose(): void;
}

export function createFrameRestore(env: FrameRestoreEnv): FrameRestore {
  let tried = false;
  let timer: number | null = null;
  let release: (() => void) | null = null;

  const stop = () => {
    if (timer !== null) env.clearTimeout(timer);
    timer = null;
    release?.();
    release = null;
  };
  const poll = (left: number) => {
    timer = env.setTimeout(() => {
      timer = null;
      if (left > 0 && env.isShort()) {
        poll(left - 1);
        return;
      }
      stop();
      if (!env.isShort()) tried = false;
    }, POLL_MS);
  };

  return {
    check() {
      if (!env.isShort()) {
        stop();
        tried = false;
        return;
      }
      if (tried) return;
      tried = true;
      timer = env.setTimeout(() => {
        timer = null;
        if (!env.isShort()) return;
        release = env.lift();
        poll(POLLS);
      }, SETTLE_MS);
    },
    dispose: stop,
  };
}
