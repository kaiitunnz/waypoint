"use client";

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
        </div>
        <div className="inbox-doc-title-row">
          <h2 className="inbox-doc-subject">{item.subject}</h2>
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

  const answered = blockAnswered(block);
  // A pending question keeps its composer open — the Submit control lives
  // there; every other block reveals it behind an unobtrusive Reply trigger.
  const [replyOpen, setReplyOpen] = useState(
    block.type === "question" && !answered,
  );
  const hasReplyDraft =
    notes.trim().length > 0 || attachments.readyIds.length > 0;

  const isDecisionBlock =
    block.type === "question" || block.type === "approval";
  const pending = isDecisionBlock && !answered;
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

  async function submit(answer?: InboxQuestionAnswer | InboxApprovalAnswer) {
    if (submitting) return;
    const trimmed = notes.trim();
    const hasReply = trimmed.length > 0 || attachments.readyIds.length > 0;
    if (!answer && !hasReply) return;
    const body: InboxBlockSubmit = {};
    if (answer) body.answer = answer;
    if (hasReply) {
      const newAttachments = attachments.readyIds.map((attachmentId) => ({
        session_id: item.from_session_id,
        attachment_id: attachmentId,
      }));
      // Editing replaces the single reply, so carry the existing reply's
      // attachments forward — a notes-only edit must not drop prior files.
      // Prefer the last-sent set (ref) over the prop, which lags the WS refresh.
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
      // Reflect the persisted reply (WS refresh updates block.reply too) rather
      // than blanking the box after an edit.
      setNotes(trimmed);
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

  const questionSubmitEnabled =
    block.type === "question" &&
    (selected.size > 0 || other.trim().length > 0 || hasReplyDraft);

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
          {!pending && answerEcho ? (
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
            disabled={answered}
          />
        ) : null}
        {block.type === "approval" ? (
          <SharedApprovalCard
            badge="approval"
            copyText={block.prompt}
            actions={
              answered
                ? []
                : block.options.map((option, index) => ({
                    id: option,
                    label: option,
                    className: index === 0 ? "primary" : "secondary",
                    onSelect: () => submit({ decision: option }),
                  }))
            }
          >
            <p className="approval-prompt">{block.prompt}</p>
          </SharedApprovalCard>
        ) : null}
      </div>

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
        {replyOpen ? (
          <div
            className={`inbox-reply${dragActive ? " dragging" : ""}`}
            onDrop={onDrop}
            onDragOver={onDragOver}
            onDragLeave={() => setDragActive(false)}
          >
            <textarea
              className="inbox-reply-input"
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              onPaste={onPaste}
              placeholder="Write a reply — paste or drop an image to attach…"
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
              {block.type === "question" && !answered ? (
                <button
                  type="button"
                  className="primary"
                  disabled={submitting || !questionSubmitEnabled}
                  onClick={() =>
                    void submit(
                      selected.size > 0 || other.trim().length > 0
                        ? {
                            selected: Array.from(selected),
                            other: other.trim() || null,
                          }
                        : undefined,
                    )
                  }
                >
                  {submitting ? "Sending…" : "Submit"}
                </button>
              ) : (
                <button
                  type="button"
                  className="primary"
                  disabled={submitting || !hasReplyDraft || attachments.uploading}
                  onClick={() => void submit()}
                >
                  {submitting
                    ? "Sending…"
                    : block.reply
                      ? "Update reply"
                      : "Send reply"}
                </button>
              )}
            </div>
            {error ? <p className="inbox-block-error">{error}</p> : null}
          </div>
        ) : (
          <button
            type="button"
            className="inbox-reply-trigger"
            onClick={() => setReplyOpen(true)}
          >
            {block.reply ? "Edit reply" : "Reply"}
          </button>
        )}
      </div>
    </section>
  );
}
