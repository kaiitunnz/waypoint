// The width of a pane the person can drag: clamped to its range and half the
// window, and forgotten when it comes back to the default.

export interface PaneWidthConfig {
  storageKey: string;
  defaultWidth: number;
  min: number;
  max: number;
  // A width this close to the default is the default.
  snap: number;
}

export function clampPaneWidth(width: number, { min, max }: PaneWidthConfig, viewport?: number): number {
  const limit = viewport === undefined ? max : Math.min(max, Math.round(viewport * 0.5));
  return Math.max(min, Math.min(width, limit));
}

// Undefined stands for "no preference", which is the default width.
export function snapPaneWidth(width: number, config: PaneWidthConfig, viewport?: number): number | undefined {
  const clamped = clampPaneWidth(width, config, viewport);
  return Math.abs(clamped - config.defaultWidth) <= config.snap ? undefined : clamped;
}

export const PANE_KEY_STEP = 24;
