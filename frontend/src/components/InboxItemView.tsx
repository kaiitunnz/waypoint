"use client";

import Link from "next/link";
import { useRef, useState, type ClipboardEvent, type DragEvent } from "react";

import {
  AttachmentTray,
  filesFromDataTransfer,
  PaperclipIcon,
  useAttachments,
} from "@/components/AttachmentTray";
import { InboxAttachment, InboxAttachments } from "@/components/InboxAttachment";
import { InboxQuestionCard } from "@/components/InboxQuestionCard";
import { MarkdownMessage } from "@/components/MarkdownMessage";
import { SharedApprovalCard } from "@/components/ApprovalCard";
import { submitInboxBlock, type InboxBlockSubmit } from "@/lib/api";
import type {
  InboxApprovalAnswer,
  InboxAttachmentRef,
  InboxBlock,
  InboxItem,
  InboxQuestionAnswer,
} from "@/lib/types";

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function blockAnswered(block: InboxBlock): boolean {
  if (block.type === "question" || block.type === "approval") {
    return block.answer !== null;
  }
  return false;
}

export function InboxItemView({
  host,
  token,
  item,
  onDelete,
}: {
  host: string;
  token: string;
  item: InboxItem;
  onDelete?: () => void;
}) {
  return (
    <article className="inbox-doc">
      <header className="inbox-doc-head">
        <div className="inbox-doc-meta">
          {item.from_label ? (
            <span className="inbox-doc-from">{item.from_label}</span>
          ) : null}
          <span className={`inbox-doc-status inbox-status-${item.status}`}>
            <span className="inbox-lamp" aria-hidden="true" />
            {item.status}
          </span>
          <span className="inbox-doc-read">
            {item.read_at ? "read" : "unread"}
          </span>
          <span className="inbox-doc-time">{formatTime(item.created_at)}</span>
          {item.from_session_id || onDelete ? (
            <div className="inbox-doc-actions">
              {item.from_session_id ? (
                <Link
                  className="inbox-doc-action-link"
                  href={`/session/${item.from_session_id}`}
                >
                  Open session
                </Link>
              ) : null}
              {onDelete ? (
                <button
                  type="button"
                  className="inbox-doc-delete"
                  onClick={onDelete}
                >
                  Delete
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
        <div className="inbox-doc-title-row">
          <h2 className="inbox-doc-subject">{item.subject}</h2>
        </div>
      </header>
      <div className="inbox-doc-blocks">
        {item.blocks.map((block) => (
          <InboxBlockRow
            key={block.id}
            host={host}
            token={token}
            item={item}
            block={block}
          />
        ))}
      </div>
    </article>
  );
}

function InboxBlockRow({
  host,
  token,
  item,
  block,
}: {
  host: string;
  token: string;
  item: InboxItem;
  block: InboxBlock;
}) {
  const [error, setError] = useState<string | null>(null);
  const attachments = useAttachments({
    host,
    token,
    sessionId: item.from_session_id,
    pin: true,
    onError: setError,
  });
  // Pre-fill with the existing reply so a re-submit visibly edits that one
  // reply instead of silently overwriting an invisible prior (single reply
  // per block — no thread).
  const [notes, setNotes] = useState(block.reply?.notes ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(
    () =>
      new Set(block.type === "question" ? block.answer?.selected ?? [] : []),
  );
  const [other, setOther] = useState(
    block.type === "question" ? block.answer?.other ?? "" : "",
  );
  const fileInputRef = useRef<HTMLInputElement>(null);
  // The reply's persisted attachments as of the last successful submit. Carried
  // forward on the next edit so a re-submit landing before the WS refresh (when
  // the `block.reply` prop is still stale) can't drop just-sent files.
  const sentReplyAttachmentsRef = useRef<InboxAttachmentRef[] | null>(null);

  const [decision, setDecision] = useState<string | null>(
    block.type === "approval" ? (block.answer?.decision ?? null) : null,
  );

  const answered = blockAnswered(block);
  const isDecisionBlock =
    block.type === "question" || block.type === "approval";
  const pending = isDecisionBlock && !answered;
  const hasCommittedReply = block.reply != null;
  // Unanswered decision blocks open straight into edit mode (low-friction
  // first answer); everything else starts read-only behind an Edit/Reply
  // affordance.
  const [editing, setEditing] = useState(pending);
  const decisionTag = block.type === "approval" ? "APPROVAL" : "QUESTION";
  let answerEcho = "";
  if (block.type === "approval" && block.answer) {
    answerEcho = block.answer.decision;
  } else if (block.type === "question" && block.answer) {
    answerEcho = [
      ...block.answer.selected,
      ...(block.answer.other ? [block.answer.other] : []),
    ].join(", ");
  }

  function toggle(label: string) {
    if (block.type !== "question") return;
    setSelected((prev) => {
      const next = new Set(prev);
      if (block.multi) {
        if (next.has(label)) next.delete(label);
        else next.add(label);
      } else if (next.has(label) && next.size === 1) {
        next.clear();
      } else {
        next.clear();
        next.add(label);
      }
      return next;
    });
  }

  // The answer built from the current edit-mode selection, or undefined when
  // there's nothing to record. Omitting it (e.g. a required question with no
  // selection) leaves any existing answer untouched and never sends an empty
  // answer the backend would reject.
  function buildAnswer(): InboxQuestionAnswer | InboxApprovalAnswer | undefined {
    if (block.type === "question") {
      if (selected.size === 0 && !other.trim()) return undefined;
      return { selected: Array.from(selected), other: other.trim() || null };
    }
    if (block.type === "approval") {
      return decision ? { decision } : undefined;
    }
    return undefined;
  }

  function startEdit() {
    // Re-seed from the latest committed values so an edit opens on current
    // state even if props changed since mount.
    setSelected(
      new Set(block.type === "question" ? (block.answer?.selected ?? []) : []),
    );
    setOther(block.type === "question" ? (block.answer?.other ?? "") : "");
    setDecision(block.type === "approval" ? (block.answer?.decision ?? null) : null);
    setNotes(block.reply?.notes ?? "");
    setError(null);
    setEditing(true);
  }

  function cancel() {
    // Revert local edits and free any uncommitted (pinned) uploads.
    setSelected(
      new Set(block.type === "question" ? (block.answer?.selected ?? []) : []),
    );
    setOther(block.type === "question" ? (block.answer?.other ?? "") : "");
    setDecision(block.type === "approval" ? (block.answer?.decision ?? null) : null);
    setNotes(block.reply?.notes ?? "");
    attachments.discardAll();
    setError(null);
    // Restore the initial edit state: a never-answered decision block
    // (pending) stays open/answerable rather than collapsing into a
    // misleading answered-looking view; everything else returns to its
    // read-only trigger.
    setEditing(pending);
  }

  async function save() {
    if (submitting) return;
    const answer = buildAnswer();
    const trimmed = notes.trim();
    const hasReply = trimmed.length > 0 || attachments.readyIds.length > 0;
    if (!answer && !hasReply) {
      // Nothing to persist — just leave edit mode.
      cancel();
      return;
    }
    const body: InboxBlockSubmit = {};
    if (answer) body.answer = answer;
    if (hasReply) {
      const newAttachments = attachments.readyIds.map((attachmentId) => ({
        session_id: item.from_session_id,
        attachment_id: attachmentId,
      }));
      // The single reply is replaced on submit, so carry the existing reply's
      // attachments forward — an edit must not drop prior files. Prefer the
      // last-sent set (ref) over the prop, which lags the WS refresh.
      const prior =
        sentReplyAttachmentsRef.current ?? block.reply?.attachments ?? [];
      body.reply = {
        notes: trimmed || null,
        attachments: [...prior, ...newAttachments],
      };
    }
    setSubmitting(true);
    setError(null);
    try {
      await submitInboxBlock(host, token, item.id, block.id, body);
      if (body.reply) {
        sentReplyAttachmentsRef.current = body.reply.attachments ?? [];
      }
      // Drop the pinned reply blobs from the orphan set so the hook's
      // unmount/pagehide cleanup can't delete them out from under the lead.
      attachments.clear();
      setNotes(trimmed);
      setEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to submit");
    } finally {
      setSubmitting(false);
    }
  }

  function onFilesPicked(files: FileList | null) {
    const list = files ? Array.from(files) : [];
    if (list.length) attachments.addFiles(list);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function onPaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const files = filesFromDataTransfer(event.clipboardData);
    if (files.length === 0) return;
    event.preventDefault();
    attachments.addFiles(files);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragActive(false);
    const files = filesFromDataTransfer(event.dataTransfer);
    if (files.length) attachments.addFiles(files);
  }

  function onDragOver(event: DragEvent<HTMLDivElement>) {
    if (!Array.from(event.dataTransfer.types).includes("Files")) return;
    event.preventDefault();
    setDragActive(true);
  }

  const canSave =
    !!buildAnswer() ||
    notes.trim().length > 0 ||
    attachments.readyIds.length > 0;
  const editTrigger = pending ? "Answer" : hasCommittedReply || answered ? "Edit" : "Reply";

  return (
    <section className={`inbox-block${pending ? " pending" : ""}`}>
      {isDecisionBlock ? (
        <div className="inbox-block-head">
          <span className="inbox-block-tag">{decisionTag}</span>
          <span
            className={`inbox-block-state${pending ? " pending" : " answered"}`}
          >
            <span className="inbox-lamp" aria-hidden="true" />
            {pending ? "Pending" : "Answered"}
          </span>
          {!editing && answered && answerEcho ? (
            <span className="inbox-block-echo">{answerEcho}</span>
          ) : null}
        </div>
      ) : null}

      <div className="inbox-block-content">
        {block.type === "markdown" ? (
          <MarkdownMessage text={block.text} />
        ) : null}
        {block.type === "attachment" ? (
          <InboxAttachment host={host} token={token} attachmentRef={block.ref} />
        ) : null}
        {block.type === "question" ? (
          <InboxQuestionCard
            block={block}
            selected={selected}
            other={other}
            onToggle={toggle}
            onOtherChange={setOther}
            disabled={!editing}
          />
        ) : null}
        {block.type === "approval" ? (
          <SharedApprovalCard
            badge="approval"
            copyText={block.prompt}
            actions={
              editing
                ? block.options.map((option) => ({
                    id: option,
                    label: option,
                    className: option === decision ? "primary" : "secondary",
                    onSelect: () => setDecision(option),
                  }))
                : []
            }
          >
            <p className="approval-prompt">{block.prompt}</p>
          </SharedApprovalCard>
        ) : null}
      </div>

      {editing ? (
        <div
          className={`inbox-reply${dragActive ? " dragging" : ""}`}
          onDrop={onDrop}
          onDragOver={onDragOver}
          onDragLeave={() => setDragActive(false)}
        >
          {block.reply?.attachments && block.reply.attachments.length > 0 ? (
            <div className="inbox-reply-carried">
              <span className="inbox-reply-shown-label">Attached</span>
              <InboxAttachments
                host={host}
                token={token}
                refs={block.reply.attachments}
              />
            </div>
          ) : null}
          <textarea
            className="inbox-reply-input"
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            onPaste={onPaste}
            placeholder="Write a reply…"
            rows={2}
            disabled={submitting}
          />
          <AttachmentTray
            items={attachments.items}
            onRemove={attachments.remove}
            onRetry={attachments.retry}
            onClear={attachments.discardAll}
          />
          <div className="inbox-reply-actions">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              hidden
              onChange={(event) => onFilesPicked(event.target.files)}
            />
            <button
              type="button"
              className="secondary inbox-attach-btn"
              onClick={() => fileInputRef.current?.click()}
              disabled={submitting}
            >
              <PaperclipIcon />
              Attach
            </button>
            <div className="inbox-reply-actions-end">
              <button
                type="button"
                className="secondary"
                onClick={cancel}
                disabled={submitting}
              >
                Cancel
              </button>
              <button
                type="button"
                className="primary"
                disabled={submitting || !canSave || attachments.uploading}
                onClick={() => void save()}
              >
                {submitting ? "Saving…" : "Save"}
              </button>
            </div>
          </div>
          {error ? <p className="inbox-block-error">{error}</p> : null}
        </div>
      ) : (
        <>
          {block.reply ? (
            <div className="inbox-reply-shown">
              <span className="inbox-reply-shown-label">Your reply</span>
              {block.reply.notes ? (
                <p className="inbox-reply-shown-notes">{block.reply.notes}</p>
              ) : null}
              <InboxAttachments
                host={host}
                token={token}
                refs={block.reply.attachments}
              />
            </div>
          ) : null}
          <div className="inbox-block-foot">
            <button
              type="button"
              className="inbox-reply-trigger"
              onClick={startEdit}
            >
              {editTrigger}
            </button>
          </div>
        </>
      )}
    </section>
  );
}
