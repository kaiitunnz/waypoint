"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { trapTabFocus } from "@waypoint/web-core/keyboard";
import { matchesQuery, parseQuery } from "@waypoint/web-core/search";
import {
  byRecency,
  isHistorySession,
  SESSION_SEARCH_FIELDS,
  useMergedSessionPage,
  useSessionHistory,
} from "@waypoint/web-core/sessionHistory";
import { SessionRecord } from "@waypoint/web-core/types";
import { SearchInput } from "@/components/SearchInput";
import { humaniseBackend, type BackendCatalog } from "@waypoint/web-core/backends";
import { SWITCHER_SHOW_EXITED_KEY, useShowExitedSessions } from "@waypoint/web-core/hooks/useShowExitedSessions";

interface SessionSwitcherProps {
  host: string;
  token: string;
  catalog: BackendCatalog;
  /** Cached live + pinned sessions; null until the first list frame arrives. */
  liveSessions: SessionRecord[] | null;
  historyCount: number;
  historyRevision: string;
  /** The cached list is being refreshed from the server. */
  syncing: boolean;
  currentSession: SessionRecord | null;
  onAuthFailure?: () => void;
  onClose: () => void;
}

// The highlighted row: a session id once known, or a row position while
// keyboard paging lands on a page whose rows haven't rendered yet.
type Selection = { id: string } | { index: number };

const PAGE_SIZE = 8;
// Window after a keypress during which mouse-hover updates to the
// active row are ignored, so list scroll under a stationary cursor
// doesn't snap the selection back.
const MOUSE_AFTER_KEY_SUPPRESS_MS = 300;

function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffMins < 1) return "just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 30) return `${diffDays}d ago`;
  return date.toLocaleDateString();
}

function formatCwdSegments(cwd: string): string[] {
  if (!cwd) return [""];
  const trimmed = cwd.replace(/\/+$/, "");
  const segments = trimmed.split("/").filter(Boolean);
  if (trimmed.startsWith("/")) {
    return segments.length ? [`/${segments[0]}`, ...segments.slice(1)] : ["/"];
  }
  return segments.length ? segments : [trimmed];
}

