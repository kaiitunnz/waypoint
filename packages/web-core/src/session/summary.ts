// A tool call's summary: one line of prose with backticks around its machine strings.

export interface SummaryPart {
  text: string;
  code: boolean;
}

// An unpaired backtick leaves the whole line as written.
export function summaryParts(summary: string): SummaryPart[] {
  const pieces = summary.split("`");
  if (pieces.length % 2 === 0) return [{ text: summary, code: false }];
  return pieces.map((text, index) => ({ text, code: index % 2 === 1 })).filter((part) => part.text !== "");
}

// The summary as a label that takes no markup reads it.
export const plainSummary = (summary: string): string => summaryParts(summary).map((part) => part.text).join("");
