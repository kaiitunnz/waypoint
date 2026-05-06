"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { connectSessionsSocket, fetchSessions } from "@/lib/api";
import { matchesQuery, parseQuery } from "@/lib/search";
import { SessionEnvelope, SessionRecord } from "@/lib/types";
import { SearchInput } from "@/components/SearchInput";
import { humaniseBackend } from "@/lib/backends";

interface SessionSwitcherProps {
  host: string;
  token: string;
  currentSession: SessionRecord | null;
  onAuthFailure?: () => void;
  onClose: () => void;
}

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
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [activeIndex, setActiveIndex] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

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
    const parsed = parseQuery(query);
    list = list.filter((s) =>
      matchesQuery(s, parsed, ["title", "cwd", "repo_name", "branch", "backend", "search_status"]),
    );

    const pinned = list.filter((s) => s.pinned_at != null);
    const recent = list.filter((s) => s.pinned_at == null);

    recent.sort((a, b) => b.last_event_at.localeCompare(a.last_event_at));
    pinned.sort((a, b) => b.last_event_at.localeCompare(a.last_event_at));

    const PAGE_SIZE = 8;
    const totalPages = Math.ceil(recent.length / PAGE_SIZE) || 1;
    const cappedRecent = recent.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
    
    return {
      pinned,
      recent: cappedRecent,
      totalPages,
      totalRecent: recent.length
    };
  }, [sessions, query, currentSession?.id, page]);

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
    
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => (i + 1) % flatItems.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
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
  }, [flatItems, activeIndex, onClose, router]);

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

  const renderRow = (s: SessionRecord, idx: number) => {
    const isSelected = activeIndex === idx;
    const cwdSegments = formatCwdSegments(s.cwd);
    const workspace = s.repo_name || cwdSegments[cwdSegments.length - 1];
    const target = s.launch_target_id || "Local";
    const breadcrumb = [humaniseBackend(s.backend), target, workspace].filter(Boolean).join(" ▸ ");
    
    return (
      <button 
        key={s.id}
        type="button"
        className={`session-switcher-row ${isSelected ? 'selected' : ''}`}
        onClick={() => {
          onClose();
          router.push(`/session/${s.id}`);
        }}
        onPointerMove={() => {
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
      <div className="session-switcher-modal" role="dialog" aria-modal="true" aria-label="Switch session">
        <div className="session-switcher-search">
          <SearchInput 
            value={query}
            onChange={(val) => {
              setQuery(val);
              setPage(1);
              setActiveIndex(0);
            }}
            placeholder="Search sessions..."
            autoFocus
            showStatusExample={false}
          />
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
                Recent <span>{filteredSessions.totalRecent}</span>
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
          ↑↓ navigate · ↵ open · esc close
        </div>
      </div>
    </div>
  );

  return typeof document !== "undefined" ? createPortal(content, document.body) : null;
}
