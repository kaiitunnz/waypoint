"use client";

import type { KeyboardEvent, RefObject } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import { commandLabel } from "@/components/CommandSuggestions";
import { fetchSessionCompletionsResponse } from "@/lib/api";
import type {
  CommandCompletion,
  SessionCommandInvocation,
} from "@/lib/types";

const COMPLETION_FETCH_DEBOUNCE_MS = 180;
const COMPLETION_REFRESH_POLL_MS = 750;

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

interface UseCommandCompletionsOptions {
  host: string;
  token: string;
  sessionId: string;
  draft: string;
  setDraft: (next: string) => void;
  enabled: boolean;
  // Override the local fallback list. Chat composer uses ``/new``; the
  // tmux composer passes ``[]`` because its backend doesn't accept it.
  localFallback?: ReadonlyArray<CommandCompletion>;
  textareaRef?: RefObject<HTMLTextAreaElement | null>;
}

export interface CommandCompletionsState {
  suggestions: ReadonlyArray<CommandCompletion>;
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
  localFallback = [SLASH_NEW_FALLBACK],
  textareaRef,
}: UseCommandCompletionsOptions): CommandCompletionsState {
  const [suggestionIndex, setSuggestionIndex] = useState(0);
  const [suggestionsDismissed, setSuggestionsDismissed] = useState(false);
  const [backendCompletions, setBackendCompletions] = useState<CommandCompletion[]>([]);
  const [selectedCompletion, setSelectedCompletion] =
    useState<CommandCompletion | null>(null);

  const listRef = useRef<HTMLUListElement | null>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const completionHead = draft.split(/\s/, 1)[0];
  const completionTrigger = completionHead.startsWith("/")
    ? "/"
    : completionHead.startsWith("$")
      ? "$"
      : null;

  const suggestions = useMemo<ReadonlyArray<CommandCompletion>>(() => {
    if (!enabled || suggestionsDismissed || completionTrigger === null) {
      return [];
    }
    const pool =
      completionTrigger === "/"
        ? mergeLocalFallback(backendCompletions, localFallback)
        : backendCompletions;
    return pool.filter((entry) => commandLabel(entry).startsWith(completionHead));
  }, [
    backendCompletions,
    completionHead,
    completionTrigger,
    enabled,
    localFallback,
    suggestionsDismissed,
  ]);

  const suggestionsOpen = suggestions.length > 0 && /^\S+$/.test(draft);
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
    if (!draft.startsWith("/") && !draft.startsWith("$")) {
      setSuggestionsDismissed(false);
    }
  }, [draft]);

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
    setDraft(chosen.replacement);
    setSelectedCompletion(chosen);
    setSuggestionsDismissed(true);
    if (textareaRef) {
      requestAnimationFrame(() => textareaRef.current?.focus());
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
    if (
      event.key === "Tab" ||
      (event.key === "Enter" &&
        !(event.metaKey || event.ctrlKey) &&
        !event.shiftKey)
    ) {
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
