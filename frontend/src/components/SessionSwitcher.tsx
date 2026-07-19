"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { connectSessionsSocket, fetchSessions } from "@/lib/api";
import { trapTabFocus } from "@/lib/keyboard";
import { matchesQuery, parseQuery } from "@/lib/search";
import { SessionEnvelope, SessionRecord } from "@/lib/types";
import { SearchInput } from "@/components/SearchInput";
import { humaniseBackend, useBackendCatalog } from "@/lib/backends";
import { useShowExitedSessions } from "@/lib/useShowExitedSessions";

interface SessionSwitcherProps {
  host: string;
  token: string;
  currentSession: SessionRecord | null;
  onAuthFailure?: () => void;
  onClose: () => void;
}

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

export function SessionSwitcher({ host, token, currentSession, onAuthFailure, onClose }: SessionSwitcherProps) {
  const router = useRouter();
  const catalog = useBackendCatalog(host || null, token || null, null);
  const toggleHint = typeof navigator !== "undefined" && /Mac/i.test(navigator.platform) ? "⌘K" : "Ctrl+K";
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [query, setQuery] = useState("");
  const [showExited, setShowExited] = useShowExitedSessions();
  const [page, setPage] = useState(1);
  const [activeIndex, setActiveIndex] = useState(0);
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

  useEffect(() => {
    let active = true;
    let socket: WebSocket | null = null;

    async function load() {
      try {
        const data = await fetchSessions(host, token);
        if (active) {
          setSessions(data);
        }
      } catch {
        if (active && typeof onAuthFailure === "function") {
          onAuthFailure();
        }
      }
    }
    load();

    socket = connectSessionsSocket(
      host,
      token,
      (message: SessionEnvelope) => {
        if (message.type === "session_list_update") {
          setSessions(message.payload.sessions as SessionRecord[]);
          return;
        }
        if (message.type === "session_state") {
          const updated = message.payload.session as SessionRecord;
          setSessions((current) => {
            const index = current.findIndex((s) => s.id === updated.id);
            if (index !== -1) {
              const next = [...current];
              next[index] = updated;
              return next;
            }
            return [updated, ...current];
          });
        }
      },
      () => {
        if (active && typeof onAuthFailure === "function") {
          onAuthFailure();
        }
      }
    );

    return () => {
      active = false;
      socket?.close();
    };
  }, [host, token, onAuthFailure]);

  const filteredSessions = useMemo(() => {
    let list = sessions.filter((s) => s.id !== currentSession?.id);
    const hasExited = list.some((s) => s.status === "exited");
    if (!showExited) {
      list = list.filter((s) => s.status !== "exited");
    }
    const parsed = parseQuery(query);
    list = list.filter((s) =>
      matchesQuery(s, parsed, ["title", "cwd", "repo_name", "branch", "backend", "search_status"]),
    );

    const pinned = list.filter((s) => s.pinned_at != null);
    const recent = list.filter((s) => s.pinned_at == null);

    recent.sort((a, b) => b.last_event_at.localeCompare(a.last_event_at));
    pinned.sort((a, b) => b.last_event_at.localeCompare(a.last_event_at));

    const totalPages = Math.ceil(recent.length / PAGE_SIZE) || 1;
    const cappedRecent = recent.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

    return {
      pinned,
      recent: cappedRecent,
      totalPages,
      totalRecent: recent.length,
      hasExited
    };
  }, [sessions, query, currentSession?.id, page, showExited]);

  const flatItems = useMemo(() => {
    return [...filteredSessions.pinned, ...filteredSessions.recent];
  }, [filteredSessions]);

  // Adjust active index if list shrinks
  useEffect(() => {
    if (activeIndex >= flatItems.length && flatItems.length > 0) {
      setActiveIndex(flatItems.length - 1);
    } else if (flatItems.length === 0) {
      setActiveIndex(0);
    }
  }, [flatItems.length, activeIndex]);

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

    const pinnedCount = filteredSessions.pinned.length;
    const totalPages = filteredSessions.totalPages;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (flatItems.length === 0) return;
      // At the last visible row with more pages remaining, advance the
      // page and land on the first recent item of the new page.
      if (activeIndex === flatItems.length - 1 && page < totalPages) {
        setPage(page + 1);
        setActiveIndex(pinnedCount);
        return;
      }
      setActiveIndex((i) => (i + 1) % flatItems.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (flatItems.length === 0) return;
      // At the first recent row with more pages preceding, step back a
      // page and land on the last recent item of the previous page
      // (which is always full since only the last page can be partial).
      if (activeIndex === pinnedCount && page > 1) {
        setPage(page - 1);
        setActiveIndex(pinnedCount + PAGE_SIZE - 1);
        return;
      }
      setActiveIndex((i) => (i - 1 + flatItems.length) % flatItems.length);
    } else if (e.key === "Home") {
      e.preventDefault();
      setActiveIndex(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setActiveIndex(flatItems.length > 0 ? flatItems.length - 1 : 0);
    } else if (e.key === "Enter") {
      e.preventDefault();
      const target = flatItems[activeIndex];
      if (target) {
        onClose();
        router.push(`/session/${target.id}`);
      }
    }
  }, [flatItems, activeIndex, onClose, router, filteredSessions.pinned.length, filteredSessions.totalPages, page]);

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
          if (activeIndex !== idx) setActiveIndex(idx);
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
              setActiveIndex(0);
            }}
            placeholder="Search sessions..."
            autoFocus={!isMobile}
            showStatusExample={false}
          />
        </div>

        {filteredSessions.hasExited ? (
          <div className="session-switcher-filter">
            <span className="session-switcher-filter-label">Show exited</span>
            <button
              type="button"
              role="switch"
              aria-checked={showExited}
              aria-label="Show exited sessions"
              className="switch"
              onClick={() => setShowExited(!showExited)}
            >
              <span className="switch-thumb" />
            </button>
          </div>
        ) : null}

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

          {filteredSessions.pinned.length > 0 ? (
            <div className="session-switcher-section">
              <div className="session-switcher-header">
                Pinned <span>{filteredSessions.pinned.length}</span>
              </div>
              {filteredSessions.pinned.map((s, i) => renderRow(s, i))}
            </div>
          ) : null}

          {filteredSessions.recent.length > 0 ? (
            <div className="session-switcher-section">
              <div className="session-switcher-header">
                Recent
                <span>
                  {(page - 1) * PAGE_SIZE + 1}–
                  {Math.min(page * PAGE_SIZE, filteredSessions.totalRecent)} of{" "}
                  {filteredSessions.totalRecent}
                </span>
              </div>
              {filteredSessions.recent.map((s, i) => renderRow(s, filteredSessions.pinned.length + i))}
              {filteredSessions.totalPages > 1 ? (
                <div className="session-switcher-pagination">
                  <button 
                    type="button" 
                    onClick={() => setPage(p => Math.max(1, p - 1))}
                    disabled={page === 1}
                  >
                    Previous
                  </button>
                  <span>Page {page} of {filteredSessions.totalPages}</span>
                  <button 
                    type="button" 
                    onClick={() => setPage(p => Math.min(filteredSessions.totalPages, p + 1))}
                    disabled={page === filteredSessions.totalPages}
                  >
                    Next
                  </button>
                </div>
              ) : null}
            </div>
          ) : null}

          {flatItems.length === 0 ? (
            <div className="session-switcher-empty">
              No matching sessions
            </div>
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
