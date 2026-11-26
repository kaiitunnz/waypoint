"use client";

import { useRef, type KeyboardEvent } from "react";

import { attachFromPaste } from "@waypoint/web-core/fileDrop";
import { useElementFileDrop } from "@waypoint/web-core/hooks/useElementFileDrop";
import { useInboxBlock, type InboxBlockController } from "@waypoint/web-core/hooks/useInboxBlock";
import { blockAnswerEcho, blockEditLabel, isDecisionBlock, type InboxDecisionBlock } from "@waypoint/web-core/inbox";
import { isComposingKeyEvent, isModifiedEnterShortcut } from "@waypoint/web-core/keyboard";
import { stripRecommendedSuffix } from "@waypoint/web-core/pendingAskActions";
import type { InboxApprovalBlock, InboxBlock, InboxItem, InboxQuestionBlock } from "@waypoint/web-core/types";

import { AttachmentTray } from "@/components/composer/AttachmentTray";
import { CopyButton } from "@/components/session/cards/CopyButton";
import { Markdown } from "@/components/session/cards/Markdown";
import { Button } from "@/components/ui/Button";
import { TextArea } from "@/components/ui/Field";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { StatusLamp } from "@/components/ui/StatusLamp";
import { cx } from "@/lib/cx";
import { useShellData } from "@/lib/shell-data";

import { FileChips, FileTile } from "./InboxFiles";
import { OptionRows } from "./OptionRows";
import styles from "./InboxBlock.module.css";

function QuestionOptions({ block, controller }: { block: InboxQuestionBlock; controller: InboxBlockController }) {
  return (
    <>
      {block.options.length > 0 ? (
        <OptionRows
          options={block.options}
          selected={[...controller.selected]}
          multi={block.multi}
          disabled={controller.submitting}
          label={block.question}
          onOption={controller.toggle}
        />
      ) : null}
      <TextArea
        rows={1}
        value={controller.other}
        disabled={controller.submitting}
        aria-label="Other answer"
        placeholder="Other answer"
        onChange={(event) => controller.setOther(event.target.value)}
      />
    </>
  );
}

function ApprovalOptions({ block, controller }: { block: InboxApprovalBlock; controller: InboxBlockController }) {
  return (
    <OptionRows
      columns
      options={block.options.map((label) => ({ label }))}
      selected={controller.decision ? [controller.decision] : []}
      multi={false}
      disabled={controller.submitting}
      label={block.prompt}
      onOption={controller.setDecision}
    />
  );
}

function ReplyEditor({ block, controller }: { block: InboxBlock; controller: InboxBlockController }) {
  const { attachments, submitting } = controller;
  const picker = useRef<HTMLInputElement>(null);
  const carried = block.reply?.attachments ?? [];

  return (
    <>
      {carried.length > 0 ? (
        <div className={styles.echo}>
          <span className={styles.echoLabel}>Attached</span>
          <FileChips refs={carried} />
        </div>
      ) : null}
      <TextArea
        rows={2}
        value={controller.notes}
        disabled={submitting}
        aria-label="Reply"
        placeholder="Reply"
        onChange={(event) => controller.setNotes(event.target.value)}
        onPaste={(event) => attachFromPaste(event, attachments.addFiles)}
      />
      {attachments.items.length > 0 ? (
        <AttachmentTray
          items={attachments.items}
          onRemove={attachments.remove}
          onRetry={attachments.retry}
          onClear={attachments.discardAll}
          locked={submitting}
        />
      ) : null}
      <div className={styles.actions}>
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          onChange={(event) => {
            const files = Array.from(event.target.files ?? []);
            if (files.length) attachments.addFiles(files);
            event.target.value = "";
          }}
        />
        <IconButton label="Attach files" className={styles.attach} disabled={submitting} onClick={() => picker.current?.click()}>
          <Icon name="paperclip" size={18} />
        </IconButton>
        <span className={styles.grow} />
        <Button variant="text" disabled={submitting} className={styles.cancel} onClick={controller.cancel}>
          Cancel
        </Button>
        <Button
          variant="primary"
          disabled={submitting || !controller.canSave || attachments.uploading}
          onClick={() => void controller.save()}
        >
          {submitting ? "Saving…" : isDecisionBlock(block) ? "Save answer" : "Save reply"}
        </Button>
      </div>
      {controller.error ? (
        <p className={styles.error} role="alert">
          {controller.error}
        </p>
      ) : null}
    </>
  );
}

