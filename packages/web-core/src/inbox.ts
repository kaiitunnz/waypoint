// Inbox logic shared by the apps: list filtering and live updates, block drafts and
// the submit payload, selection, and display strings.

import type { InboxBlockSubmit, InboxListPage } from "./api";
import type {
  InboxApprovalAnswer,
  InboxApprovalBlock,
  InboxAttachmentRef,
  InboxBlock,
  InboxItem,
  InboxQuestionAnswer,
  InboxQuestionBlock,
  InboxStatus,
} from "./types";

export type InboxStatusFilter = InboxStatus | "all";

export const INBOX_PAGE_LIMIT = 30;

export interface InboxFilter {
  status: InboxStatusFilter;
  q: string;
}

// ─── List ───

// The server's search (subject and sender), applied to items that arrive live.
export function matchesInboxFilter(item: InboxItem, filter: InboxFilter): boolean {
  if (filter.status !== "all" && item.status !== filter.status) return false;
  const query = filter.q.trim().toLowerCase();
  if (query) {
    const haystack = `${item.subject} ${item.from_label ?? ""}`.toLowerCase();
    if (!haystack.includes(query)) return false;
  }
  return true;
}

export function byUpdatedDesc(a: InboxItem, b: InboxItem): number {
  if (a.updated_at < b.updated_at) return 1;
  if (a.updated_at > b.updated_at) return -1;
  return 0;
}

export interface InboxUpdate {
  itemId: string;
  deleted: boolean;
  item: InboxItem | null;
  unresolvedCount: number | null;
}

// An `inbox_update` frame payload.
export function parseInboxUpdate(payload: Record<string, unknown>): InboxUpdate {
  const count = payload.unresolved_count;
  return {
    itemId: payload.item_id as string,
    deleted: Boolean(payload.deleted),
    item: (payload.item as InboxItem | null | undefined) ?? null,
    unresolvedCount: typeof count === "number" ? count : null,
  };
}

// A changed item moves to its place; a deleted one, or one that left the filter, goes.
export function applyInboxUpdate(items: InboxItem[], update: InboxUpdate, filter: InboxFilter): InboxItem[] {
  const next = items.filter((item) => item.id !== update.itemId);
  if (!update.deleted && update.item && matchesInboxFilter(update.item, filter)) {
    next.unshift(update.item);
  }
  return next.sort(byUpdatedDesc);
}

export function appendInboxPage(items: InboxItem[], page: InboxItem[]): InboxItem[] {
  const seen = new Set(items.map((item) => item.id));
  return [...items, ...page.filter((item) => !seen.has(item.id))];
}

// The server's page size limit.
const MAX_FETCH = 100;

// As many items as are already shown, read page by page, so a refresh after a
// reconnect keeps what was loaded.
export async function fetchInboxUpTo(
  fetchPage: (cursor: string | null, limit: number) => Promise<InboxListPage>,
  count: number,
): Promise<InboxListPage> {
  const target = Math.max(count, INBOX_PAGE_LIMIT);
  let items: InboxItem[] = [];
  let cursor: string | null = null;
  for (;;) {
    const page: InboxListPage = await fetchPage(cursor, Math.min(MAX_FETCH, target - items.length));
    items = appendInboxPage(items, page.items);
    cursor = page.cursor;
    if (!page.hasMore || !cursor || items.length >= target) return { items, cursor, hasMore: page.hasMore };
  }
}

// The items last changed today, in the local time zone.
export function updatedToday(items: InboxItem[], now: number = Date.now()): InboxItem[] {
  const today = new Date(now).toDateString();
  return items.filter((item) => new Date(item.updated_at).toDateString() === today);
}

// ─── Display ───

