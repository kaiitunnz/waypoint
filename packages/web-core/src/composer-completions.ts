"use client";

import type { KeyboardEvent, RefObject } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  fetchSessionCompletionsResponse,
  fetchWorkspaceFind,
  fetchWorkspaceTree,
  type WorkspaceTreeEntry,
} from "./api";
import { matchPath, rankItems, span } from "./fuzzy";
import { isInlineMenuAcceptKey } from "./keyboard";
import type {
  CommandCompletion,
  SessionCommandInvocation,
} from "./types";

const COMPLETION_FETCH_DEBOUNCE_MS = 180;
const COMPLETION_REFRESH_POLL_MS = 750;
const WORKSPACE_PATH_PAGE_LIMIT = 200;
// Each find runs `git ls-files` (over SSH when remote), so it debounces longer.
const WORKSPACE_FIND_DEBOUNCE_MS = 250;
const WORKSPACE_FIND_LIMIT = 20;
// A one-letter query fuzzy-hits nearly every file in the repo.
const WORKSPACE_FIND_MIN_CHARS = 2;
// Other characters (`/etc/hosts`) mean a path or prose: exact/prefix hits only.
const COMMAND_QUERY = /^[A-Za-z0-9_.:-]*$/;
const PATH_TRIGGER = "./";

// `/new` works on every structured backend so we surface it locally
// while the debounced fetch is in flight or if the request fails. A caller
// whose backend rejects `/new` (tmux) passes `localFallback: []`.
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
  section: "dir" | "workspace";
  // Matched character indices into ``replacement``.
  positions: number[];
}

interface CommandSuggestion {
  type: "command";
  command: CommandCompletion;
  // Matched character indices into ``commandLabel(command)``.
  positions: number[];
  descriptionPositions: number[];
}

// A menu row: a backend command or a workspace path. The discriminated union
// keeps a path from reaching command dispatch.
export type SuggestionRow = CommandSuggestion | WorkspacePathSuggestion;

// Match a Waypoint control command (e.g. ``/new``) and return its trailing
// argument string, or null when the text isn't that command. The trailing
// space requirement keeps ``/news`` from being read as ``/new``.
export function matchControlCommand(text: string, name: string): string | null {
  const command = `/${name}`;
  if (text === command) return "";
  if (text.startsWith(`${command} `)) return text.slice(command.length + 1).trim();
  return null;
}

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
  if (row.type === "command") return row.command.description ?? null;
  // Repo-wide rows are always files; the label would only crowd the path.
  return row.section === "workspace" ? null : row.description;
}

export function rowDescriptionPositions(row: SuggestionRow): number[] {
  return row.type === "command" ? row.descriptionPositions : [];
}

export function rowHint(row: SuggestionRow): string | null {
  return row.type === "command" ? row.command.argument_hint ?? null : null;
}

export function rowSection(row: SuggestionRow | undefined): "dir" | "workspace" | null {
  return row?.type === "workspace_path" ? row.section : null;
}

// Split a `./` caret token into the directory to list and the leaf being typed.
// Null when the token isn't `./`-rooted or a directory segment is `.`/`..`, so
// an out-of-scope prefix never becomes a valid query.
export function parseWorkspacePathToken(
  head: string,
): { dir: string; leaf: string } | null {
  if (!head.startsWith(PATH_TRIGGER)) return null;
  const fragment = head.slice(PATH_TRIGGER.length);
  const lastSlash = fragment.lastIndexOf("/");
  const dir = lastSlash === -1 ? "" : fragment.slice(0, lastSlash);
  const leaf = lastSlash === -1 ? fragment : fragment.slice(lastSlash + 1);
  const segments = dir.split("/").filter((segment) => segment.length > 0);
  if (segments.some((segment) => segment === "." || segment === "..")) {
    return null;
  }
  return { dir, leaf };
}

function dirPathRow(
  dir: string,
  entry: WorkspaceTreeEntry,
  leafLength: number,
): WorkspacePathSuggestion {
  const prefix = dir ? `${PATH_TRIGGER}${dir}/` : PATH_TRIGGER;
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
    section: "dir",
    positions: span(prefix.length, leafLength),
  };
}

function workspaceFindRow(
  path: string,
  fragment: string,
): WorkspacePathSuggestion {
  return {
    type: "workspace_path",
    replacement: `${PATH_TRIGGER}${path}`,
    description: "File",
    section: "workspace",
    positions: (matchPath(path, fragment) ?? []).map(
      (index) => index + PATH_TRIGGER.length,
    ),
  };
}

interface DirPage {
  dir: string;
  // Lowercased `name_prefix` the page was fetched with; "" for a full listing.
  namePrefix: string;
  entries: WorkspaceTreeEntry[];
  truncated: boolean;
}

interface FindPage {
  query: string;
  paths: string[];
}

