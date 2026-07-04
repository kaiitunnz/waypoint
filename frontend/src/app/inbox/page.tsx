"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import { InboxItemPane } from "@/components/InboxItemPane";
import { InboxRow } from "@/components/InboxRow";
import { SearchInput } from "@/components/SearchInput";
import { ThemeToggle } from "@/components/ThemeToggle";
import {
  batchDeleteInboxItems,
  connectSessionsSocket,
  deleteInboxItem,
  deleteResolvedInboxItems,
  fetchInboxList,
  isAuthError,
} from "@/lib/api";
import { clearToken, readHost, readToken } from "@/lib/store";
import { useTheme } from "@/lib/theme";
import type { InboxItem, InboxStatus, SessionEnvelope } from "@/lib/types";

type StatusFilter = InboxStatus | "all";

const LIMIT = 30;

const STATUS_FILTERS: { id: StatusFilter; label: string }[] = [
  { id: "open", label: "Open" },
  { id: "resolved", label: "Resolved" },
  { id: "all", label: "All" },
];

function matchesFilter(
  item: InboxItem,
  filter: { status: StatusFilter; q: string },
): boolean {
  if (filter.status !== "all" && item.status !== filter.status) return false;
  const query = filter.q.trim().toLowerCase();
  if (query) {
    const hay = `${item.subject} ${item.from_label ?? ""}`.toLowerCase();
    if (!hay.includes(query)) return false;
  }
  return true;
}

function byUpdatedDesc(a: InboxItem, b: InboxItem): number {
  if (a.updated_at < b.updated_at) return 1;
  if (a.updated_at > b.updated_at) return -1;
  return 0;
}