export function inboxRelativeTime(value: string, now: number = Date.now()): string {
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "";
  const sec = Math.round((now - then) / 1000);
  if (sec < 45) return "now";
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h`;
  const day = Math.round(hr / 24);
  if (day < 7) return `${day}d`;
  const wk = Math.round(day / 7);
  if (wk < 5) return `${wk}w`;
  return new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

const MONTH_DAY = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" });

// "Today at 08:30", "Yesterday at 17:05", "Nov 3 at 09:00".
export function formatInboxTime(value: string, now: number = Date.now()): string {
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return "";
  const clock = `${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}`;
  const today = new Date(now);
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
  if (at.toDateString() === today.toDateString()) return `Today at ${clock}`;
  if (at.toDateString() === yesterday.toDateString()) return `Yesterday at ${clock}`;
  const day = MONTH_DAY.format(at);
  return `${at.getFullYear() === today.getFullYear() ? day : `${day}, ${at.getFullYear()}`} at ${clock}`;
}

export interface InboxBlockChip {
  key: string;
  label: string;
  // A question or approval of the item still has no answer.
  pending: boolean;
}

export function inboxBlockChips(item: InboxItem): InboxBlockChip[] {
  let questions = 0;
  let questionsPending = 0;
  let approvals = 0;
  let approvalsPending = 0;
  let files = 0;
  for (const block of item.blocks) {
    if (block.type === "question") {
      questions += 1;
      if (block.answer === null) questionsPending += 1;
    } else if (block.type === "approval") {
      approvals += 1;
      if (block.answer === null) approvalsPending += 1;
    } else if (block.type === "attachment") {
      files += 1;
    }
  }
  const chips: InboxBlockChip[] = [];
  const count = (n: number, noun: string) => `${n} ${noun}${n > 1 ? "s" : ""}`;
  if (questions > 0) chips.push({ key: "q", label: count(questions, "question"), pending: questionsPending > 0 });
  if (approvals > 0) chips.push({ key: "a", label: count(approvals, "approval"), pending: approvalsPending > 0 });
  if (files > 0) chips.push({ key: "f", label: count(files, "file"), pending: false });
  return chips;
}

// ─── Blocks ───

export type InboxDecisionBlock = InboxQuestionBlock | InboxApprovalBlock;

export function isDecisionBlock(block: InboxBlock): block is InboxDecisionBlock {
  return block.type === "question" || block.type === "approval";
}

export function isBlockAnswered(block: InboxBlock): boolean {
  return isDecisionBlock(block) && block.answer !== null;
}

// A required decision with no answer keeps the item open.
export function requiredAnswersLeft(item: InboxItem): number {
  return item.blocks.filter((block) => isDecisionBlock(block) && block.required && block.answer === null).length;
}

// What was answered, as one line.
export function blockAnswerEcho(block: InboxBlock): string {
  if (block.type === "approval") return block.answer?.decision ?? "";
  if (block.type === "question" && block.answer) {
    return [...block.answer.selected, ...(block.answer.other ? [block.answer.other] : [])].join(", ");
  }
  return "";
}

export function blockEditLabel(block: InboxBlock): "Answer" | "Edit" | "Reply" {
  if (isDecisionBlock(block) && block.answer === null) return "Answer";
  return block.reply != null || isBlockAnswered(block) ? "Edit" : "Reply";
}

// ─── Block drafts ───

export interface BlockDraft {
  selected: Set<string>;
  other: string;
  decision: string | null;
  notes: string;
}

// The committed answer and reply, which an edit starts from.
export function blockDraft(block: InboxBlock): BlockDraft {
  return {
    selected: new Set(block.type === "question" ? (block.answer?.selected ?? []) : []),
    other: block.type === "question" ? (block.answer?.other ?? "") : "",
    decision: block.type === "approval" ? (block.answer?.decision ?? null) : null,
    notes: block.reply?.notes ?? "",
  };
}

// Multi-select toggles; single-select replaces, and tapping the one pick clears it.
export function toggleBlockOption(selected: ReadonlySet<string>, label: string, multi: boolean): Set<string> {
  const next = new Set(selected);
  if (multi) {
    if (next.has(label)) next.delete(label);
    else next.add(label);
  } else if (next.has(label) && next.size === 1) {
    next.clear();
  } else {
    next.clear();
    next.add(label);
  }
  return next;
}

// The answer in the draft, or undefined when there is none: an answer is never sent
// empty, and omitting it leaves the committed one untouched.
export function buildBlockAnswer(
  block: InboxBlock,
  draft: Pick<BlockDraft, "selected" | "other" | "decision">,
): InboxQuestionAnswer | InboxApprovalAnswer | undefined {
  if (block.type === "question") {
    if (draft.selected.size === 0 && !draft.other.trim()) return undefined;
    return { selected: Array.from(draft.selected), other: draft.other.trim() || null };
  }
  if (block.type === "approval") {
    return draft.decision ? { decision: draft.decision } : undefined;
  }
  return undefined;
}

export interface BlockSubmitInput {
  draft: BlockDraft;
  // Files uploaded for this edit.
  uploaded: InboxAttachmentRef[];
  // The committed reply's files, which an edit keeps.
  carried: InboxAttachmentRef[];
}

// The request body for a save, or null when there is nothing to save. A submit
// replaces the block's single reply, so the carried files travel with it.
export function buildBlockSubmit(block: InboxBlock, { draft, uploaded, carried }: BlockSubmitInput): InboxBlockSubmit | null {
  const answer = buildBlockAnswer(block, draft);
  const notes = draft.notes.trim();
  const hasReply = notes.length > 0 || uploaded.length > 0;
  if (!answer && !hasReply) return null;
  const body: InboxBlockSubmit = {};
  if (answer) body.answer = answer;
  if (hasReply) body.reply = { notes: notes || null, attachments: [...carried, ...uploaded] };
  return body;
}

// A save waits out uploads and an earlier save; with nothing to send it does nothing.
export function blockSaveAction(state: { submitting: boolean; uploading: boolean; canSave: boolean }): "wait" | "nothing" | "save" {
  if (state.submitting || state.uploading) return "wait";
  return state.canSave ? "save" : "nothing";
}

// ─── Selection ───

export function toggleId(selected: ReadonlySet<string>, id: string): Set<string> {
  const next = new Set(selected);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

// Drops ids that are no longer listed; the same set when nothing dropped.
export function pruneSelection(selected: Set<string>, listed: readonly string[]): Set<string> {
  if (selected.size === 0) return selected;
  const present = new Set(listed);
  const next = new Set([...selected].filter((id) => present.has(id)));
  return next.size === selected.size ? selected : next;
}

export function toggleAll(selected: ReadonlySet<string>, listed: readonly string[]): Set<string> {
  return selected.size === listed.length ? new Set() : new Set(listed);
}

export type SelectAllState = "all" | "some" | "none";

export function selectAllState(selected: ReadonlySet<string>, listed: readonly string[]): SelectAllState {
  if (listed.length > 0 && selected.size === listed.length) return "all";
  return selected.size > 0 ? "some" : "none";
}
