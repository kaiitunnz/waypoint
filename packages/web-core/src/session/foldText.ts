export interface FoldedText {
  text: string;
  // Lines in the whole text.
  total: number;
  // Lines `text` leaves out.
  hidden: number;
}

// `text` cut to `max` lines, keeping its start or its end; nothing is cut when it fits.
// Blank lines at either end do not count.
export function foldLines(text: string, max: number, keep: "start" | "end"): FoldedText {
  const lines = text.replace(/\r\n?/g, "\n").replace(/^(?:[ \t]*\n)+|\s+$/g, "").split("\n");
  const total = text.trim() === "" ? 0 : lines.length;
  if (total <= max) return { text: total === 0 ? "" : lines.join("\n"), total, hidden: 0 };
  const kept = keep === "start" ? lines.slice(0, max) : lines.slice(total - max);
  return { text: kept.join("\n"), total, hidden: total - max };
}