function relativeTime(value: string): string {
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "";
  const sec = Math.round((Date.now() - then) / 1000);
  if (sec < 45) return "now";
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h`;
  const day = Math.round(hr / 24);
  if (day < 7) return `${day}d`;
  const wk = Math.round(day / 7);
  if (wk < 5) return `${wk}w`;
  return new Date(value).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

type BlockChip = { key: string; label: string; pending: boolean };

function blockChips(item: InboxItem): BlockChip[] {
  let questions = 0;
  let questionsPending = 0;
  let approvals = 0;
  let approvalsPending = 0;
  let files = 0;
  for (const block of item.blocks) {
    if (block.type === "question") {
      questions += 1;
      if (block.answer === null) questionsPending += 1;
    } else if (block.type === "approval") {
      approvals += 1;
      if (block.answer === null) approvalsPending += 1;
    } else if (block.type === "attachment") {
      files += 1;
    }
  }
  const chips: BlockChip[] = [];
  if (questions > 0)
    chips.push({
      key: "q",
      label: `${questions} question${questions > 1 ? "s" : ""}`,
      pending: questionsPending > 0,
    });
  if (approvals > 0)
    chips.push({
      key: "a",
      label: `${approvals} approval${approvals > 1 ? "s" : ""}`,
      pending: approvalsPending > 0,
    });
  if (files > 0)
    chips.push({
      key: "f",
      label: `${files} file${files > 1 ? "s" : ""}`,
      pending: false,
    });
  return chips;
}

function EnvelopeGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="34"
      height="34"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="m3 7 9 6 9-6" />
    </svg>
  );
}

function BulkCheckGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="12"
      height="12"
      fill="none"
      stroke="currentColor"
      strokeWidth="3"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

function DashGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="12"
      height="12"
      fill="none"
      stroke="currentColor"
      strokeWidth="3"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M6 12h12" />
    </svg>
  );
}

export default function InboxPage() {
  return (
    <Suspense fallback={null}>
      <InboxPageInner />
    </Suspense>
  );
}

function InboxPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { theme } = useTheme();
  const [host, setHost] = useState("");
  const [token, setToken] = useState("");
  const [items, setItems] = useState<InboxItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<StatusFilter>("open");
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mobileView, setMobileView] = useState<"list" | "item">("list");
  const [selectMode, setSelectMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const didInit = useRef(false);
  const filterRef = useRef({ status, q: debouncedQ });

  useEffect(() => {
    filterRef.current = { status, q: debouncedQ };
  }, [status, debouncedQ]);

  const handleAuthFailure = useCallback(() => {
    clearToken();
    setToken("");
    router.replace("/");
  }, [router]);

  useEffect(() => {
    setHost(readHost());
    setToken(readToken());
    if (!didInit.current) {
      didInit.current = true;
      const item = searchParams.get("item");
      if (item) {
        setSelectedId(item);
        setMobileView("item");
      }
    }
  }, [searchParams]);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQ(q), 250);
    return () => clearTimeout(timer);
  }, [q]);

  useEffect(() => {
    if (!host || !token) return;
    let active = true;
    setLoading(true);
    fetchInboxList(host, token, {
      status: status === "all" ? undefined : status,
      q: debouncedQ || undefined,
      limit: LIMIT,
    })
      .then((page) => {
        if (!active) return;
        setItems(page.items);
        setCursor(page.cursor);
        setHasMore(page.hasMore);
      })
      .catch((err) => {
        if (active && isAuthError(err)) handleAuthFailure();
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [host, token, status, debouncedQ, handleAuthFailure]);

  useEffect(() => {
    if (!host || !token) return;
    let active = true;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    function connect() {
      socket = connectSessionsSocket(
        host,
        token,
        (message: SessionEnvelope) => {
          if (message.type !== "inbox_update") return;
          const payload = message.payload;
          const id = payload.item_id as string;
          const deleted = Boolean(payload.deleted);
          const updated = (payload.item as InboxItem | null) ?? null;
          setItems((prev) => {
            const next = prev.filter((it) => it.id !== id);
            if (!deleted && updated && matchesFilter(updated, filterRef.current)) {
              next.unshift(updated);
            }
            next.sort(byUpdatedDesc);
            return next;
          });
        },
        () => {
          if (active) handleAuthFailure();
        },
        {
          onOpen: () => {
            attempt = 0;
          },
          onClose: () => {
            if (!active) return;
            const delay = Math.min(15000, 500 * 2 ** attempt);
            attempt += 1;
            reconnectTimer = setTimeout(connect, delay);
          },
        },
      );
    }
    connect();

    return () => {
      active = false;
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [host, token, handleAuthFailure]);

  const loadMore = useCallback(() => {
    if (!cursor || loading || !host || !token) return;
    setLoading(true);
    fetchInboxList(host, token, {
      status: status === "all" ? undefined : status,
      q: debouncedQ || undefined,
      limit: LIMIT,
      cursor,
    })
      .then((page) => {
        setItems((prev) => {
          const seen = new Set(prev.map((it) => it.id));
          return [...prev, ...page.items.filter((it) => !seen.has(it.id))];
        });
        setCursor(page.cursor);
        setHasMore(page.hasMore);
      })
      .catch((err) => {
        if (isAuthError(err)) handleAuthFailure();
      })
      .finally(() => setLoading(false));
  }, [cursor, loading, host, token, status, debouncedQ, handleAuthFailure]);

  const select = useCallback(
    (id: string) => {
      setSelectedId(id);
      setMobileView("item");
      router.replace(`/inbox?item=${encodeURIComponent(id)}`, { scroll: false });
    },
    [router],
  );

  const handleDeleted = useCallback(() => {
    setSelectedId(null);
    setMobileView("list");
    router.replace("/inbox", { scroll: false });
  }, [router]);

  const handleRowDelete = useCallback(
    async (id: string) => {
      try {
        await deleteInboxItem(host, token, id);
        // The inbox_update {deleted} WS event also removes it; this is a
        // belt-and-braces immediate removal. Only clear selection if the
        // deleted row was the open one.
        setItems((prev) => prev.filter((it) => it.id !== id));
        if (id === selectedId) handleDeleted();
      } catch (err) {
        if (isAuthError(err)) handleAuthFailure();
      }
    },
    [host, token, selectedId, handleDeleted, handleAuthFailure],
  );

  // Leaving the current filter/search invalidates any in-flight selection.
  useEffect(() => {
    setSelectMode(false);
    setSelectedIds(new Set());
  }, [status, debouncedQ]);

  // Keep the selection to ids still present (WS deletions, load-more churn).
  useEffect(() => {
    setSelectedIds((prev) => {
      if (prev.size === 0) return prev;
      const present = new Set(items.map((it) => it.id));
      const next = new Set<string>();
      for (const id of prev) if (present.has(id)) next.add(id);
      return next.size === prev.size ? prev : next;
    });
  }, [items]);

  const toggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const exitSelect = useCallback(() => {
    setSelectMode(false);
    setSelectedIds(new Set());
  }, []);

  const allSelected = items.length > 0 && selectedIds.size === items.length;
  const someSelected = selectedIds.size > 0 && !allSelected;

  const toggleSelectAll = useCallback(() => {
    setSelectedIds((prev) =>
      prev.size === items.length ? new Set() : new Set(items.map((it) => it.id)),
    );
  }, [items]);

  const handleBatchDelete = useCallback(async () => {
    const ids = [...selectedIds];
    if (ids.length === 0 || busy) return;
    if (
      typeof window !== "undefined" &&
      !window.confirm(
        `Delete ${ids.length} selected inbox item${ids.length > 1 ? "s" : ""}? This cannot be undone.`,
      )
    ) {
      return;
    }
    setBusy(true);
    try {
      const deleted = await batchDeleteInboxItems(host, token, ids);
      const gone = new Set(deleted);
      setItems((prev) => prev.filter((it) => !gone.has(it.id)));
      if (selectedId && gone.has(selectedId)) handleDeleted();
      exitSelect();
    } catch (err) {
      if (isAuthError(err)) handleAuthFailure();
    } finally {
      setBusy(false);
    }
  }, [
    selectedIds,
    busy,
    host,
    token,
    selectedId,
    handleDeleted,
    exitSelect,
    handleAuthFailure,
  ]);

  const handleDeleteResolved = useCallback(async () => {
    if (busy) return;
    if (
      typeof window !== "undefined" &&
      !window.confirm(
        "Delete all resolved inbox items? This cannot be undone.",
      )
    ) {
      return;
    }
    setBusy(true);
    try {
      const deleted = await deleteResolvedInboxItems(host, token);
      const gone = new Set(deleted);
      setItems((prev) => prev.filter((it) => !gone.has(it.id)));
      if (selectedId && gone.has(selectedId)) handleDeleted();
      exitSelect();
    } catch (err) {
      if (isAuthError(err)) handleAuthFailure();
    } finally {
      setBusy(false);
    }
  }, [busy, host, token, selectedId, handleDeleted, exitSelect, handleAuthFailure]);

  return (
    <div className="page-shell inbox-shell">
      <header className="app-bar">
        <div className="app-bar-brand">
          <Link className="app-bar-mark" href="/" aria-label="Waypoint home">
            <Image
              src={theme === "light" ? "/waypoint-light.svg" : "/waypoint.svg"}
              alt=""
              width={38}
              height={38}
              priority
            />
          </Link>
          <div className="app-bar-titles">
            <p className="app-bar-eyebrow">Waypoint · inbox</p>
            <h1 className="app-bar-title">Inbox</h1>
          </div>
        </div>
        <div className="app-bar-meta">
          <Link className="back-link" href="/">
            ← all sessions
          </Link>
          <ThemeToggle />
        </div>
      </header>

      {host && token ? (
        <div className="inbox-layout">
          <div
            className={`inbox-list-pane${mobileView === "item" ? " wp-mobile-hidden" : ""}`}
          >
            <div className="inbox-toolbar">
              <SearchInput
                value={q}
                onChange={setQ}
                placeholder="Search inbox…"
                showStatusExample={false}
              />
              <div className="inbox-controls">
                <div className="inbox-tabs" role="group" aria-label="Filter inbox">
                  {STATUS_FILTERS.map((filter) => (
                    <button
                      key={filter.id}
                      type="button"
                      aria-pressed={status === filter.id}
                      className={`inbox-tab${status === filter.id ? " active" : ""}`}
                      onClick={() => setStatus(filter.id)}
                    >
                      {filter.label}
                    </button>
                  ))}
                </div>
                <div className="inbox-actions">
                  <button
                    type="button"
                    className={`inbox-action${selectMode ? " active" : ""}`}
                    aria-pressed={selectMode}
                    disabled={!selectMode && items.length === 0}
                    onClick={() => (selectMode ? exitSelect() : setSelectMode(true))}
                  >
                    {selectMode ? "Done" : "Select"}
                  </button>
                  <button
                    type="button"
                    className="inbox-action danger"
                    disabled={busy}
                    onClick={handleDeleteResolved}
                  >
                    Delete resolved
                  </button>
                </div>
              </div>
            </div>
            <div className="inbox-list" role="list">
              {loading && items.length === 0
                ? Array.from({ length: 6 }).map((_, i) => (
                    <span key={i} className="inbox-row-skeleton" aria-hidden="true">
                      <span className="inbox-skel-bar short" />
                      <span className="inbox-skel-bar wide" />
                    </span>
                  ))
                : null}
              {!loading && items.length === 0 ? (
                <div className="inbox-empty inbox-empty-list">
                  <EnvelopeGlyph />
                  <p>Nothing here</p>
                </div>
              ) : null}
              {items.map((item) => (
                <InboxRow
                  key={item.id}
                  item={item}
                  active={item.id === selectedId}
                  chips={blockChips(item)}
                  timeLabel={relativeTime(item.updated_at)}
                  onSelect={select}
                  onDelete={handleRowDelete}
                  selectMode={selectMode}
                  selected={selectedIds.has(item.id)}
                  onToggleSelect={toggleSelect}
                />
              ))}
              {hasMore ? (
                <button
                  type="button"
                  className="inbox-load-more"
                  onClick={loadMore}
                  disabled={loading}
                >
                  {loading ? "Loading…" : "Load more"}
                </button>
              ) : null}
            </div>
            {selectMode && selectedIds.size > 0 ? (
              <div className="inbox-bulk-bar" role="toolbar" aria-label="Bulk actions">
                <button
                  type="button"
                  className={`inbox-check inbox-check-all${
                    allSelected ? " checked" : someSelected ? " mixed" : ""
                  }`}
                  role="checkbox"
                  aria-checked={allSelected ? true : someSelected ? "mixed" : false}
                  aria-label="Select all loaded items"
                  onClick={toggleSelectAll}
                >
                  {allSelected ? <BulkCheckGlyph /> : someSelected ? <DashGlyph /> : null}
                </button>
                <span className="inbox-bulk-count">
                  {selectedIds.size} selected
                </span>
                <span className="inbox-bulk-spacer" />
                <button
                  type="button"
                  className="inbox-bulk-btn danger"
                  disabled={busy}
                  onClick={handleBatchDelete}
                >
                  Delete
                </button>
                <button
                  type="button"
                  className="inbox-bulk-btn"
                  onClick={exitSelect}
                >
                  Cancel
                </button>
              </div>
            ) : null}
          </div>

          <div
            className={`inbox-item-pane${mobileView === "list" ? " wp-mobile-hidden" : ""}`}
          >
            {selectedId ? (
              <>
                <button
                  type="button"
                  className="link-button inbox-mobile-back"
                  onClick={() => setMobileView("list")}
                >
                  ← inbox
                </button>
                <InboxItemPane
                  host={host}
                  token={token}
                  itemId={selectedId}
                  onAuthFailure={handleAuthFailure}
                  onDeleted={handleDeleted}
                />
              </>
            ) : (
              <div className="inbox-empty inbox-empty-select">
                <EnvelopeGlyph />
                <p>Select an item to read it</p>
              </div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
