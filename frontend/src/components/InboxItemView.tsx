"use client";

import Link from "next/link";
import { useRef, type ClipboardEvent } from "react";

import { AttachmentTray, PaperclipIcon } from "@/components/AttachmentTray";
import { filesFromDataTransfer } from "@waypoint/web-core/fileDrop";
import { useElementFileDrop } from "@waypoint/web-core/hooks/useElementFileDrop";
import { useInboxBlock } from "@waypoint/web-core/hooks/useInboxBlock";
import { InboxAttachment, InboxAttachments } from "@/components/InboxAttachment";
import { InboxQuestionCard } from "@/components/InboxQuestionCard";
import { MarkdownMessage } from "@/components/MarkdownMessage";
import { SharedApprovalCard } from "@/components/ApprovalCard";
import { blockAnswerEcho, blockEditLabel, isDecisionBlock } from "@waypoint/web-core/inbox";
import type { InboxBlock, InboxItem } from "@waypoint/web-core/types";

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
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
  const {
    attachments,
    error,
    notes,
    setNotes,
    selected,
    toggle,
    other,
    setOther,
    decision,
    setDecision,
    editing,
    answered,
    pending,
    submitting,
    canSave,
    startEdit,
    cancel,
    save,
  } = useInboxBlock({ host, token, item, block });
  const fileInputRef = useRef<HTMLInputElement>(null);
  const isDecision = isDecisionBlock(block);
  const { dragActive, dropProps } = useElementFileDrop(editing, attachments.addFiles);
  const decisionTag = block.type === "approval" ? "APPROVAL" : "QUESTION";
  const answerEcho = blockAnswerEcho(block);

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

  const editTrigger = blockEditLabel(block);

  return (
    <section
      className={`inbox-block${pending ? " pending" : ""}${dragActive ? " is-drag-active" : ""}`}
      {...dropProps}
    >
      {isDecision ? (
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
        <div className="inbox-reply">
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