// A truncated page answers only the exact prefix it was fetched with.
function pageCovers(
  page: DirPage | null,
  dir: string,
  leaf: string,
): page is DirPage {
  const needle = leaf.toLowerCase();
  return (
    page !== null &&
    page.dir === dir &&
    needle.startsWith(page.namePrefix) &&
    (needle === page.namePrefix || !page.truncated)
  );
}

// Falls back to the partial base page while a filtered fetch is in flight.
function dirEntriesFor(
  dir: string,
  leaf: string,
  base: DirPage | null,
  filtered: DirPage | null,
): WorkspaceTreeEntry[] {
  for (const page of [base, filtered]) {
    if (pageCovers(page, dir, leaf)) return page.entries;
  }
  return base?.dir === dir ? base.entries : [];
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
  // Override the local fallback list. Defaults to ``[SLASH_NEW_FALLBACK]``;
  // pass ``[]`` to disable.
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
  const [suggestionsDismissed, setSuggestionsDismissed] = useState(false);
  // The full list for one trigger, tagged so a `/` list never ranks under `$`.
  const [backendCompletions, setBackendCompletions] = useState<{
    trigger: string;
    entries: CommandCompletion[];
  } | null>(null);
  // The last workspace directory page, tagged with its directory so a stale
  // response for another token is ignored.
  const [pathPage, setPathPage] = useState<DirPage | null>(null);
  const [filteredPage, setFilteredPage] = useState<DirPage | null>(null);
  const [findPage, setFindPage] = useState<FindPage | null>(null);
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
  const isPathHead = completionHead.startsWith(PATH_TRIGGER);
  const onCompletableToken = completionTrigger !== null || isPathHead;
  const pathToken = useMemo(
    () =>
      pathCompletionEnabled && !suggestionsDismissed && isPathHead
        ? parseWorkspacePathToken(completionHead)
        : null,
    [completionHead, isPathHead, pathCompletionEnabled, suggestionsDismissed],
  );
  const pathFragment = completionHead.slice(PATH_TRIGGER.length);
  // Keyed on the directory so typing the leaf filters the loaded page.
  const pathQueryDir = pathToken?.dir ?? null;
  // A truncated listing can't answer the leaf; refetch filtered by name.
  const filteredNamePrefix =
    pathToken?.leaf &&
    pathPage?.dir === pathToken.dir &&
    !pageCovers(pathPage, pathToken.dir, pathToken.leaf) &&
    !pageCovers(filteredPage, pathToken.dir, pathToken.leaf)
      ? pathToken.leaf.toLowerCase()
      : null;
  // A bare `./dir/` only lists that directory.
  const findQuery =
    pathToken &&
    pathToken.leaf &&
    pathFragment.replaceAll("/", "").length >= WORKSPACE_FIND_MIN_CHARS
      ? pathFragment
      : null;

  const suggestions = useMemo<ReadonlyArray<SuggestionRow>>(() => {
    if (suggestionsDismissed) return [];
    // A token can't be both `./` and `/`/`$`, so at most one source is eligible.
    if (isPathHead) {
      if (!pathToken) return [];
      const { dir, leaf } = pathToken;
      const lowered = leaf.toLowerCase();
      const dirRows = dirEntriesFor(dir, leaf, pathPage, filteredPage)
        .filter((entry) => entry.name.toLowerCase().startsWith(lowered))
        .map((entry) => dirPathRow(dir, entry, leaf.length));
      if (findQuery === null || findPage === null) return dirRows;
      const seen = new Set(dirRows.map((row) => row.replacement));
      // Until the new results land, keep previous ones that still match.
      const paths =
        findPage.query === findQuery
          ? findPage.paths
          : findPage.paths.filter(
              (path) => matchPath(path, findQuery) !== null,
            );
      const workspaceRows = paths
        .map((path) => workspaceFindRow(path, findQuery))
        .filter((row) => !seen.has(row.replacement));
      return [...dirRows, ...workspaceRows];
    }
    if (!enabled || completionTrigger === null) return [];
    const loaded =
      backendCompletions?.trigger === completionTrigger
        ? backendCompletions.entries
        : [];
    const pool =
      completionTrigger === "/"
        ? mergeLocalFallback(loaded, localFallback)
        : loaded;
    const query = completionHead.slice(completionTrigger.length);
    return rankItems(
      pool,
      query,
      (entry) => entry.name,
      (entry) => entry.description,
      { prefixOnly: !COMMAND_QUERY.test(query) },
    ).map(({ item, positions, descriptionPositions }) => ({
      type: "command",
      command: item,
      positions: positions.map((index) => index + completionTrigger.length),
      descriptionPositions,
    }));
  }, [
    backendCompletions,
    completionHead,
    completionTrigger,
    enabled,
    filteredPage,
    findPage,
    findQuery,
    isPathHead,
    localFallback,
    pathPage,
    pathToken,
    suggestionsDismissed,
  ]);

  // ``suggestions`` is already empty unless the caret sits on a completable
  // token, so its presence is the open condition — no whole-draft test that
  // would suppress mid-prompt triggers.
  const suggestionsOpen = suggestions.length > 0;
  const { activeIndex, setActiveIndex, resetActive } = useActiveRow(
    suggestions,
    rowKey,
    completionHead,
  );

  useEffect(() => {
    if (!enabled || completionTrigger === null) {
      setBackendCompletions(null);
      return;
    }
    const trigger = completionTrigger;
    const controller = new AbortController();
    let debounceTimer: number | null = null;
    let pollTimer: number | null = null;
    const loadCompletions = () => {
      debounceTimer = null;
      fetchSessionCompletionsResponse(
        host,
        token,
        sessionId,
        trigger,
        false,
        controller.signal,
      )
        .then((payload) => {
          if (controller.signal.aborted) return;
          setBackendCompletions({ trigger, entries: payload.completions });
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
          setBackendCompletions(null);
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
  }, [host, token, sessionId, enabled, completionTrigger]);

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
          setPathPage({
            dir,
            namePrefix: "",
            entries: page.entries,
            truncated: page.truncated,
          });
        })
        .catch((error) => {
          if (error instanceof DOMException && error.name === "AbortError") {
            return;
          }
          // Disabled, 404, denied, or unreachable: stay silent. Drop only this
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
    if (pathQueryDir === null || filteredNamePrefix === null) return;
    const dir = pathQueryDir;
    const namePrefix = filteredNamePrefix;
    const controller = new AbortController();
    const debounceTimer = window.setTimeout(() => {
      fetchWorkspaceTree(host, token, sessionId, dir, {
        limit: WORKSPACE_PATH_PAGE_LIMIT,
        namePrefix,
        signal: controller.signal,
      })
        .then((page) => {
          if (controller.signal.aborted) return;
          setFilteredPage({
            dir,
            namePrefix,
            entries: page.entries,
            truncated: page.truncated,
          });
        })
        .catch(() => undefined);
    }, COMPLETION_FETCH_DEBOUNCE_MS);
    return () => {
      controller.abort();
      window.clearTimeout(debounceTimer);
    };
  }, [host, token, sessionId, pathQueryDir, filteredNamePrefix]);

  useEffect(() => {
    if (findQuery === null) return;
    const query = findQuery;
    const controller = new AbortController();
    const debounceTimer = window.setTimeout(() => {
      fetchWorkspaceFind(
        host,
        token,
        sessionId,
        query,
        WORKSPACE_FIND_LIMIT,
        controller.signal,
      )
        .then((result) => {
          if (controller.signal.aborted) return;
          setFindPage({
            query,
            paths: result.matches.map((match) => match.path),
          });
        })
        .catch((error) => {
          if (error instanceof DOMException && error.name === "AbortError") {
            return;
          }
          setFindPage({ query, paths: [] });
        });
    }, WORKSPACE_FIND_DEBOUNCE_MS);
    return () => {
      controller.abort();
      window.clearTimeout(debounceTimer);
    };
  }, [host, token, sessionId, findQuery]);

  useEffect(() => {
    if (!suggestionsOpen) return;
    const activeItem = itemRefs.current[activeIndex];
    const list = listRef.current;
    if (!activeItem || !list) return;
    const activeTop = activeItem.offsetTop;
    const activeBottom = activeTop + activeItem.offsetHeight;
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
      setActiveIndex(Math.min(suggestions.length - 1, activeIndex + 1));
      return true;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex(Math.max(0, activeIndex - 1));
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
    resetActive();
  }

  return {
    suggestions,
    suggestionsOpen,
    activeIndex,
    setActiveIndex,
    selectedCompletion,
    listRef,
    itemRefs,
    applySuggestion,
    selectedCommandInvocation,
    handleSuggestionKey,
    reset,
  };
}

// The highlighted row of an inline menu, tracked by key so rows landing
// asynchronously can't move it to another item. Scoped to the query it was
// picked under; until one is picked, it follows row 0.
export function useActiveRow<T>(
  rows: ReadonlyArray<T>,
  keyOf: (row: T) => string,
  scope: string,
) {
  const [active, setActive] = useState<{ scope: string; key: string } | null>(
    null,
  );
  const key = active?.scope === scope ? active.key : null;
  const activeIndex =
    key === null ? 0 : Math.max(0, rows.findIndex((row) => keyOf(row) === key));
  function setActiveIndex(index: number) {
    const row = rows[index];
    if (row) setActive({ scope, key: keyOf(row) });
  }
  return { activeIndex, setActiveIndex, resetActive: () => setActive(null) };
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

// The draft with a `/` or `@` completion trigger typed at `caret`, as a word of
// its own, and where the caret goes.
export function insertTrigger(draft: string, caret: number, trigger: "/" | "@"): { draft: string; caret: number } {
  const at = Math.max(0, Math.min(caret, draft.length));
  const before = draft.slice(0, at);
  const after = draft.slice(at);
  const inserted = `${before && !/\s$/.test(before) ? " " : ""}${trigger}`;
  return { draft: before + inserted + (after && !/^\s/.test(after) ? " " : "") + after, caret: at + inserted.length };
}
