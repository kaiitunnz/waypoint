import { describe, expect, it } from "vitest";

import { ev } from "../test-helpers";
import type { PendingQuestionsSnapshot } from "../types";
import {
  EMPTY_ASK_DRAFT,
  askAnswerEntries,
  askCardState,
  askFormState,
  askToolUseId,
  indexAskQuestionEvidence,
  mergePendingQuestionSnapshot,
  parseAskUserQuestion,
  resolveAskQuestion,
  serializeAnswers,
  toggleLabel,
  type AskDraft,
  type AskUserQuestion,
  askFormView,
  clearAskAnswers,
  pendingQuestionPrompt,
  pendingQuestionsCountLabel,
  setAskNote,
  setAskNoteOpen,
  stepAskQuestion,
} from "./askQuestion";

const askEvent = (questions: unknown, toolName = "AskUserQuestion") =>
  ev(1, "tool_call", "", { tool_name: toolName, payload: { input: { questions } } });

describe("parseAskUserQuestion", () => {
  it("parses questions with options, headers and multiSelect", () => {
    const parsed = parseAskUserQuestion(
      askEvent([
        {
          question: "Which?",
          header: "Pick",
          multiSelect: true,
          options: [{ label: "A", description: "first" }, { label: "B" }],
        },
      ]),
    );
    expect(parsed).toEqual([
      {
        question: "Which?",
        header: "Pick",
        options: [{ label: "A", description: "first" }, { label: "B", description: undefined }],
        multiSelect: true,
      },
    ]);
  });

  it("accepts tool name aliases", () => {
    expect(parseAskUserQuestion(askEvent([{ question: "q" }], "askuserquestion"))).toHaveLength(1);
    expect(parseAskUserQuestion(askEvent([{ question: "q" }], "default_api:AskUserQuestion"))).toHaveLength(1);
  });

  it("returns null for other tools or a missing tool name", () => {
    expect(parseAskUserQuestion(askEvent([{ question: "q" }], "Bash"))).toBeNull();
    expect(parseAskUserQuestion(ev(1, "tool_call", "", {}))).toBeNull();
  });

  it("returns null when the payload has no usable questions", () => {
    expect(parseAskUserQuestion(ev(1, "tool_call", "", { tool_name: "AskUserQuestion" }))).toBeNull();
    expect(parseAskUserQuestion(askEvent([]))).toBeNull();
    expect(parseAskUserQuestion(askEvent("nope"))).toBeNull();
    expect(parseAskUserQuestion(askEvent([null, 3, { header: "no question" }]))).toBeNull();
  });

  it("skips malformed questions and options and defaults the rest", () => {
    const parsed = parseAskUserQuestion(
      askEvent([
        null,
        { question: "kept", options: [null, 5, { description: "no label" }, { label: "ok", description: 7 }] },
        { question: "no options", multiSelect: "true", header: 3 },
      ]),
    );
    expect(parsed).toEqual([
      { question: "kept", header: undefined, options: [{ label: "ok", description: undefined }], multiSelect: false },
      { question: "no options", header: undefined, options: [], multiSelect: false },
    ]);
  });
});

describe("askToolUseId", () => {
  it("returns a non-empty string tool_use_id", () => {
    expect(askToolUseId(ev(1, "tool_call", "", { tool_use_id: "t1" }))).toBe("t1");
    expect(askToolUseId(ev(1, "tool_call", "", { tool_use_id: "" }))).toBeNull();
    expect(askToolUseId(ev(1, "tool_call", "", { tool_use_id: 4 }))).toBeNull();
    expect(askToolUseId(ev(1, "tool_call", "", {}))).toBeNull();
  });
});

