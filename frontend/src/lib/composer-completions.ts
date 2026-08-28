"use client";

import type { KeyboardEvent, RefObject } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  fetchSessionCompletionsResponse,
  fetchWorkspaceTree,
  type WorkspaceTreeEntry,
} from "@/lib/api";
import { isInlineMenuAcceptKey } from "@/lib/keyboard";
import type {
  CommandCompletion,
  SessionCommandInvocation,
} from "@/lib/types";

const COMPLETION_FETCH_DEBOUNCE_MS = 180;
const COMPLETION_REFRESH_POLL_MS = 750;
const WORKSPACE_PATH_PAGE_LIMIT = 200;

// `/new` works on every structured backend so we surface it locally
// while the debounced fetch is in flight or if the request fails. Tmux
// sessions don't support `/new`, so the term composer opts out of this
// fallback (`localFallback: []`).
export const SLASH_NEW_FALLBACK: CommandCompletion = {
  id: "waypoint:builtin:new",
  trigger: "/",
  replacement: "/new ",
  name: "new",
  description: "Start a new session with the same settings",
  kind: "session_control",
  source: "waypoint",
  dispatch: "frontend_control",
  metadata: {},
};

// A `./` workspace path candidate; selecting it edits the draft, never dispatches.
export interface WorkspacePathSuggestion {
  type: "workspace_path";
  replacement: string;
  description: "Directory" | "File" | "Symlink";
}

// A menu row: a backend command or a workspace path. The discriminated union
// keeps a path from reaching command dispatch.
export type SuggestionRow =
  | { type: "command"; command: CommandCompletion }
  | WorkspacePathSuggestion;

export function commandLabel(entry: CommandCompletion): string {
  return `${entry.trigger}${entry.name}`;
}

export function rowKey(row: SuggestionRow): string {
  return row.type === "command" ? row.command.id : row.replacement;
}

export function rowLabel(row: SuggestionRow): string {
  return row.type === "command" ? commandLabel(row.command) : row.replacement;
}

export function rowDescription(row: SuggestionRow): string | null {
  return row.type === "command"
    ? row.command.description ?? null
    : row.description;
}

export function rowHint(row: SuggestionRow): string | null {
  return row.type === "command" ? row.command.argument_hint ?? null : null;
}

// Split a `./` caret token into the directory to list and the leaf being typed.
// Null when the token isn't `./`-rooted or a directory segment is `.`/`..`, so
// an out-of-scope prefix never becomes a valid query.
export function parseWorkspacePathToken(
  head: string,
): { dir: string; leaf: string } | null {
  if (!head.startsWith("./")) return null;
  const fragment = head.slice(2);
  const lastSlash = fragment.lastIndexOf("/");
  const dir = lastSlash === -1 ? "" : fragment.slice(0, lastSlash);
  const leaf = lastSlash === -1 ? fragment : fragment.slice(lastSlash + 1);
  const segments = dir.split("/").filter((segment) => segment.length > 0);
  if (segments.some((segment) => segment === "." || segment === "..")) {
    return null;
  }
  return { dir, leaf };
}

function workspacePathRow(
  dir: string,
  entry: WorkspaceTreeEntry,
): WorkspacePathSuggestion {
  const prefix = dir ? `./${dir}/` : "./";
  const suffix = entry.kind === "dir" ? "/" : "";
  const description =
    entry.kind === "dir"
      ? "Directory"
      : entry.kind === "symlink"
        ? "Symlink"
        : "File";
  return {
    type: "workspace_path",
    replacement: `${prefix}${entry.name}${suffix}`,
    description,
  };
}

interface UseCommandCompletionsOptions {
  host: string;
  token: string;
  sessionId: string;
  draft: string;
  setDraft: (next: string) => void;
  // Gates `/` and `$` completions.
  enabled: boolean;
  // Gates `./` path completion. Defaults to ``enabled``.
  pathEnabled?: boolean;
  // Override the local fallback list. Chat composer uses ``/new``; the
  // tmux composer passes ``[]`` because its backend doesn't accept it.
  localFallback?: ReadonlyArray<CommandCompletion>;
  textareaRef?: RefObject<HTMLTextAreaElement | null>;
}

