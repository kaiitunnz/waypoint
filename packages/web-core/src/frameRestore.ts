// iOS leaves the frame short of the screen in a home-screen app, at launch and after the keyboard
// has been up, and restores it once the document is taller than the screen. The restorer makes
// the document that tall until the frame is back. It starts at once, so the frame grows as the
// keyboard closes, and tries again once if iOS ignored that first lift. A shortfall has to clear
// before the restorer tries for it again.

// Under this the gap between the frame and the screen is rounding.
const TOLERANCE = 8;

// Delay before each attempt; the retry lets a close animation that ignored the first lift finish.
const ATTEMPT_DELAYS_MS = [0, 250];
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
  const clear = () => {
    stop();
    tried = false;
  };
  const poll = (attempt: number, left: number) => {
    timer = env.setTimeout(() => {
      timer = null;
      if (!env.isShort()) return clear();
      if (left > 0) return poll(attempt, left - 1);
      stop();
      if (attempt + 1 < ATTEMPT_DELAYS_MS.length) schedule(attempt + 1);
    }, POLL_MS);
  };
  const schedule = (attempt: number) => {
    timer = env.setTimeout(() => {
      timer = null;
      if (!env.isShort()) return clear();
      release = env.lift();
      poll(attempt, POLLS);
    }, ATTEMPT_DELAYS_MS[attempt]);
  };

  return {
    check() {
      if (!env.isShort()) return clear();
      if (tried) return;
      tried = true;
      schedule(0);
    },
    dispose: stop,
  };
}