describe("resolveAskQuestion", () => {
  const answer = (seq: number, id: string) =>
    ev(seq, "user_input", "a", { kind: "ask_user_question_answer", tool_use_id: id });
  const cancel = (seq: number, id: string) =>
    ev(seq, "system_note", "c", { kind: "ask_user_question_cancelled", tool_use_id: id });
  const closed = (seq: number, id: string) =>
    ev(seq, "system_note", "x", { kind: "ask_user_question_closed", tool_use_id: id });
  const toolResult = (seq: number, id: string) =>
    ev(seq, "tool_result", "r", { tool_use_id: id });

  const resolve = (id: string, events: ReturnType<typeof ev>[]) =>
    resolveAskQuestion(id, indexAskQuestionEvidence(events));

  it("is pending without evidence", () => {
    expect(resolve("t", [])).toEqual({ state: "pending" });
    expect(resolve("t", [answer(1, "other")])).toEqual({ state: "pending" });
  });

  it("resolves from an answer, a cancellation, or a provider ending", () => {
    expect(resolve("t", [answer(1, "t")])).toMatchObject({ state: "answered" });
    expect(resolve("t", [cancel(1, "t")])).toMatchObject({ state: "cancelled" });
    expect(resolve("t", [closed(1, "t")])).toMatchObject({ state: "closed_unanswered" });
    expect(resolve("t", [toolResult(1, "t")])).toMatchObject({ state: "closed_unanswered" });
  });

  it("prefers an answer over a cancellation over an ending, whatever the order", () => {
    const all = [toolResult(1, "t"), cancel(2, "t"), answer(3, "t")];
    expect(resolve("t", all)).toMatchObject({ state: "answered" });
    expect(resolve("t", [toolResult(1, "t"), cancel(2, "t")])).toMatchObject({ state: "cancelled" });
  });

  it("keeps the earliest event of each kind", () => {
    const state = resolve("t", [answer(5, "t"), answer(2, "t")]);
    expect(state).toMatchObject({ state: "answered", answerEvent: { sequence: 2 } });
  });

  it("ignores evidence without a tool_use_id or with the wrong shape", () => {
    const noId = ev(1, "user_input", "a", { kind: "ask_user_question_answer" });
    const wrongKind = ev(2, "user_input", "a", { kind: "other", tool_use_id: "t" });
    const wrongNote = ev(3, "system_note", "n", { kind: "something", tool_use_id: "t" });
    expect(resolve("t", [noId, wrongKind, wrongNote])).toEqual({ state: "pending" });
  });
});

describe("askFormState", () => {
  const snapshot = (asOf: number, ids: [string, string][] = []): PendingQuestionsSnapshot =>
    ({
      as_of_sequence: asOf,
      revision: 1,
      questions: ids.map(([tool_use_id, availability]) => ({ tool_use_id, availability })),
    }) as unknown as PendingQuestionsSnapshot;
  const event = (seq: number, id?: string) =>
    ev(seq, "tool_call", "", id === undefined ? {} : { tool_use_id: id });
  const pending = { state: "pending" } as const;

  it("is resolved once the question has resolution evidence", () => {
    expect(askFormState(null, event(1, "t"), { state: "answered", answerEvent: event(2) })).toBe("resolved");
    expect(askFormState(snapshot(9), event(1, "t"), { state: "cancelled", cancelEvent: event(2) })).toBe("resolved");
  });

  it("is loading until the first snapshot arrives", () => {
    expect(askFormState(null, event(1, "t"), pending)).toBe("loading");
  });

  it("reports the availability the snapshot lists for the question", () => {
    expect(askFormState(snapshot(9, [["t", "actionable"]]), event(1, "t"), pending)).toBe("actionable");
    expect(askFormState(snapshot(9, [["t", "starting"]]), event(1, "t"), pending)).toBe("starting");
  });

  it("is starting when the snapshot predates the question and gone when it already covers it", () => {
    expect(askFormState(snapshot(5), event(6, "t"), pending)).toBe("starting");
    expect(askFormState(snapshot(5), event(5, "t"), pending)).toBe("gone");
    expect(askFormState(snapshot(5), event(2, "t"), pending)).toBe("gone");
  });

  it("falls back on the sequence rule when the event has no tool_use_id", () => {
    expect(askFormState(snapshot(5, [["t", "actionable"]]), event(6), pending)).toBe("starting");
    expect(askFormState(snapshot(5, [["t", "actionable"]]), event(4), pending)).toBe("gone");
  });
});

