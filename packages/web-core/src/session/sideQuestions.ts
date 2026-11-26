import type { SideQuestion } from "../types";

export interface SideQuestionState {
  items: Map<string, SideQuestion>;
  // Every id already seen, hydrated ones included, so a later update to a
  // rehydrated aside never counts as a first arrival.
  seen: Set<string>;
}

export const EMPTY_SIDE_QUESTIONS: SideQuestionState = { items: new Map(), seen: new Set() };

export interface SideQuestionFrame {
  side_question?: SideQuestion;
  removed_id?: string;
  hydrated?: boolean;
}

// Applies one `side_question` frame. `expand` is true when a live (not
// hydrated) aside first arrives, which opens its dock.
export function applySideQuestionFrame(
  state: SideQuestionState,
  frame: SideQuestionFrame,
): { state: SideQuestionState; expand: boolean } {
  if (frame.side_question) {
    const question = frame.side_question;
    const items = new Map(state.items).set(question.id, question);
    if (state.seen.has(question.id)) return { state: { items, seen: state.seen }, expand: false };
    const seen = new Set(state.seen).add(question.id);
    return { state: { items, seen }, expand: !frame.hydrated };
  }
  if (frame.removed_id && state.items.has(frame.removed_id)) {
    const items = new Map(state.items);
    items.delete(frame.removed_id);
    return { state: { items, seen: state.seen }, expand: false };
  }
  return { state, expand: false };
}

export function sortedSideQuestions(questions: Iterable<SideQuestion>): SideQuestion[] {
  return [...questions].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
}

// The strip reads "Asking…" while any aside is still being answered, else the latest question.
export function sideQuestionStripLabel(sorted: SideQuestion[]): string {
  if (sorted.some((question) => question.status === "pending")) return "Asking…";
  return sorted[0]?.question ?? "";
}
