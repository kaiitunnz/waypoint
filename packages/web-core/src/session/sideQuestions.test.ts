import { describe, expect, it } from "vitest";

import type { SideQuestion } from "../types";
import {
  applySideQuestionFrame,
  EMPTY_SIDE_QUESTIONS,
  sideQuestionStripLabel,
  sortedSideQuestions,
} from "./sideQuestions";

const aside = (overrides: Partial<SideQuestion> = {}): SideQuestion => ({
  id: "q1",
  question: "why?",
  status: "answered",
  answer: "because",
  attempts: 1,
  resumed: false,
  created_at: "2026-01-01T00:00:00Z",
  ...overrides,
});

describe("applySideQuestionFrame", () => {
  it("expands on a live first arrival only", () => {
    const first = applySideQuestionFrame(EMPTY_SIDE_QUESTIONS, { side_question: aside() });
    expect(first.expand).toBe(true);
    expect(first.state.items.get("q1")?.answer).toBe("because");
    const update = applySideQuestionFrame(first.state, { side_question: aside({ answer: "more" }) });
    expect(update.expand).toBe(false);
    expect(update.state.items.get("q1")?.answer).toBe("more");
  });

  it("never expands for a hydrated aside, even on its later updates", () => {
    const hydrated = applySideQuestionFrame(EMPTY_SIDE_QUESTIONS, { side_question: aside(), hydrated: true });
    expect(hydrated.expand).toBe(false);
    expect(applySideQuestionFrame(hydrated.state, { side_question: aside({ status: "pending" }) }).expand).toBe(false);
  });

  it("removes by id and ignores unknown removals", () => {
    const { state } = applySideQuestionFrame(EMPTY_SIDE_QUESTIONS, { side_question: aside() });
    expect(applySideQuestionFrame(state, { removed_id: "q1" }).state.items.size).toBe(0);
    expect(applySideQuestionFrame(state, { removed_id: "nope" }).state).toBe(state);
  });
});

describe("side question ordering and label", () => {
  it("sorts newest first and labels by the latest question", () => {
    const sorted = sortedSideQuestions([
      aside({ id: "old", question: "old", created_at: "2026-01-01T00:00:00Z" }),
      aside({ id: "new", question: "new", created_at: "2026-01-02T00:00:00Z" }),
    ]);
    expect(sorted.map((q) => q.id)).toEqual(["new", "old"]);
    expect(sideQuestionStripLabel(sorted)).toBe("new");
  });

  it("reads Asking… while one is pending", () => {
    expect(sideQuestionStripLabel([aside(), aside({ id: "p", status: "pending" })])).toBe("Asking…");
  });
});
