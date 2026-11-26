"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { InboxItemPane } from "@/components/InboxItemPane";
import { InboxRow } from "@/components/InboxRow";
import { SearchInput } from "@/components/SearchInput";
import { ThemeToggle } from "@/components/ThemeToggle";
import {
  batchDeleteInboxItems,
  deleteInboxItem,
  deleteResolvedInboxItems,
  isAuthError,
} from "@waypoint/web-core/api";
import { useInboxList } from "@waypoint/web-core/hooks/useInboxList";
import { useResizablePane } from "@waypoint/web-core/hooks/useResizablePane";
import { useSelection } from "@waypoint/web-core/hooks/useSelection";
import {
  inboxBlockChips,
  inboxRelativeTime,
  type InboxStatusFilter,
} from "@waypoint/web-core/inbox";
import { clearToken, readHost, readToken } from "@waypoint/web-core/store";
import { useTheme } from "@/lib/theme";

const SIDEBAR: Parameters<typeof useResizablePane>[0] = {
  storageKey: "waypoint.inboxSidebarWidth",
  defaultWidth: 340,
  min: 260,
  max: 560,
  snap: 14,
};

function toggleResizingClass(dragging: boolean) {
  document.body.classList.toggle("wp-dock-resizing", dragging);
}

const STATUS_FILTERS: { id: InboxStatusFilter; label: string }[] = [
  { id: "open", label: "Open" },
  { id: "resolved", label: "Resolved" },
  { id: "all", label: "All" },
];

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
  const [status, setStatus] = useState<InboxStatusFilter>("open");
  const [q, setQ] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mobileView, setMobileView] = useState<"list" | "item">("list");
  const [busy, setBusy] = useState(false);
  const sidebar = useResizablePane(SIDEBAR, toggleResizingClass);
  const didInit = useRef(false);

  const handleAuthFailure = useCallback(() => {
    clearToken();
    setToken("");
    router.replace("/");
  }, [router]);

  const {
    items,
    query: debouncedQ,
    loading,
    hasMore,
    loadMore,
    remove,
  } = useInboxList({ host, token, status, q, onAuthFailure: handleAuthFailure });
  const itemIds = useMemo(() => items.map((it) => it.id), [items]);
  const {
    selecting: selectMode,
    selected: selectedIds,
    allState,
    start: startSelect,
    exit: exitSelect,
    toggle: toggleSelect,
    toggleAll: toggleSelectAll,
  } = useSelection(itemIds);

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
        remove(new Set([id]));
        if (id === selectedId) handleDeleted();
      } catch (err) {
        if (isAuthError(err)) handleAuthFailure();
      }
    },
    [host, token, selectedId, remove, handleDeleted, handleAuthFailure],
  );

  // Leaving the current filter/search invalidates any in-flight selection.
  useEffect(() => {
    exitSelect();
  }, [status, debouncedQ, exitSelect]);

  const allSelected = allState === "all";
  const someSelected = allState === "some";

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
      remove(gone);
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
    remove,
    handleDeleted,
    exitSelect,
    handleAuthFailure,
  ]);

  const handleDeleteResolved = useCallback(async () => {
    if (busy) return;
    if (
      typeof window !== "undefined" &&
      !window.confirm(
        "Delete every resolved inbox item, including any hidden by the current search? This cannot be undone.",
      )
    ) {
      return;
    }
    setBusy(true);
    try {
      const deleted = await deleteResolvedInboxItems(host, token);
      const gone = new Set(deleted);
      remove(gone);
      if (selectedId && gone.has(selectedId)) handleDeleted();
      exitSelect();
    } catch (err) {
      if (isAuthError(err)) handleAuthFailure();
    } finally {
      setBusy(false);
    }
  }, [busy, host, token, selectedId, remove, handleDeleted, exitSelect, handleAuthFailure]);

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
        <div
          className="inbox-layout"
          style={
            { "--inbox-sidebar-w": `${sidebar.rendered}px` } as React.CSSProperties
          }
        >
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
                    onClick={() => (selectMode ? exitSelect() : startSelect())}
                  >
                    {selectMode ? "Done" : "Select"}
                  </button>
                  {!selectMode && (status === "resolved" || status === "all") ? (
                    <button
                      type="button"
                      className="inbox-action inbox-action-danger"
                      disabled={busy}
                      onClick={handleDeleteResolved}
                    >
                      Delete resolved
                    </button>
                  ) : null}
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
                  chips={inboxBlockChips(item)}
                  timeLabel={inboxRelativeTime(item.updated_at)}
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
            {selectMode ? (
              <div className="inbox-bulk-bar" role="toolbar" aria-label="Bulk actions">
                <button
                  type="button"
                  className={`inbox-check inbox-check-all${
                    allSelected ? " checked" : someSelected ? " mixed" : ""
                  }`}
                  role="checkbox"
                  aria-checked={allSelected ? true : someSelected ? "mixed" : false}
                  aria-label="Select all loaded items"
                  disabled={items.length === 0}
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
                  className="inbox-bulk-btn inbox-bulk-btn-danger"
                  disabled={busy || selectedIds.size === 0}
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
            <div
              className="inbox-resizer"
              role="separator"
              aria-orientation="vertical"
              aria-label="Resize inbox sidebar"
              aria-valuenow={sidebar.rendered}
              aria-valuemin={SIDEBAR.min}
              aria-valuemax={SIDEBAR.max}
              tabIndex={0}
              onPointerDown={sidebar.startDrag}
              onKeyDown={sidebar.onKeyDown}
            />
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