describe("mergePendingQuestionSnapshot", () => {
  const snap = (as_of_sequence: number, revision: number) =>
    ({ as_of_sequence, revision, questions: [] }) as unknown as PendingQuestionsSnapshot;

  it("takes the first snapshot and any replacing snapshot outright", () => {
    const next = snap(1, 1);
    expect(mergePendingQuestionSnapshot(null, next, false)).toBe(next);
    expect(mergePendingQuestionSnapshot(snap(9, 9), next, true)).toBe(next);
  });

  it("orders by as_of_sequence first", () => {
    const current = snap(5, 3);
    expect(mergePendingQuestionSnapshot(current, snap(6, 0), false)).toMatchObject({ as_of_sequence: 6 });
    expect(mergePendingQuestionSnapshot(current, snap(4, 99), false)).toBe(current);
  });

  it("then by revision, accepting an equal revision", () => {
    const current = snap(5, 3);
    expect(mergePendingQuestionSnapshot(current, snap(5, 4), false)).toMatchObject({ revision: 4 });
    const equal = snap(5, 3);
    expect(mergePendingQuestionSnapshot(current, equal, false)).toBe(equal);
    expect(mergePendingQuestionSnapshot(current, snap(5, 2), false)).toBe(current);
  });
});

describe("toggleLabel", () => {
  it("replaces the selection for a single-select question and clears it on a second pick", () => {
    const one = toggleLabel(EMPTY_ASK_DRAFT, 0, "A", false);
    expect(one.picked).toEqual({ 0: ["A"] });
    expect(toggleLabel(one, 0, "B", false).picked).toEqual({ 0: ["B"] });
    expect(toggleLabel(one, 0, "A", false).picked).toEqual({ 0: [] });
  });

  it("adds and removes labels for a multi-select question", () => {
    let draft = toggleLabel(EMPTY_ASK_DRAFT, 1, "A", true);
    draft = toggleLabel(draft, 1, "B", true);
    expect(draft.picked).toEqual({ 1: ["A", "B"] });
    expect(toggleLabel(draft, 1, "A", true).picked).toEqual({ 1: ["B"] });
  });

  it("leaves other questions and the rest of the draft untouched", () => {
    const draft: AskDraft = { ...EMPTY_ASK_DRAFT, picked: { 0: ["X"] }, notes: { 0: "n" }, activeIndex: 2 };
    const next = toggleLabel(draft, 1, "A", false);
    expect(next).toEqual({ ...draft, picked: { 0: ["X"], 1: ["A"] } });
    expect(draft.picked).toEqual({ 0: ["X"] });
  });
});

describe("serializeAnswers", () => {
  const q = (question: string, labels: string[] = []): AskUserQuestion => ({
    question,
    options: labels.map((label) => ({ label })),
  });
  const draft = (picked: AskDraft["picked"], notes: AskDraft["notes"] = {}): AskDraft => ({
    ...EMPTY_ASK_DRAFT,
    picked,
    notes,
  });

  it("returns null when nothing is answered", () => {
    expect(serializeAnswers([q("a", ["x"])], EMPTY_ASK_DRAFT)).toBeNull();
    expect(serializeAnswers([q("a", ["x"])], draft({ 0: [] }, { 0: "   " }))).toBeNull();
    expect(serializeAnswers([], EMPTY_ASK_DRAFT)).toBeNull();
  });

  it("serialises a selection in the Claude tool-result shape", () => {
    expect(serializeAnswers([q("Pick", ["A", "B"])], draft({ 0: ["A"] }))).toEqual({
      text: '"Pick"="A"',
      answers: [{ question: "Pick", answer: "A", notes: undefined }],
    });
  });

  it("joins multiple selections and appends trimmed notes", () => {
    expect(serializeAnswers([q("Pick", ["A", "B"])], draft({ 0: ["A", "B"] }, { 0: "  because  " }))).toEqual({
      text: '"Pick"="A, B" user notes: because',
      answers: [{ question: "Pick", answer: "A, B", notes: "because" }],
    });
  });

  it("records a notes-only answer on a question with options", () => {
    expect(serializeAnswers([q("Pick", ["A"])], draft({}, { 0: "my own" }))).toEqual({
      text: '"Pick"=(no option selected) user notes: my own',
      answers: [{ question: "Pick", answer: null, notes: "my own" }],
    });
  });

  it("uses the note as the answer for a free-text question", () => {
    expect(serializeAnswers([q("Why?")], draft({}, { 0: " because " }))).toEqual({
      text: '"Why?"="because"',
      answers: [{ question: "Why?", answer: "because" }],
    });
  });

  it("joins several answered questions with commas and skips unanswered ones", () => {
    const result = serializeAnswers(
      [q("one", ["A"]), q("two", ["B"]), q("three", ["C"])],
      draft({ 0: ["A"], 2: ["C"] }),
    );
    expect(result?.text).toBe('"one"="A", "three"="C"');
    expect(result?.answers.map((a) => a.question)).toEqual(["one", "three"]);
  });
});

