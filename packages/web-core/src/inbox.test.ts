import { describe, expect, it } from "vitest";

import {
  appendInboxPage,
  applyInboxUpdate,
  blockAnswerEcho,
  blockDraft,
  blockEditLabel,
  blockSaveAction,
  fetchInboxUpTo,
  buildBlockAnswer,
  buildBlockSubmit,
  formatInboxTime,
  inboxBlockChips,
  inboxRelativeTime,
  matchesInboxFilter,
  parseInboxUpdate,
  pruneSelection,
  requiredAnswersLeft,
  selectAllState,
  toggleAll,
  toggleBlockOption,
  toggleId,
  updatedToday,
} from "./inbox";
import type { InboxApprovalBlock, InboxBlock, InboxItem, InboxQuestionBlock } from "./types";

const question = (over: Partial<InboxQuestionBlock> = {}): InboxQuestionBlock => ({
  id: "q1",
  type: "question",
  header: null,
  question: "Which?",
  options: [{ label: "A" }, { label: "B" }],
  multi: false,
  required: true,
  answer: null,
  answered_at: null,
  reply: null,
  ...over,
});

const approval = (over: Partial<InboxApprovalBlock> = {}): InboxApprovalBlock => ({
  id: "a1",
  type: "approval",
  prompt: "Ship it?",
  options: ["Approve", "Reject"],
  required: true,
  answer: null,
  answered_at: null,
  reply: null,
  ...over,
});

const markdown: InboxBlock = { id: "m1", type: "markdown", text: "hi", reply: null };
const file: InboxBlock = { id: "f1", type: "attachment", ref: { session_id: "s", attachment_id: "x" }, reply: null };

const item = (id: string, over: Partial<InboxItem> = {}): InboxItem => ({
  id,
  from_session_id: "s",
  from_label: "Manager",
  subject: `Subject ${id}`,
  status: "open",
  read_at: null,
  version: 1,
  created_at: "2026-11-27T08:00:00Z",
  updated_at: "2026-11-27T08:00:00Z",
  blocks: [],
  ...over,
});

describe("matchesInboxFilter", () => {
  it("checks status and searches subject and sender", () => {
    expect(matchesInboxFilter(item("1"), { status: "open", q: "" })).toBe(true);
    expect(matchesInboxFilter(item("1"), { status: "resolved", q: "" })).toBe(false);
    expect(matchesInboxFilter(item("1"), { status: "all", q: " MANAG " })).toBe(true);
    expect(matchesInboxFilter(item("1"), { status: "all", q: "subject 1" })).toBe(true);
    expect(matchesInboxFilter(item("1"), { status: "all", q: "other" })).toBe(false);
  });
});

describe("applyInboxUpdate", () => {
  const filter = { status: "open" as const, q: "" };
  const older = item("1", { updated_at: "2026-11-27T07:00:00Z" });
  const newer = item("2", { updated_at: "2026-11-27T09:00:00Z" });

  it("upserts a matching item in updated order", () => {
    const changed = { ...older, updated_at: "2026-11-27T10:00:00Z" };
    const next = applyInboxUpdate([newer, older], { itemId: "1", deleted: false, item: changed, unresolvedCount: 2 }, filter);
    expect(next.map((entry) => entry.id)).toEqual(["1", "2"]);
  });

  it("removes a deleted item and one that left the filter", () => {
    expect(applyInboxUpdate([newer, older], { itemId: "1", deleted: true, item: null, unresolvedCount: 1 }, filter)).toEqual([newer]);
    const resolved = { ...older, status: "resolved" as const };
    expect(applyInboxUpdate([newer, older], { itemId: "1", deleted: false, item: resolved, unresolvedCount: 1 }, filter)).toEqual([newer]);
  });

  it("parses a frame payload", () => {
    expect(parseInboxUpdate({ item_id: "1", deleted: 1, unresolved_count: 3 })).toEqual({
      itemId: "1",
      deleted: true,
      item: null,
      unresolvedCount: 3,
    });
    expect(parseInboxUpdate({ item_id: "1", item: older }).item).toBe(older);
  });

  it("appends a page without repeating items", () => {
    expect(appendInboxPage([newer], [newer, older]).map((entry) => entry.id)).toEqual(["2", "1"]);
  });
});