export interface CommandCompletionsState {
  suggestions: ReadonlyArray<SuggestionRow>;
  suggestionsOpen: boolean;
  activeIndex: number;
  setActiveIndex: (index: number) => void;
  selectedCompletion: CommandCompletion | null;
  listRef: RefObject<HTMLUListElement | null>;
  itemRefs: RefObject<Array<HTMLButtonElement | null>>;
  applySuggestion: (index: number) => void;
  selectedCommandInvocation: (text: string) => SessionCommandInvocation | undefined;
  handleSuggestionKey: (event: KeyboardEvent<HTMLTextAreaElement>) => boolean;
  reset: () => void;
}

export function useCommandCompletions({
  host,
  token,
  sessionId,
  draft,
  setDraft,
  enabled,
  pathEnabled,
  localFallback = [SLASH_NEW_FALLBACK],
  textareaRef,
}: UseCommandCompletionsOptions): CommandCompletionsState {
  const pathCompletionEnabled = pathEnabled ?? enabled;
  const [suggestionIndex, setSuggestionIndex] = useState(0);
  const [suggestionsDismissed, setSuggestionsDismissed] = useState(false);
  const [backendCompletions, setBackendCompletions] = useState<CommandCompletion[]>([]);
  // The last workspace directory page, tagged with its directory so a stale
  // response for another token is ignored.
  const [pathPage, setPathPage] = useState<{
    dir: string;
    entries: WorkspaceTreeEntry[];
  } | null>(null);
  const [selectedCompletion, setSelectedCompletion] =
    useState<CommandCompletion | null>(null);

  const listRef = useRef<HTMLUListElement | null>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);

  // Caret position drives which word is being completed, so a ``/`` or ``$``
  // typed mid-prompt triggers suggestions just like one at the start. Kept in
  // state (synced from the textarea) so the active-word memo recomputes as the
  // caret moves, not only as ``draft`` changes.
  const [caret, setCaret] = useState(0);

  useEffect(() => {
    const el = textareaRef?.current;
    if (!el) return;
    let frame = 0;
    // Defer to the next frame: calling setState synchronously inside a native
    // ``input`` listener on a *controlled* textarea races React's value
    // reconciliation — it re-renders with the pre-update ``draft`` and resets
    // the field, dropping the just-typed character. By the next frame the
    // controlled value has committed, so reading ``selectionStart`` is safe.
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

  // The word under the caret: the run of non-whitespace it sits in. With no
  // textarea wired we can't track the caret, so fall back to the draft end
  // (trailing-word completion) rather than silently disabling suggestions.
  const caretPos = textareaRef ? Math.min(caret, draft.length) : draft.length;
  const [wordStart] = wordRangeAt(draft, caretPos);
  const completionHead = draft.slice(wordStart, caretPos);
  const completionTrigger = completionHead.startsWith("/")
    ? "/"
    : completionHead.startsWith("$")
      ? "$"
      : null;
  const isPathHead = completionHead.startsWith("./");
  const onCompletableToken = completionTrigger !== null || isPathHead;
  // The directory a `./` token would list, or null when no query should run.
  // Keyed on the directory (not the whole token) so typing the leaf filters the
  // loaded page client-side instead of re-fetching the same listing.
  const pathQueryDir =
    pathCompletionEnabled && !suggestionsDismissed
      ? parseWorkspacePathToken(completionHead)?.dir ?? null
      : null;

  const suggestions = useMemo<ReadonlyArray<SuggestionRow>>(() => {
    if (suggestionsDismissed) return [];
    // A token can't be both `./` and `/`/`$`, so at most one source is eligible.
    if (isPathHead) {
      if (!pathCompletionEnabled) return [];
      const parsed = parseWorkspacePathToken(completionHead);
      if (!parsed || !pathPage || pathPage.dir !== parsed.dir) return [];
      const leaf = parsed.leaf.toLowerCase();
      return pathPage.entries
        .filter((entry) => entry.name.toLowerCase().startsWith(leaf))
        .map((entry) => workspacePathRow(parsed.dir, entry));
    }
    if (!enabled || completionTrigger === null) return [];
    const pool =
      completionTrigger === "/"
        ? mergeLocalFallback(backendCompletions, localFallback)
        : backendCompletions;
    return pool
      .filter((entry) => commandLabel(entry).startsWith(completionHead))
      .map((command) => ({ type: "command", command }) as const);
  }, [
    backendCompletions,
    completionHead,
    completionTrigger,
    enabled,
    isPathHead,
    localFallback,
    pathCompletionEnabled,
    pathPage,
    suggestionsDismissed,
  ]);

  // ``suggestions`` is already empty unless the caret sits on a completable
  // token, so its presence is the open condition — no whole-draft test that
  // would suppress mid-prompt triggers.
  const suggestionsOpen = suggestions.length > 0;
  const activeIndex = Math.min(
    suggestionIndex,
    Math.max(0, suggestions.length - 1),
  );

  useEffect(() => {
    if (!enabled || completionTrigger === null) {
      setBackendCompletions([]);
      return;
    }
    const controller = new AbortController();
    let debounceTimer: number | null = null;
    let pollTimer: number | null = null;
    const loadCompletions = () => {
      debounceTimer = null;
      fetchSessionCompletionsResponse(
        host,
        token,
        sessionId,
        completionTrigger,
        completionHead,
        false,
        controller.signal,
      )
        .then((payload) => {
          if (controller.signal.aborted) return;
          setBackendCompletions(payload.completions);
          if (payload.refreshing) {
            pollTimer = window.setTimeout(
              loadCompletions,
              COMPLETION_REFRESH_POLL_MS,
            );
          }
        })
        .catch((error) => {
          if (error instanceof DOMException && error.name === "AbortError") {
            return;
          }
          setBackendCompletions([]);
        });
    };
    debounceTimer = window.setTimeout(
      loadCompletions,
      COMPLETION_FETCH_DEBOUNCE_MS,
    );
    return () => {
      controller.abort();
      if (debounceTimer !== null) window.clearTimeout(debounceTimer);
      if (pollTimer !== null) window.clearTimeout(pollTimer);
    };
  }, [host, token, sessionId, enabled, completionTrigger, completionHead]);

  useEffect(() => {
    if (pathQueryDir === null) return;
    const dir = pathQueryDir;
    const controller = new AbortController();
    const debounceTimer = window.setTimeout(() => {
      fetchWorkspaceTree(host, token, sessionId, dir, {
        limit: WORKSPACE_PATH_PAGE_LIMIT,
        signal: controller.signal,
      })
        .then((page) => {
          if (controller.signal.aborted) return;
          setPathPage({ dir, entries: page.entries });
        })
        .catch((error) => {
          if (error instanceof DOMException && error.name === "AbortError") {
            return;
          }
          // Remote, disabled, 404, or denied: stay silent. Drop only this
          // directory's own stale page, never another token's.
          setPathPage((current) => (current?.dir === dir ? null : current));
        });
    }, COMPLETION_FETCH_DEBOUNCE_MS);
    return () => {
      controller.abort();
      window.clearTimeout(debounceTimer);
    };
  }, [host, token, sessionId, pathQueryDir]);

  useEffect(() => {
    setSuggestionIndex(0);
  }, [completionHead]);

  useEffect(() => {
    if (!suggestionsOpen) return;
    const active = itemRefs.current[activeIndex];
    const list = listRef.current;
    if (!active || !list) return;
    const activeTop = active.offsetTop;
    const activeBottom = activeTop + active.offsetHeight;
    const visibleTop = list.scrollTop;
    const visibleBottom = visibleTop + list.clientHeight;
    if (activeTop < visibleTop) {
      list.scrollTop = activeTop;
    } else if (activeBottom > visibleBottom) {
      list.scrollTop = activeBottom - list.clientHeight;
    }
  }, [activeIndex, suggestionsOpen, suggestions.length]);

  useEffect(() => {
    // Re-arm when the caret leaves the token, so a new trigger reopens the list.
    if (!onCompletableToken) {
      setSuggestionsDismissed(false);
    }
  }, [onCompletableToken]);

  useEffect(() => {
    if (
      selectedCompletion &&
      !draft.startsWith(commandLabel(selectedCompletion))
    ) {
      setSelectedCompletion(null);
    }
  }, [draft, selectedCompletion]);

  function applySuggestion(index: number) {
    const chosen = suggestions[index];
    if (!chosen) return;
    // Replace the whole word the caret is in (its run on both sides), not the
    // entire draft, so mid-prompt completions leave surrounding text intact.
    const pos = textareaRef ? Math.min(caret, draft.length) : draft.length;
    const [start, end] = wordRangeAt(draft, pos);
    const replacement =
      chosen.type === "command" ? chosen.command.replacement : chosen.replacement;
    // Command replacements carry a trailing space; drop a redundant one so
    // completing inside "see /to|do here" doesn't double the gap.
    const tail = draft.slice(end);
    const joinedTail =
      chosen.type === "command" &&
      replacement.endsWith(" ") &&
      tail.startsWith(" ")
        ? tail.slice(1)
        : tail;
    const next = draft.slice(0, start) + replacement + joinedTail;
    const nextCaret = start + replacement.length;
    setDraft(next);
    setCaret(nextCaret);
    if (chosen.type === "command") {
      setSelectedCompletion(chosen.command);
      setSuggestionsDismissed(true);
    } else {
      // Keep the menu armed after a directory (trailing slash) to offer its
      // children; dismiss after a file.
      setSuggestionsDismissed(!replacement.endsWith("/"));
    }
    if (textareaRef) {
      requestAnimationFrame(() => {
        const el = textareaRef.current;
        if (!el) return;
        el.focus();
        el.setSelectionRange(nextCaret, nextCaret);
      });
    }
  }

  function selectedCommandInvocation(
    text: string,
  ): SessionCommandInvocation | undefined {
    if (!selectedCompletion || selectedCompletion.dispatch === "frontend_control") {
      return undefined;
    }
    const command = commandLabel(selectedCompletion);
    if (text !== command && !text.startsWith(`${command} `)) {
      return undefined;
    }
    return {
      completion_id: selectedCompletion.id,
      name: selectedCompletion.name,
      arguments: text.slice(command.length).trim(),
      dispatch: selectedCompletion.dispatch,
      metadata: selectedCompletion.metadata,
    };
  }

  function handleSuggestionKey(
    event: KeyboardEvent<HTMLTextAreaElement>,
  ): boolean {
    if (!suggestionsOpen) return false;
    if (isInlineMenuAcceptKey(event)) {
      event.preventDefault();
      applySuggestion(activeIndex);
      return true;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setSuggestionIndex((index) => Math.min(suggestions.length - 1, index + 1));
      return true;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setSuggestionIndex((index) => Math.max(0, index - 1));
      return true;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      setSuggestionsDismissed(true);
      return true;
    }
    return false;
  }

  function reset() {
    setSelectedCompletion(null);
    setSuggestionsDismissed(false);
    setSuggestionIndex(0);
  }

  return {
    suggestions,
    suggestionsOpen,
    activeIndex,
    setActiveIndex: setSuggestionIndex,
    selectedCompletion,
    listRef,
    itemRefs,
    applySuggestion,
    selectedCommandInvocation,
    handleSuggestionKey,
    reset,
  };
}

// The [start, end) bounds of the non-whitespace word that ``pos`` sits in (or
// at the edge of). Empty run when ``pos`` is on whitespace.
export function wordRangeAt(text: string, pos: number): [number, number] {
  let start = pos;
  while (start > 0 && !/\s/.test(text[start - 1])) start--;
  let end = pos;
  while (end < text.length && !/\s/.test(text[end])) end++;
  return [start, end];
}

function mergeLocalFallback(
  backend: ReadonlyArray<CommandCompletion>,
  fallback: ReadonlyArray<CommandCompletion>,
): CommandCompletion[] {
  if (fallback.length === 0) return [...backend];
  const seen = new Set(backend.map(commandLabel));
  const merged = [...backend];
  for (const entry of fallback) {
    if (!seen.has(commandLabel(entry))) {
      merged.push(entry);
    }
  }
  return merged;
}
