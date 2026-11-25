/**
 * The size a person gives a resizable popover: what is stored, and how a drag or
 * a key press changes it.
 */

// Versioned so a future shape change can coexist with an old saved value.
export const USAGE_POPOVER_SIZE_KEY = "waypoint.usage-popover-size.v1";

// Pixel steps for keyboard resize; Shift takes the larger stride.
const KEY_STEP = 24;
const KEY_STEP_LARGE = 72;
// A resize landing this close to an axis's default returns that axis to it.
const SNAP_THRESHOLD = 14;

// An axis without a value takes the popover's default.
export interface PopoverSize {
  width?: number;
  height?: number;
}

export type ResizeAxis = "width" | "height";

// Which way each axis grows from the popover's anchor: 1 is right and down, -1
// left and up.
export interface ResizeDirection {
  width: 1 | -1;
  height: 1 | -1;
}

function dimension(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : undefined;
}

export function parsePopoverSize(raw: string | null): PopoverSize {
  if (!raw) return {};
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed === null || typeof parsed !== "object") return {};
    const record = parsed as Record<string, unknown>;
    const size: PopoverSize = {};
    const width = dimension(record.width);
    const height = dimension(record.height);
    if (width !== undefined) size.width = width;
    if (height !== undefined) size.height = height;
    return size;
  } catch {
    return {};
  }
}

export function readPopoverSize(key: string): PopoverSize {
  if (typeof window === "undefined") return {};
  try {
    return parsePopoverSize(window.localStorage.getItem(key));
  } catch {
    return {};
  }
}

export function writePopoverSize(key: string, size: PopoverSize): void {
  if (typeof window === "undefined") return;
  try {
    if (size.width === undefined && size.height === undefined) {
      window.localStorage.removeItem(key);
    } else {
      window.localStorage.setItem(key, JSON.stringify(size));
    }
  } catch {
    // Storage disabled or over quota: the preference is best-effort only.
  }
}

export function clampSize(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(value, max));
}

// What to store for an axis resized to `value`: nothing when that lands on the
// default.
export function resizedDimension(value: number, min: number, max: number, fallback: number | null): number | undefined {
  const clamped = clampSize(value, min, max);
  return fallback !== null && Math.abs(clamped - fallback) <= SNAP_THRESHOLD ? undefined : clamped;
}

// How far an arrow key resizes an axis, or null for a key that does not. The
// arrow toward the popover's free edge grows it, as a drag of that edge does.
export function keyResizeDelta(
  axis: ResizeAxis,
  key: string,
  shiftKey: boolean,
  direction: ResizeDirection,
): number | null {
  const [forward, backward] = axis === "width" ? ["ArrowRight", "ArrowLeft"] : ["ArrowDown", "ArrowUp"];
  if (key !== forward && key !== backward) return null;
  const step = shiftKey ? KEY_STEP_LARGE : KEY_STEP;
  return (key === forward ? step : -step) * direction[axis];
}