describe("fetchInboxUpTo", () => {
  const all = Array.from({ length: 75 }, (_, index) => item(String(index)));
  const pages: [string | null, number][] = [];
  const fetchPage = async (cursor: string | null, limit: number) => {
    pages.push([cursor, limit]);
    const start = cursor ? Number(cursor) : 0;
    const end = Math.min(all.length, start + limit);
    return { items: all.slice(start, end), hasMore: end < all.length, cursor: end < all.length ? String(end) : null };
  };

  it("reads pages until it holds as many items as before", async () => {
    pages.length = 0;
    const result = await fetchInboxUpTo(fetchPage, 60);
    expect(result.items).toHaveLength(60);
    expect(result.hasMore).toBe(true);
    expect(result.cursor).toBe("60");
    expect(pages).toEqual([[null, 60]]);
  });

  it("reads at least a page and stops at the end", async () => {
    pages.length = 0;
    expect((await fetchInboxUpTo(fetchPage, 0)).items).toHaveLength(30);
    expect((await fetchInboxUpTo(fetchPage, 500)).items).toHaveLength(75);
    expect(pages.slice(1)).toEqual([
      [null, 100],
    ]);
  });
});

describe("blockSaveAction", () => {
  it("waits out uploads and saves, and never sends nothing", () => {
    expect(blockSaveAction({ submitting: true, uploading: false, canSave: true })).toBe("wait");
    expect(blockSaveAction({ submitting: false, uploading: true, canSave: false })).toBe("wait");
    expect(blockSaveAction({ submitting: false, uploading: false, canSave: false })).toBe("nothing");
    expect(blockSaveAction({ submitting: false, uploading: false, canSave: true })).toBe("save");
  });
});

describe("display", () => {
  const now = new Date(2026, 10, 27, 12, 0).getTime();
  const at = (y: number, m: number, d: number, h = 9, min = 5) => new Date(y, m, d, h, min).toISOString();

  it("formats relative times", () => {
    expect(inboxRelativeTime(new Date(now - 10_000).toISOString(), now)).toBe("now");
    expect(inboxRelativeTime(new Date(now - 5 * 60_000).toISOString(), now)).toBe("5m");
    expect(inboxRelativeTime(new Date(now - 3 * 3_600_000).toISOString(), now)).toBe("3h");
    expect(inboxRelativeTime(new Date(now - 2 * 86_400_000).toISOString(), now)).toBe("2d");
    expect(inboxRelativeTime(new Date(now - 14 * 86_400_000).toISOString(), now)).toBe("2w");
    expect(inboxRelativeTime("nope", now)).toBe("");
  });

  it("formats an item's time by day", () => {
    expect(formatInboxTime(at(2026, 10, 27), now)).toBe("Today at 09:05");
    expect(formatInboxTime(at(2026, 10, 26), now)).toBe("Yesterday at 09:05");
    expect(formatInboxTime(at(2026, 10, 3), now)).toBe("Nov 3 at 09:05");
    expect(formatInboxTime(at(2025, 10, 3), now)).toBe("Nov 3, 2025 at 09:05");
  });

  it("lists items changed today", () => {
    const today = item("1", { updated_at: at(2026, 10, 27, 1) });
    const before = item("2", { updated_at: at(2026, 10, 26, 23) });
    expect(updatedToday([today, before], now)).toEqual([today]);
  });

  it("counts blocks and marks pending decisions", () => {
    const chips = inboxBlockChips(
      item("1", { blocks: [question(), question({ id: "q2" }), approval({ answer: { decision: "Approve" } }), file, markdown] }),
    );
    expect(chips).toEqual([
      { key: "q", label: "2 questions", pending: true },
      { key: "a", label: "1 approval", pending: false },
      { key: "f", label: "1 file", pending: false },
    ]);
  });

  it("counts required answers left", () => {
    const blocks = [question(), question({ id: "q2", required: false }), approval({ answer: { decision: "Approve" } })];
    expect(requiredAnswersLeft(item("1", { blocks }))).toBe(1);
  });

  it("echoes answers and labels the edit action", () => {
    const answered = question({ answer: { selected: ["A", "B"], other: "also C" } });
    expect(blockAnswerEcho(answered)).toBe("A, B, also C");
    expect(blockAnswerEcho(approval({ answer: { decision: "Reject" } }))).toBe("Reject");
    expect(blockAnswerEcho(markdown)).toBe("");
    expect(blockEditLabel(question())).toBe("Answer");
    expect(blockEditLabel(answered)).toBe("Edit");
    expect(blockEditLabel(markdown)).toBe("Reply");
    expect(blockEditLabel({ ...markdown, reply: { notes: "x", attachments: [], created_at: "" } })).toBe("Edit");
  });
});

