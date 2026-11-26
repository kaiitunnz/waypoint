"use client";

import { useRef, useState } from "react";

import { submitInboxBlock } from "../api";
import {
  blockDraft,
  buildBlockAnswer,
  buildBlockSubmit,
  isBlockAnswered,
  isDecisionBlock,
  toggleBlockOption,
} from "../inbox";
import type { InboxAttachmentRef, InboxBlock, InboxItem } from "../types";
import { useAttachments } from "./useAttachments";

interface InboxBlockOptions {
  host: string;
  token: string;
  item: InboxItem;
  block: InboxBlock;
}

export function useInboxBlock({ host, token, item, block }: InboxBlockOptions) {
  const [error, setError] = useState<string | null>(null);
  // Reply files are pinned so the requesting session can read them back later.
  const attachments = useAttachments({
    host,
    token,
    sessionId: item.from_session_id,
    pin: true,
    onError: setError,
  });
  const committed = () => blockDraft(block);
  // A block has one reply, which a save replaces, so the draft starts from it.
  const [notes, setNotes] = useState(() => committed().notes);
  const [selected, setSelected] = useState(() => committed().selected);
  const [other, setOther] = useState(() => committed().other);
  const [decision, setDecision] = useState(() => committed().decision);
  const [submitting, setSubmitting] = useState(false);
  const saving = useRef(false);
  // The reply's files as of the last save, so a re-submit that lands before the
  // update arrives (while `block.reply` is stale) cannot drop just-sent files.
  const sentFiles = useRef<InboxAttachmentRef[] | null>(null);

  const answered = isBlockAnswered(block);
  const pending = isDecisionBlock(block) && !answered;
  // Unanswered decisions open ready to answer; everything else starts read-only.
  const [editing, setEditing] = useState(pending);

  const draft = { selected, other, decision, notes };
  const answer = buildBlockAnswer(block, draft);
  const canSave = !!answer || notes.trim().length > 0 || attachments.readyIds.length > 0;

  function reset() {
    const next = committed();
    setSelected(next.selected);
    setOther(next.other);
    setDecision(next.decision);
    setNotes(next.notes);
  }

  // Opens on the latest committed values, even if they changed since mount.
  function startEdit() {
    reset();
    setError(null);
    setEditing(true);
  }

  // An unanswered decision stays open. Does nothing while a save is in flight, which
  // owns the uploads.
  function cancel() {
    if (saving.current) return;
    reset();
    attachments.discardAll();
    setError(null);
    setEditing(pending);
  }

  async function save() {
    if (saving.current || attachments.uploading) return;
    const uploaded = attachments.readyIds.map((attachmentId) => ({
      session_id: item.from_session_id,
      attachment_id: attachmentId,
    }));
    const body = buildBlockSubmit(block, {
      draft,
      uploaded,
      carried: sentFiles.current ?? block.reply?.attachments ?? [],
    });
    if (!body) return;
    saving.current = true;
    setSubmitting(true);
    setError(null);
    // Leaving mid-save must not delete the files the reply is naming.
    const letGo = attachments.hold(attachments.readyIds);
    try {
      await submitInboxBlock(host, token, item.id, block.id, body);
      if (body.reply) sentFiles.current = body.reply.attachments ?? [];
      // Empties the tray and keeps the files the saved reply names.
      attachments.clear();
      setNotes(notes.trim());
      setEditing(false);
    } catch (reason) {
      letGo();
      setError(reason instanceof Error ? reason.message : "failed to submit");
    } finally {
      saving.current = false;
      setSubmitting(false);
    }
  }

  function toggle(label: string) {
    if (block.type !== "question") return;
    setSelected((current) => toggleBlockOption(current, label, block.multi));
  }

  return {
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
  };
}

export type InboxBlockController = ReturnType<typeof useInboxBlock>;
