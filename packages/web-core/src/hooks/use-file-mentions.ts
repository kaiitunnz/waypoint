"use client";

import type { KeyboardEvent, RefObject } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useActiveRow, wordRangeAt } from "../composer-completions";
import { fetchSessionAttachments } from "../api";
import { rankItems } from "../fuzzy";
import { isInlineMenuAcceptKey } from "../keyboard";
import type { SessionAttachment } from "../types";

interface UseFileMentionsOptions {
  host: string;
  token: string;
  sessionId: string;
  draft: string;
  setDraft: (next: string) => void;
  enabled: boolean;
  onReference: (spec: SessionAttachment) => void;
  textareaRef?: RefObject<HTMLTextAreaElement | null>;
}

// A ranked `@` candidate; ``positions`` index into ``file.filename``.
export interface FileMentionRow {
  file: SessionAttachment;
  positions: number[];
}

export interface FileMentionsState {
  mentions: ReadonlyArray<FileMentionRow>;
  open: boolean;
  activeIndex: number;
  setActiveIndex: (index: number) => void;
  itemRefs: RefObject<Array<HTMLButtonElement | null>>;
  apply: (index: number) => void;
  handleKey: (event: KeyboardEvent<HTMLTextAreaElement>) => boolean;
}

const MAX_MENTIONS = 8;

const mentionKey = (row: FileMentionRow) => row.file.id;

// Inline `@name` file referencing: type `@` in the composer to search the
// session's stored files and pick one. Selecting strips the `@query` token and
// adds the file to the attachment tray (no re-upload) via `onReference`. Models
// the caret/word + key handling of `useCommandCompletions`, but the source is
// the local session-files list and the apply is an attachment, not text.
export function useFileMentions({
  host,
  token,
  sessionId,
  draft,
  setDraft,
  enabled,
  onReference,
  textareaRef,
}: UseFileMentionsOptions): FileMentionsState {
  const [files, setFiles] = useState<SessionAttachment[] | null>(null);
  const [dismissedQuery, setDismissedQuery] = useState<string | null>(null);
  const [caret, setCaret] = useState(0);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);

  useEffect(() => {
    const el = textareaRef?.current;
    if (!el) return;
    let frame = 0;
    const sync = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() =>
        setCaret(el.selectionStart ?? el.value.length),
      );
    };
    const events = ["input", "keyup", "mouseup", "focus", "select"] as const;
    events.forEach((name) => el.addEventListener(name, sync));
    return () => {
      cancelAnimationFrame(frame);
      events.forEach((name) => el.removeEventListener(name, sync));
    };
  }, [textareaRef]);

  const caretPos = textareaRef ? Math.min(caret, draft.length) : draft.length;
  const [wordStart] = wordRangeAt(draft, caretPos);
  const word = draft.slice(wordStart, caretPos);
  const query = word.startsWith("@") ? word.slice(1) : null;
  const isActive = enabled && query !== null && dismissedQuery !== word;

  // Load the session's files when a mention opens; clear when it closes so the
  // next `@` refetches a fresh list.
  useEffect(() => {
    if (!isActive) {
      setFiles(null);
      return;
    }
    if (files !== null) return;
    const controller = new AbortController();
    fetchSessionAttachments(host, token, sessionId)
      .then((list) => {
        if (!controller.signal.aborted) setFiles(list);
      })
      .catch(() => {
        if (!controller.signal.aborted) setFiles([]);
      });
    return () => controller.abort();
  }, [isActive, files, host, token, sessionId]);

  const mentions = useMemo<ReadonlyArray<FileMentionRow>>(() => {
    if (!isActive || files === null) return [];
    return rankItems(files, query ?? "", (file) => file.filename)
      .slice(0, MAX_MENTIONS)
      .map(({ item, positions }) => ({ file: item, positions }));
  }, [isActive, files, query]);

  const open = mentions.length > 0;
  const { activeIndex, setActiveIndex, resetActive } = useActiveRow(
    mentions,
    mentionKey,
    word,
  );

  function apply(target: number) {
    const chosen = mentions[target]?.file;
    if (!chosen) return;
    const pos = textareaRef ? Math.min(caret, draft.length) : draft.length;
    const [start, end] = wordRangeAt(draft, pos);
    const before = draft.slice(0, start);
    const tail = draft.slice(end);
    // Drop the `@query` token; collapse a doubled space the removal would leave.
    const next =
      before.endsWith(" ") && tail.startsWith(" ")
        ? before + tail.slice(1)
        : before + tail;
    setDraft(next);
    setCaret(before.length);
    resetActive();
    onReference(chosen);
    if (textareaRef) {
      requestAnimationFrame(() => {
        const el = textareaRef.current;
        if (!el) return;
        el.focus();
        el.setSelectionRange(before.length, before.length);
      });
    }
  }

  function handleKey(event: KeyboardEvent<HTMLTextAreaElement>): boolean {
    if (!open) return false;
    if (isInlineMenuAcceptKey(event)) {
      event.preventDefault();
      apply(activeIndex);
      return true;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex(Math.min(mentions.length - 1, activeIndex + 1));
      return true;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex(Math.max(0, activeIndex - 1));
      return true;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      setDismissedQuery(word);
      return true;
    }
    return false;
  }

  return {
    mentions,
    open,
    activeIndex,
    setActiveIndex,
    itemRefs,
    apply,
    handleKey,
  };
}