describe("askAnswerEntries", () => {
  it("keeps well-formed answers", () => {
    const event = ev(1, "user_input", "", {
      answers: [{ question: "Q", answer: "A", notes: "n" }, { question: "Q2" }, { answer: "orphan" }, null],
    });
    expect(askAnswerEntries(event)).toEqual([
      { question: "Q", answer: "A", notes: "n" },
      { question: "Q2", answer: null, notes: undefined },
    ]);
    expect(askAnswerEntries(ev(2, "user_input", "raw"))).toEqual([]);
  });
});

describe("askCardState", () => {
  const ask = ev(1, "tool_call", "", { tool_name: "AskUserQuestion", tool_use_id: "q1", payload: { input: { questions: [{ question: "Q?" }] } } });
  const answer = ev(2, "user_input", "A", {
    kind: "ask_user_question_answer",
    tool_use_id: "q1",
    answers: [{ question: "Q?", answer: "A" }],
  });
  const cancelled = ev(3, "system_note", "x", { kind: "ask_user_question_cancelled", tool_use_id: "q1" });
  const state = (events: ReturnType<typeof ev>[], snapshot: PendingQuestionsSnapshot | null = null) =>
    askCardState(ask, indexAskQuestionEvidence(events), snapshot);

  it("waits while nothing has answered it", () => {
    expect(state([ask])).toMatchObject({ waiting: true, outcome: null, answers: [] });
  });

  it("is answered, with the answers", () => {
    expect(state([ask, answer])).toMatchObject({
      waiting: false,
      outcome: "answered",
      answers: [{ question: "Q?", answer: "A" }],
    });
  });

  it("is cancelled", () => {
    expect(state([ask, cancelled])).toMatchObject({ waiting: false, outcome: "cancelled" });
  });

  it("is not answered once a snapshot that covers it no longer lists it", () => {
    const snapshot = { as_of_sequence: 10, revision: 1, questions: [] } as unknown as PendingQuestionsSnapshot;
    expect(state([ask], snapshot)).toMatchObject({ waiting: false, outcome: "not answered" });
  });
});

describe("answer form helpers", () => {
  const questions = [
    { question: "Which?", options: [{ label: "A (Recommended)" }, { label: "B" }] },
    { question: "Why?", options: [] },
  ];

  it("reads the form's page, fill and free-text state", () => {
    let draft = { ...EMPTY_ASK_DRAFT, activeIndex: 5 };
    let view = askFormView(questions, draft);
    expect(view.index).toBe(1);
    expect(view.freeText).toBe(true);
    expect(view.paginated).toBe(true);
    expect(view.hasAnswer).toBe(false);
    draft = setAskNote(draft, 1, "  because ");
    view = askFormView(questions, draft);
    expect(view.filled).toEqual([false, true]);
    expect(view.hasAnswer).toBe(true);
  });

  it("steps within the questions and clears answers but keeps the page", () => {
    let draft = stepAskQuestion(EMPTY_ASK_DRAFT, "next", 2);
    expect(draft.activeIndex).toBe(1);
    expect(stepAskQuestion(draft, "next", 2).activeIndex).toBe(1);
    expect(stepAskQuestion(draft, "prev", 2).activeIndex).toBe(0);
    draft = setAskNoteOpen(setAskNote(draft, 0, "n"), 0, true);
    const cleared = clearAskAnswers({ ...draft, picked: { 0: ["B"] } });
    expect(cleared).toEqual({ ...draft, picked: {}, notes: {} });
  });

  it("heads a waiting question by the prompt on its page", () => {
    const event = askEvent([
      { question: "First?", options: [] },
      { question: "Second?", options: [] },
    ]);
    expect(pendingQuestionPrompt({ tool_use_id: "t", event } as never)).toBe("First?");
    expect(pendingQuestionPrompt({ tool_use_id: "t", event } as never, 1)).toBe("Second?");
    expect(pendingQuestionPrompt({ tool_use_id: "t", event } as never, 5)).toBe("Second?");
    expect(pendingQuestionsCountLabel(1)).toBe("Question waiting");
    expect(pendingQuestionsCountLabel(3)).toBe("3 questions waiting");
  });
});