describe("block drafts", () => {
  const ref = (id: string) => ({ session_id: "s", attachment_id: id });

  it("starts from the committed answer and reply", () => {
    const draft = blockDraft(
      question({ answer: { selected: ["A"], other: "x" }, reply: { notes: "n", attachments: [], created_at: "" } }),
    );
    expect([...draft.selected]).toEqual(["A"]);
    expect(draft.other).toBe("x");
    expect(draft.notes).toBe("n");
    expect(blockDraft(approval({ answer: { decision: "Approve" } })).decision).toBe("Approve");
  });

  it("toggles single and multi selections", () => {
    expect([...toggleBlockOption(new Set(["A"]), "B", false)]).toEqual(["B"]);
    expect([...toggleBlockOption(new Set(["A"]), "A", false)]).toEqual([]);
    expect([...toggleBlockOption(new Set(["A"]), "B", true)]).toEqual(["A", "B"]);
    expect([...toggleBlockOption(new Set(["A", "B"]), "A", true)]).toEqual(["B"]);
  });

  it("never builds an empty answer", () => {
    expect(buildBlockAnswer(question(), { selected: new Set(), other: "  ", decision: null })).toBeUndefined();
    expect(buildBlockAnswer(question(), { selected: new Set(), other: " c ", decision: null })).toEqual({ selected: [], other: "c" });
    expect(buildBlockAnswer(approval(), { selected: new Set(), other: "", decision: null })).toBeUndefined();
    expect(buildBlockAnswer(markdown, { selected: new Set(["A"]), other: "", decision: null })).toBeUndefined();
  });

  it("builds one body for the answer and the reply, keeping earlier files", () => {
    const draft = { ...blockDraft(question()), selected: new Set(["A"]), notes: " note " };
    expect(buildBlockSubmit(question(), { draft, uploaded: [ref("2")], carried: [ref("1")] })).toEqual({
      answer: { selected: ["A"], other: null },
      reply: { notes: "note", attachments: [ref("1"), ref("2")] },
    });
  });

  it("omits the reply when only the answer changed, and is null with nothing to save", () => {
    const draft = { ...blockDraft(approval()), decision: "Approve" };
    expect(buildBlockSubmit(approval(), { draft, uploaded: [], carried: [ref("1")] })).toEqual({ answer: { decision: "Approve" } });
    expect(buildBlockSubmit(markdown, { draft: blockDraft(markdown), uploaded: [], carried: [] })).toBeNull();
  });
});

describe("selection", () => {
  it("toggles, prunes and selects all of the listed ids", () => {
    expect([...toggleId(new Set(["a"]), "b")]).toEqual(["a", "b"]);
    expect([...toggleId(new Set(["a"]), "a")]).toEqual([]);
    const selected = new Set(["a", "b"]);
    expect(pruneSelection(selected, ["a", "b", "c"])).toBe(selected);
    expect([...pruneSelection(selected, ["b"])]).toEqual(["b"]);
    expect([...toggleAll(new Set(), ["a", "b"])]).toEqual(["a", "b"]);
    expect([...toggleAll(new Set(["a", "b"]), ["a", "b"])]).toEqual([]);
    expect(selectAllState(new Set(["a", "b"]), ["a", "b"])).toBe("all");
    expect(selectAllState(new Set(["a"]), ["a", "b"])).toBe("some");
    expect(selectAllState(new Set(), [])).toBe("none");
  });
});