function Committed({ block, controller }: { block: InboxBlock; controller: InboxBlockController }) {
  const { answered } = controller;
  return (
    <>
      {isDecisionBlock(block) && answered ? (
        <p className={styles.echo}>
          <span className={styles.echoLabel}>Your answer</span>
          <span className={styles.echoAnswer}>{blockAnswerEcho(block, stripRecommendedSuffix)}</span>
        </p>
      ) : null}
      {block.reply ? (
        <div className={styles.echo}>
          <span className={styles.echoLabel}>Reply</span>
          <span className={styles.echoReply}>
            {block.reply.notes ? <span>{block.reply.notes}</span> : null}
            <FileChips refs={block.reply.attachments} />
          </span>
        </div>
      ) : null}
      {answered ? null : (
        <Button variant="text" size="sm" className={styles.open} onClick={controller.startEdit}>
          {blockEditLabel(block)}
        </Button>
      )}
    </>
  );
}

function Head({ block, controller }: { block: InboxDecisionBlock; controller: InboxBlockController }) {
  return (
    <div className={styles.head}>
      <span className={styles.kind}>{block.type === "question" ? "Question" : "Approval"}</span>
      {controller.pending ? (
        <span className={styles.waiting}>
          <StatusLamp state="needs" size="sm" flat />
          Waiting on you
        </span>
      ) : (
        <span className={styles.answered}>
          <Icon name="check" size={14} strokeWidth={2.2} />
          Answered
        </span>
      )}
      <span className={styles.end}>
        {block.required ? <span>Required</span> : null}
        {block.type === "approval" ? <CopyButton text={block.prompt} label="Copy prompt" /> : null}
        {!controller.editing && !controller.pending ? (
          <Button variant="text" size="sm" onClick={controller.startEdit}>
            Edit
          </Button>
        ) : null}
      </span>
    </div>
  );
}

export function InboxBlockView({ item, block }: { item: InboxItem; block: InboxBlock }) {
  const { host, token } = useShellData();
  const controller = useInboxBlock({ host, token, item, block });
  const { dragActive, dropProps } = useElementFileDrop(controller.editing, controller.attachments.addFiles);

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (!controller.editing || isComposingKeyEvent(event)) return;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      controller.cancel();
      return;
    }
    if (!isModifiedEnterShortcut(event)) return;
    // A focused option must not toggle.
    event.preventDefault();
    void controller.save();
  };

  return (
    <section
      className={cx(styles.block, dragActive && styles.drop)}
      aria-label={block.type === "question" ? block.question : block.type === "approval" ? block.prompt : undefined}
      onKeyDown={onKeyDown}
      {...dropProps}
    >
      {isDecisionBlock(block) ? <Head block={block} controller={controller} /> : null}
      {block.type === "markdown" ? (
        <div className={styles.markdown}>
          <Markdown text={block.text} />
        </div>
      ) : null}
      {block.type === "attachment" ? <FileTile attachment={block.ref} /> : null}
      {block.type === "question" ? (
        <>
          {block.header ? <p className={styles.meta}>{block.header}</p> : null}
          <p className={styles.question}>{block.question}</p>
          {block.multi ? <p className={styles.meta}>Multi-select</p> : null}
        </>
      ) : null}
      {block.type === "approval" ? <p className={styles.question}>{block.prompt}</p> : null}
      {controller.editing ? (
        <div className={styles.edit}>
          {block.type === "question" ? <QuestionOptions block={block} controller={controller} /> : null}
          {block.type === "approval" ? <ApprovalOptions block={block} controller={controller} /> : null}
          <ReplyEditor block={block} controller={controller} />
        </div>
      ) : (
        <Committed block={block} controller={controller} />
      )}
    </section>
  );
}
