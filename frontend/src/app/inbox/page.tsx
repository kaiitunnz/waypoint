"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import { InboxItemPane } from "@/components/InboxItemPane";
import { SearchInput } from "@/components/SearchInput";
import { ThemeToggle } from "@/components/ThemeToggle";
import {
  connectSessionsSocket,
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
              <div className="inbox-tabs" role="tablist" aria-label="Filter inbox">
                {STATUS_FILTERS.map((filter) => (
                  <button
                    key={filter.id}
                    type="button"
                    role="tab"
                    aria-selected={status === filter.id}
                    className={`inbox-tab${status === filter.id ? " active" : ""}`}
                    onClick={() => setStatus(filter.id)}
                  >
                    {filter.label}
                  </button>
                ))}
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
              {items.map((item) => {
                const chips = blockChips(item);
                return (
                  <button
                    key={item.id}
                    type="button"
                    role="listitem"
                    className={`inbox-row${item.id === selectedId ? " active" : ""}${
                      item.read_at ? "" : " unread"
                    }`}
                    onClick={() => select(item.id)}
                  >
                    <span
                      className={`inbox-lamp inbox-status-${item.status}`}
                      aria-label={item.status}
                    />
                    <span className="inbox-row-main">
                      <span className="inbox-row-line">
                        <span className="inbox-row-from">
                          {item.from_label ?? "unknown"}
                        </span>
                        <span className="inbox-row-time">
                          {relativeTime(item.updated_at)}
                        </span>
                      </span>
                      <span className="inbox-row-subject">{item.subject}</span>
                      {chips.length > 0 ? (
                        <span className="inbox-row-tags">
                          {chips.map((chip) => (
                            <span
                              key={chip.key}
                              className={`inbox-row-tag${chip.pending ? " pending" : ""}`}
                            >
                              {chip.label}
                            </span>
                          ))}
                        </span>
                      ) : null}
                    </span>
                    {item.read_at ? null : (
                      <span className="inbox-row-unread" aria-label="unread" />
                    )}
                  </button>
                );
              })}
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