export function SessionSwitcher({
  host,
  token,
  catalog,
  liveSessions,
  historyCount,
  historyRevision,
  syncing,
  currentSession,
  onAuthFailure,
  onClose,
}: SessionSwitcherProps) {
  const router = useRouter();
  const toggleHint = typeof navigator !== "undefined" && /Mac/i.test(navigator.platform) ? "⌘K" : "Ctrl+K";
  const [query, setQuery] = useState("");
  const [showExited, setShowExited] = useShowExitedSessions(SWITCHER_SHOW_EXITED_KEY);
  const [page, setPage] = useState(1);
  const [selection, setSelection] = useState<Selection>({ index: 0 });
  const listRef = useRef<HTMLDivElement>(null);
  const modalRef = useRef<HTMLDivElement>(null);
  const lastKeyTimeRef = useRef(0);
  // Resolved synchronously on first render so SearchInput's autoFocus
  // prop is correct on mount (avoids the keyboard popping up on iOS).
  const [isMobile] = useState(() =>
    typeof window !== "undefined" && window.matchMedia("(max-width: 600px)").matches,
  );
  // Tracks the visible viewport on mobile via visualViewport — iOS
  // Safari's 100dvh doesn't shrink for the on-screen keyboard or
  // always exclude the bottom URL bar with viewportFit: cover, so we
  // size and position the modal from JS instead.
  const [mobileVV, setMobileVV] = useState<{
    height: number;
    width: number;
    offsetTop: number;
    offsetLeft: number;
  } | null>(null);

  const history = useSessionHistory({
    host,
    token,
    query,
    withItems: true,
    enabled: showExited,
    revision: historyRevision,
    pageSize: PAGE_SIZE,
    onAuthError: onAuthFailure,
  });

  const { pinned, recentLive, hasExited } = useMemo(() => {
    let list = (liveSessions ?? []).filter((s) => s.id !== currentSession?.id);
    const pinnedExited = list.some((s) => s.status === "exited");
    if (!showExited) {
      list = list.filter((s) => s.status !== "exited");
    }
    const parsed = parseQuery(query);
    list = list.filter((s) => matchesQuery(s, parsed, SESSION_SEARCH_FIELDS));
    return {
      pinned: list.filter((s) => s.pinned_at != null).sort(byRecency),
      recentLive: list.filter((s) => s.pinned_at == null),
      hasExited: pinnedExited || historyCount > 0,
    };
  }, [liveSessions, query, currentSession?.id, showExited, historyCount]);

  // The session being viewed is shown on its own above the list, so it leaves
  // history before paging; otherwise its page comes up a row short.
  const currentId = currentSession?.id ?? null;
  const currentCounted =
    currentSession !== null &&
    isHistorySession(currentSession) &&
    matchesQuery(currentSession, parseQuery(query), SESSION_SEARCH_FIELDS);
  const historyItems = useMemo(
    () => (currentId ? history.items.filter((s) => s.id !== currentId) : history.items),
    [history.items, currentId],
  );
  const historyTotal =
    history.total !== null && currentCounted ? Math.max(0, history.total - 1) : history.total;
  const recentPage = useMergedSessionPage(
    recentLive,
    { ...history, items: historyItems, total: historyTotal },
    showExited,
    page,
    PAGE_SIZE,
  );
  const recent = recentPage.items;
  const totalPages = recentPage.totalPages;

  const flatItems = useMemo(() => [...pinned, ...recent], [pinned, recent]);

  const selectedIndex =
    "id" in selection
      ? flatItems.findIndex((s) => s.id === selection.id)
      : selection.index;
  const [lastIndex, setLastIndex] = useState(0);
  const activeIndex =
    selectedIndex >= 0 && selectedIndex < flatItems.length
      ? selectedIndex
      : Math.min(lastIndex, Math.max(0, flatItems.length - 1));
  const activeId = flatItems[activeIndex]?.id ?? null;

  // Pin the selection to the session under it, so a background refresh that
  // reorders rows never moves the highlight (or Enter) to another session.
  // A position resolves to an id only once its row exists, so keyboard paging
  // onto a page still loading lands on that page's row, not the previous one.
  useEffect(() => {
    const inRange = selectedIndex >= 0 && selectedIndex < flatItems.length;
    if (inRange) setLastIndex(selectedIndex);
    const settled = "id" in selection ? selectedIndex !== -1 : !inRange;
    if (!settled && activeId) setSelection({ id: activeId });
  }, [activeId, selectedIndex, flatItems.length, selection]);

  const select = useCallback((index: number) => setSelection({ index }), []);

  const handleKeyDown = useCallback((e: globalThis.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
      return;
    }

    if (e.key === "Tab") {
      trapTabFocus(e, modalRef.current, { preventWhenEmpty: true });
      return;
    }

    const navKeys = ["ArrowDown", "ArrowUp", "Home", "End"];
    if (navKeys.includes(e.key)) {
      lastKeyTimeRef.current = Date.now();
    }

    const pinnedCount = pinned.length;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (flatItems.length === 0) return;
      // At the last visible row with more pages remaining, advance the
      // page and land on the first recent item of the new page.
      if (activeIndex === flatItems.length - 1 && page < totalPages) {
        setPage(page + 1);
        select(pinnedCount);
        return;
      }
      select((activeIndex + 1) % flatItems.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (flatItems.length === 0) return;
      // At the first recent row with more pages preceding, step back a
      // page and land on the last recent item of the previous page
      // (which is always full since only the last page can be partial).
      if (activeIndex === pinnedCount && page > 1) {
        setPage(page - 1);
        select(pinnedCount + PAGE_SIZE - 1);
        return;
      }
      select((activeIndex - 1 + flatItems.length) % flatItems.length);
    } else if (e.key === "Home") {
      e.preventDefault();
      select(0);
    } else if (e.key === "End") {
      e.preventDefault();
      select(flatItems.length > 0 ? flatItems.length - 1 : 0);
    } else if (e.key === "Enter") {
      e.preventDefault();
      const target = flatItems[activeIndex];
      if (target) {
        onClose();
        router.push(`/session/${target.id}`);
      }
    }
  }, [flatItems, activeIndex, onClose, router, pinned.length, totalPages, page, select]);

  useEffect(() => {
    window.addEventListener("keydown", handleKeyDown, { capture: true });
    return () => window.removeEventListener("keydown", handleKeyDown, { capture: true });
  }, [handleKeyDown]);

  // Scroll active item into view
  useEffect(() => {
    if (listRef.current) {
      const activeEl = listRef.current.querySelector('.selected');
      if (activeEl) {
        activeEl.scrollIntoView({ block: "nearest" });
      }
    }
  }, [activeIndex]);

  // Lock body scroll
  useEffect(() => {
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = originalOverflow;
    };
  }, []);

  // Track visualViewport on mobile so the modal always matches the
  // actual visible area (URL bar collapse, keyboard show/hide).
  useEffect(() => {
    if (!isMobile) return;
    const vv = window.visualViewport;
    if (!vv) {
      setMobileVV({
        height: window.innerHeight,
        width: window.innerWidth,
        offsetTop: 0,
        offsetLeft: 0,
      });
      return;
    }
    const update = () =>
      setMobileVV({
        height: vv.height,
        width: vv.width,
        offsetTop: vv.offsetTop,
        offsetLeft: vv.offsetLeft,
      });
    update();
    vv.addEventListener("resize", update);
    vv.addEventListener("scroll", update);
    return () => {
      vv.removeEventListener("resize", update);
      vv.removeEventListener("scroll", update);
    };
  }, [isMobile]);

  // Restore focus to whatever was focused before the modal opened
  // (typically the ⋯ trigger when opened from the overflow menu).
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    return () => {
      if (previous && typeof previous.focus === "function") {
        previous.focus();
      }
    };
  }, []);

  const historyBusy = showExited && (history.pending || recentPage.filling);
  const refreshing = syncing || (history.loading && history.loaded && !recentPage.filling);
  const historyNote = query.trim() ? "Searching older sessions…" : "Loading older sessions…";
  const emptyMessage =
    liveSessions === null
      ? "Loading sessions…"
      : historyBusy && !history.error
        ? historyNote
        : "No matching sessions";
  const tailStatus =
    showExited && history.error ? (
      <div className="session-switcher-tail" role="status">
        <span>Older sessions failed to load.</span>
        <button type="button" onClick={history.retry}>
          Retry
        </button>
      </div>
    ) : historyBusy && flatItems.length > 0 ? (
      <div className="session-switcher-tail" role="status">
        {historyNote}
      </div>
    ) : null;

  const renderRow = (s: SessionRecord, idx: number) => {
    const isSelected = activeIndex === idx;
    const cwdSegments = formatCwdSegments(s.cwd);
    const workspace = s.repo_name || cwdSegments[cwdSegments.length - 1];
    const target = s.launch_target_id || "Local";
    const breadcrumb = [humaniseBackend(s.backend, catalog), target, workspace].filter(Boolean).join(" ▸ ");
    
    return (
      <button 
        key={s.id}
        type="button"
        className={`session-switcher-row ${isSelected ? 'selected' : ''}`}
        onClick={() => {
          onClose();
          router.push(`/session/${s.id}`);
        }}
        onMouseEnter={() => {
          if (Date.now() - lastKeyTimeRef.current < MOUSE_AFTER_KEY_SUPPRESS_MS) return;
          if (activeIndex !== idx) setSelection({ id: s.id });
        }}
      >
        <div className={`session-switcher-rail ${s.status}`} />
        <div className="session-switcher-body">
          <div className="session-switcher-title">{s.title || "Untitled Session"}</div>
          <div className="session-switcher-breadcrumb">{breadcrumb}</div>
        </div>
        <div className="session-switcher-time">{formatRelativeTime(s.last_event_at)}</div>
      </button>
    );
  };

  const content = (
    <div className="session-switcher-backdrop" onPointerDown={(e) => {
      if (e.target === e.currentTarget) onClose();
    }}>
      <div
        className="session-switcher-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Switch session"
        ref={modalRef}
        style={
          isMobile && mobileVV !== null
            ? {
                // Pin to the visualViewport directly — flex centering
                // against the backdrop aligns to the layout viewport,
                // which extends behind the bottom URL bar on iOS
                // Safari (viewportFit: cover puts dvh under it).
                // 90% wide × 70% tall, centered in the visible area.
                position: "fixed",
                width: `${mobileVV.width * 0.9}px`,
                height: `${mobileVV.height * 0.7}px`,
                maxWidth: `${mobileVV.width * 0.9}px`,
                maxHeight: `${mobileVV.height * 0.7}px`,
                top: `${mobileVV.offsetTop + mobileVV.height * 0.15}px`,
                left: `${mobileVV.offsetLeft + mobileVV.width * 0.05}px`,
              }
            : undefined
        }
      >
        <div className="session-switcher-search">
          <SearchInput
            value={query}
            onChange={(val) => {
              setQuery(val);
              setPage(1);
              select(0);
            }}
            placeholder="Search sessions..."
            autoFocus={!isMobile}
            showStatusExample={false}
          />
          <span
            className="session-switcher-sync"
            data-active={refreshing ? "true" : undefined}
            role="status"
            aria-live="polite"
            title={refreshing ? "Refreshing sessions" : undefined}
          >
            <span className="sr-only">{refreshing ? "Refreshing sessions" : ""}</span>
          </span>
          {hasExited ? (
            <button
              type="button"
              className="exited-toggle"
              aria-pressed={!showExited}
              onClick={() => setShowExited(!showExited)}
            >
              {showExited ? "hide exited" : "show exited"}
            </button>
          ) : null}
        </div>

        <div className="session-switcher-list" ref={listRef}>
          {currentSession && !query ? (
            <div className="session-switcher-here">
              <div className={`session-switcher-rail ${currentSession.status}`} />
              <div className="session-switcher-body">
                <div className="session-switcher-title">{currentSession.title}</div>
                <div className="session-switcher-breadcrumb">Current session</div>
              </div>
            </div>
          ) : null}

          {pinned.length > 0 ? (
            <div className="session-switcher-section">
              <div className="session-switcher-header">
                Pinned <span>{pinned.length}</span>
              </div>
              {pinned.map((s, i) => renderRow(s, i))}
            </div>
          ) : null}

          {recent.length > 0 ? (
            <div className="session-switcher-section">
              <div className="session-switcher-header">
                Recent
                <span>
                  {(page - 1) * PAGE_SIZE + 1}–
                  {(page - 1) * PAGE_SIZE + recent.length} of {recentPage.total}
                </span>
              </div>
              {recent.map((s, i) => renderRow(s, pinned.length + i))}
              {tailStatus}
              {totalPages > 1 ? (
                <div className="session-switcher-pagination">
                  <button
                    type="button"
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={page === 1}
                  >
                    Previous
                  </button>
                  <span>Page {page} of {totalPages}</span>
                  <button
                    type="button"
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                    disabled={page === totalPages}
                  >
                    Next
                  </button>
                </div>
              ) : null}
            </div>
          ) : null}

          {flatItems.length === 0 ? (
            <div className="session-switcher-empty" role="status">
              {emptyMessage}
            </div>
          ) : recent.length === 0 ? (
            tailStatus
          ) : null}
        </div>

        <div className="session-switcher-footer">
          ↑↓ navigate · ↵ open · {toggleHint} toggle · esc close
        </div>
      </div>
    </div>
  );

  return typeof document !== "undefined" ? createPortal(content, document.body) : null;
}
