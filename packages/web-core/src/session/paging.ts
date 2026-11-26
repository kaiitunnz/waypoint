// A pager over a list that can shrink under it: the index is clamped on every read.
export interface Page<T> {
  count: number;
  page: number;
  current: T | null;
}

export function pageOf<T>(items: readonly T[], index: number): Page<T> {
  const count = items.length;
  const page = count > 0 ? Math.min(index, count - 1) : 0;
  return { count, page, current: items[page] ?? null };
}

export function stepPage(index: number, direction: "prev" | "next", count: number): number {
  return direction === "prev" ? Math.max(0, index - 1) : Math.min(count - 1, index + 1);
}
