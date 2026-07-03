"use client";

import { useRef, useState } from "react";

import {
  AttachmentTray,
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
    <div className="inbox-item">
      <header className="inbox-item-head">
        <div className="inbox-item-titles">
          <h2 className="inbox-item-subject">{item.subject}</h2>
          <div className="inbox-item-meta">
            {item.from_label ? (
              <span className="badge neutral">{item.from_label}</span>
            ) : null}
            <span className={`inbox-status inbox-status-${item.status}`}>
              <span className="inbox-status-dot" aria-hidden="true" />
              {item.status}
            </span>
            <span className="role-time">{formatTime(item.created_at)}</span>
          </div>
        </div>
        {onDelete ? (
          <button
            type="button"
            className="link-button inbox-item-delete"
            onClick={onDelete}
          >
            Delete
          </button>
        ) : null}
      </header>
      <div className="inbox-blocks">
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
    </div>
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
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(
    () =>
      new Set(block.type === "question" ? block.answer?.selected ?? [] : []),
  );
  const [other, setOther] = useState(
    block.type === "question" ? block.answer?.other ?? "" : "",
  );
  const fileInputRef = useRef<HTMLInputElement>(null);

  const answered = blockAnswered(block);
  const hasReplyDraft =
    notes.trim().length > 0 || attachments.readyIds.length > 0;

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
      body.reply = {
        notes: trimmed || null,
        attachments: attachments.readyIds.map((attachmentId) => ({
          session_id: item.from_session_id,
          attachment_id: attachmentId,
        })),
      };
    }
    setSubmitting(true);
    setError(null);
    try {
      await submitInboxBlock(host, token, item.id, block.id, body);
      // Drop the pinned reply blobs from the orphan set so the hook's
      // unmount/pagehide cleanup can't delete them out from under the lead.
      attachments.clear();
      setNotes("");
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

  const questionSubmitEnabled =
    block.type === "question" &&
    (selected.size > 0 || other.trim().length > 0 || hasReplyDraft);

  return (
    <section className="inbox-block">
      <div className="inbox-block-content">
        {block.type === "markdown" ? (
          <MarkdownMessage text={block.text} />
        ) : null}
        {block.type === "attachment" ? (
          <InboxAttachment host={host} token={token} attachmentRef={block.ref} />
        ) : null}
        {block.type === "question" ? (
          <>
            <InboxQuestionCard
              block={block}
              selected={selected}
              other={other}
              onToggle={toggle}
              onOtherChange={setOther}
              disabled={answered}
            />
            {answered && block.answer?.other ? (
              <p className="inbox-answer-note">
                <span className="inbox-answer-label">Answer</span>
                {block.answer.other}
              </p>
            ) : null}
          </>
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
            {answered && block.answer ? (
              <p className="inbox-answer-note">
                <span className="inbox-answer-label">Decision</span>
                {block.answer.decision}
              </p>
            ) : null}
          </SharedApprovalCard>
        ) : null}
      </div>

      {block.reply ? (
        <div className="inbox-block-reply-shown">
          <span className="inbox-answer-label">Reply</span>
          {block.reply.notes ? <p>{block.reply.notes}</p> : null}
          <InboxAttachments
            host={host}
            token={token}
            refs={block.reply.attachments}
          />
        </div>
      ) : null}

      <div className="inbox-block-reply">
        <textarea
          className="inbox-reply-input"
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          placeholder="Add a reply note (optional)…"
          rows={2}
          disabled={submitting}
        />
        <AttachmentTray
          items={attachments.items}
          onRemove={attachments.remove}
          onRetry={attachments.retry}
          onClear={attachments.discardAll}
        />
        <div className="inbox-block-actions">
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
              {submitting ? "Sending…" : "Send reply"}
            </button>
          )}
        </div>
        {error ? <p className="inbox-block-error">{error}</p> : null}
      </div>
    </section>
  );
}
