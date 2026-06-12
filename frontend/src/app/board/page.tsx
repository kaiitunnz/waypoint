"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";

import { ThemeToggle } from "@/components/ThemeToggle";
import {
  clearBoardChannel,
  connectSessionsSocket,
  deleteBoardChannel,
  deleteBoardEntry,
  fetchBoardChannel,
  fetchBoardChannels,
  isAuthError,
  postBoardEntry,
  updateBoardEntry,
} from "@/lib/api";
import { clearToken, readHost, readToken } from "@/lib/store";
import { useTheme } from "@/lib/theme";
import { BoardChannel, BoardEntry, SessionEnvelope } from "@/lib/types";
import { formatRelativeTime } from "@/lib/usage";

const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 15000;
const LOG_LIMIT = 100;

type LoadState = "loading" | "ready" | "error";

function shortId(value: string): string {
  return value.length > 16 ? `${value.slice(0, 16)}…` : value;
}

function MetaChips({ metadata }: { metadata: Record<string, unknown> }) {
  const keys = Object.keys(metadata);
  if (keys.length === 0) return null;
  return (
    <div className="board-meta">
      {keys.map((key) => (
        <span key={key} className="board-meta-chip">
          {key}={String(metadata[key])}
        </span>
      ))}
    </div>
  );
}

const stopEvent = (event: { stopPropagation: () => void }) =>
  event.stopPropagation();

interface EntryEditorProps {
  entry: BoardEntry;
  draft: string;
  saving: boolean;
  onChange: (value: string) => void;
  onSave: (entry: BoardEntry) => void;
  onCancel: () => void;
}

// Edits happen in place of the post text so you rewrite the content where it
// lives, not in a detached field. Text-only by design; the post keeps its key
// and metadata.
function EntryEditor({
  entry,
  draft,
  saving,
  onChange,
  onSave,
  onCancel,
}: EntryEditorProps) {
  const unchanged = !draft.trim() || draft === entry.text;
  return (
    <div className="board-edit" onClick={stopEvent}>
      <textarea
        className="board-edit-text"
        value={draft}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          event.stopPropagation();
          if (event.key === "Escape") {
            event.preventDefault();
            onCancel();
          } else if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
            event.preventDefault();
            if (!saving && !unchanged) onSave(entry);
          }
        }}
        rows={3}
        autoFocus
        aria-label="Edit post text"
      />
      <div className="board-edit-foot">
        <span className="board-edit-hint" aria-hidden="true">
          ⌘↵ save · esc cancel
        </span>
        <div className="board-edit-actions">
          <button
            type="button"
            className="board-action"
            onClick={onCancel}
            disabled={saving}
          >
            Cancel
          </button>
          <button
            type="button"
            className="board-action board-edit-save"
            onClick={() => onSave(entry)}
            disabled={saving || unchanged}
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

interface EntryDetailProps {
  entry: BoardEntry;
  confirmingDelete: boolean;
  onStartEdit: (entry: BoardEntry) => void;
  onRequestDelete: (entry: BoardEntry) => void;
  onConfirmDelete: (entry: BoardEntry) => void;
  onCancelDelete: () => void;
}

function EntryDetail({
  entry,
  confirmingDelete,
  onStartEdit,
  onRequestDelete,
  onConfirmDelete,
  onCancelDelete,
}: EntryDetailProps) {
  return (
    <div className="board-detail" onClick={stopEvent} onKeyDown={stopEvent}>
      <dl className="board-detail-rows">
        <div className="board-detail-row">
          <dt>entry</dt>
          <dd>#{entry.id}</dd>
        </div>
        <div className="board-detail-row">
          <dt>posted</dt>
          <dd>{new Date(entry.created_at).toLocaleString()}</dd>
        </div>
        {entry.edited_at ? (
          <div className="board-detail-row">
            <dt>edited</dt>
            <dd>{new Date(entry.edited_at).toLocaleString()}</dd>
          </div>
        ) : null}
        <div className="board-detail-row">
          <dt>author</dt>
          <dd>
            {entry.author_session_id ? (
              <Link
                className="board-detail-link"
                href={`/session/${entry.author_session_id}`}
              >
                {entry.author_session_id} →
              </Link>
            ) : (
              "no session"
            )}
          </dd>
        </div>
      </dl>

      {confirmingDelete ? (
        <div className="board-detail-actions board-confirm">
          <span className="board-confirm-text">Delete this post?</span>
          <button type="button" className="board-action" onClick={onCancelDelete}>
            Cancel
          </button>
          <button
            type="button"
            className="board-action board-action-danger"
            onClick={() => onConfirmDelete(entry)}
          >
            Delete
          </button>
        </div>
      ) : (
        <div className="board-detail-actions">
          <button
            type="button"
            className="board-action"
            onClick={() => onStartEdit(entry)}
          >
            Edit
          </button>
          <button
            type="button"
            className="board-action board-action-danger"
            onClick={() => onRequestDelete(entry)}
          >
            Delete
          </button>
        </div>
      )}
    </div>
  );
}

export default function BoardPage() {
  const router = useRouter();
  const { theme } = useTheme();
  const [host, setHost] = useState("");
  const [token, setToken] = useState("");
  const [channels, setChannels] = useState<BoardChannel[]>([]);
  const [activeChannel, setActiveChannel] = useState<string | null>(null);
  const [entries, setEntries] = useState<BoardEntry[]>([]);
  const [logTotal, setLogTotal] = useState(0);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState("");
  const [draftChannel, setDraftChannel] = useState("");
  const [draftText, setDraftText] = useState("");
  const [draftKey, setDraftKey] = useState("");
  const [posting, setPosting] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);

  // Expand/collapse a card. Never collapse the card you're editing (that would
  // drop the draft); activating a different card cancels any open edit/confirm
  // so only one entry is ever interactive at a time.
  const activateEntry = (id: number) => {
    if (editingId === id) return;
    if (editingId !== null) {
      setEditingId(null);
      setEditDraft("");
    }
    setConfirmDeleteId(null);
    setExpandedId((current) => (current === id ? null : id));
  };

  const onEntryKeyDown = (event: KeyboardEvent, id: number) => {
    if (editingId === id) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activateEntry(id);
    }
  };

  // Read inside the live socket handler without making the socket effect
  // depend on (and reconnect on) every channel switch.
  const activeChannelRef = useRef<string | null>(null);
  useEffect(() => {
    activeChannelRef.current = activeChannel;
  }, [activeChannel]);

  // A `?channel=` deep link selects that channel once it loads.
  const pendingChannelRef = useRef<string | null>(null);

  const handleAuthFailure = useCallback(() => {
    clearToken();
    setToken("");
    router.replace("/");
  }, [router]);

  useEffect(() => {
    const currentHost = readHost();
    const currentToken = readToken();
    setHost(currentHost);
    setToken(currentToken);
    const requested = new URLSearchParams(window.location.search).get("channel");
    if (requested) pendingChannelRef.current = requested;
    if (!currentHost || !currentToken) {
      router.replace("/");
    }
  }, [router]);

  const refreshChannels = useCallback(async () => {
    if (!host || !token) return;
    try {
      const list = await fetchBoardChannels(host, token);
      setChannels(list);
      setState("ready");
      setActiveChannel((current) => {
        const pending = pendingChannelRef.current;
        if (pending && list.some((c) => c.channel === pending)) {
          pendingChannelRef.current = null;
          return pending;
        }
        if (current && list.some((c) => c.channel === current)) return current;
        return list[0]?.channel ?? null;
      });
    } catch (err) {
      if (isAuthError(err)) {
        handleAuthFailure();
        return;
      }
      setState("error");
    }
  }, [host, token, handleAuthFailure]);

  const refreshEntries = useCallback(
    async (channel: string) => {
      if (!host || !token) return;
      try {
        const page = await fetchBoardChannel(host, token, channel, {
          limit: LOG_LIMIT,
        });
        setEntries(page.entries);
        setLogTotal(page.logTotal);
      } catch (err) {
        if (isAuthError(err)) {
          handleAuthFailure();
          return;
        }
        setError(err instanceof Error ? err.message : "failed to load entries");
      }
    },
    [host, token, handleAuthFailure],
  );

  const loadOlder = useCallback(async () => {
    if (!host || !token || !activeChannel) return;
    const oldestLogId = entries
      .filter((entry) => !entry.key)
      .reduce<number | null>(
        (min, entry) => (min === null || entry.id < min ? entry.id : min),
        null,
      );
    if (oldestLogId === null) return;
    setLoadingOlder(true);
    try {
      const page = await fetchBoardChannel(host, token, activeChannel, {
        limit: LOG_LIMIT,
        before: oldestLogId,
      });
      // Older rows have ids below everything loaded, so prepend keeps the
      // append-log in ascending id order; cells already loaded are untouched.
      setEntries((prev) => [...page.entries, ...prev]);
      setLogTotal(page.logTotal);
    } catch (err) {
      if (isAuthError(err)) {
        handleAuthFailure();
        return;
      }
      setError(err instanceof Error ? err.message : "failed to load older posts");
    } finally {
      setLoadingOlder(false);
    }
  }, [host, token, activeChannel, entries, handleAuthFailure]);

  useEffect(() => {
    void refreshChannels();
  }, [refreshChannels]);

  useEffect(() => {
    setExpandedId(null);
    setEditingId(null);
    setEditDraft("");
    setConfirmDeleteId(null);
    if (activeChannel) {
      setDraftChannel(activeChannel);
      void refreshEntries(activeChannel);
      // Keep the URL shareable without triggering a navigation.
      window.history.replaceState(
        null,
        "",
        `/board?channel=${encodeURIComponent(activeChannel)}`,
      );
    } else {
      setEntries([]);
    }
  }, [activeChannel, refreshEntries]);

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
          if (message.type === "board_update") {
            void refreshChannels();
            const channel = message.payload.channel as string | null;
            const current = activeChannelRef.current;
            if (current && (channel === null || channel === current)) {
              void refreshEntries(current);
            }
          }
          if (message.type === "auth_revoked") {
            handleAuthFailure();
          }
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
            const delay = Math.min(
              RECONNECT_MAX_MS,
              RECONNECT_BASE_MS * 2 ** attempt,
            );
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
  }, [host, token, refreshChannels, refreshEntries, handleAuthFailure]);

  const handlePost = useCallback(async () => {
    const channel = draftChannel.trim();
    const text = draftText.trim();
    if (!host || !token || !channel || !text) return;
    setPosting(true);
    setError("");
    try {
      await postBoardEntry(host, token, channel, {
        text,
        key: draftKey.trim() || null,
      });
      setDraftText("");
      setDraftKey("");
      setActiveChannel(channel);
      await refreshChannels();
      await refreshEntries(channel);
    } catch (err) {
      if (isAuthError(err)) {
        handleAuthFailure();
        return;
      }
      setError(err instanceof Error ? err.message : "failed to post entry");
    } finally {
      setPosting(false);
    }
  }, [
    host,
    token,
    draftChannel,
    draftText,
    draftKey,
    refreshChannels,
    refreshEntries,
    handleAuthFailure,
  ]);

  const handleClear = useCallback(
    async (channel: string) => {
      if (!host || !token) return;
      if (
        !window.confirm(
          `Remove all posts from "${channel}"? The channel stays. This cannot be undone.`,
        )
      ) {
        return;
      }
      setError("");
      try {
        await clearBoardChannel(host, token, channel);
        await refreshChannels();
        await refreshEntries(channel);
      } catch (err) {
        if (isAuthError(err)) {
          handleAuthFailure();
          return;
        }
        setError(err instanceof Error ? err.message : "failed to clear channel");
      }
    },
    [host, token, refreshChannels, refreshEntries, handleAuthFailure],
  );

  const handleDeleteChannel = useCallback(
    async (channel: string) => {
      if (!host || !token) return;
      if (
        !window.confirm(
          `Delete channel "${channel}" and all its posts? This cannot be undone.`,
        )
      ) {
        return;
      }
      setError("");
      try {
        await deleteBoardChannel(host, token, channel);
        await refreshChannels();
      } catch (err) {
        if (isAuthError(err)) {
          handleAuthFailure();
          return;
        }
        setError(err instanceof Error ? err.message : "failed to delete channel");
      }
    },
    [host, token, refreshChannels, handleAuthFailure],
  );

  const startEdit = useCallback((entry: BoardEntry) => {
    setEditingId(entry.id);
    setEditDraft(entry.text);
  }, []);

  const cancelEdit = useCallback(() => {
    setEditingId(null);
    setEditDraft("");
  }, []);

  const handleEditSave = useCallback(
    async (entry: BoardEntry) => {
      if (!host || !token || !activeChannel) return;
      const text = editDraft.trim();
      if (!text || text === entry.text) return;
      setSavingEdit(true);
      setError("");
      try {
        // Edits are text-only here; resend the existing metadata so a text
        // change doesn't wipe the post's chips.
        await updateBoardEntry(host, token, activeChannel, entry.id, {
          text,
          metadata: entry.metadata,
        });
        setEditingId(null);
        setEditDraft("");
        await refreshEntries(activeChannel);
      } catch (err) {
        if (isAuthError(err)) {
          handleAuthFailure();
          return;
        }
        setError(err instanceof Error ? err.message : "failed to edit post");
      } finally {
        setSavingEdit(false);
      }
    },
    [host, token, activeChannel, editDraft, refreshEntries, handleAuthFailure],
  );

  const requestDelete = useCallback((entry: BoardEntry) => {
    setConfirmDeleteId(entry.id);
  }, []);

  const cancelDelete = useCallback(() => {
    setConfirmDeleteId(null);
  }, []);

  const handleDeleteEntry = useCallback(
    async (entry: BoardEntry) => {
      if (!host || !token || !activeChannel) return;
      setError("");
      try {
        await deleteBoardEntry(host, token, activeChannel, entry.id);
        setConfirmDeleteId((current) =>
          current === entry.id ? null : current,
        );
        if (expandedId === entry.id) setExpandedId(null);
        if (editingId === entry.id) setEditingId(null);
        await refreshChannels();
        await refreshEntries(activeChannel);
      } catch (err) {
        if (isAuthError(err)) {
          handleAuthFailure();
          return;
        }
        setError(err instanceof Error ? err.message : "failed to delete post");
      }
    },
    [
      host,
      token,
      activeChannel,
      expandedId,
      editingId,
      refreshChannels,
      refreshEntries,
      handleAuthFailure,
    ],
  );

  // The two shapes the board is built on: keyed cells (latest-wins
  // variables, pinned) and the append-only log (newest first).
  const cells = entries
    .filter((entry) => entry.key)
    .sort((a, b) => (a.key ?? "").localeCompare(b.key ?? ""));
  const log = entries.filter((entry) => !entry.key).reverse();
  const hasOlder = log.length < logTotal;

  return (
    <main className="page-shell">
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
            <p className="app-bar-eyebrow">Waypoint · board</p>
            <h1 className="app-bar-title">Blackboard</h1>
          </div>
        </div>
        <div className="app-bar-meta">
          <Link className="back-link" href="/">
            ← all sessions
          </Link>
          <ThemeToggle />
        </div>
      </header>

      {error ? (
        <div className="error-banner" role="alert">
          <span>{error}</span>
          <button
            className="error-banner-dismiss"
            onClick={() => setError("")}
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      ) : null}

      <section className="panel board-composer" aria-label="Post to a channel">
        <div className="board-composer-fields">
          <input
            className="board-input board-input-channel"
            placeholder="channel — e.g. topic:plan"
            value={draftChannel}
            onChange={(event) => setDraftChannel(event.target.value)}
            aria-label="Channel"
          />
          <span className="board-composer-sep" aria-hidden="true">
            /
          </span>
          <input
            className="board-input board-input-key"
            placeholder="key — blank appends to the log"
            value={draftKey}
            onChange={(event) => setDraftKey(event.target.value)}
            aria-label="Key"
          />
        </div>
        <div className="board-composer-row">
          <input
            className="board-input board-input-text"
            placeholder="message"
            value={draftText}
            onChange={(event) => setDraftText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void handlePost();
            }}
            aria-label="Message"
          />
          <button
            type="button"
            className="primary"
            onClick={() => void handlePost()}
            disabled={posting || !draftChannel.trim() || !draftText.trim()}
          >
            {draftKey.trim() ? "Set cell" : "Post"}
          </button>
        </div>
      </section>

      {state === "ready" && channels.length === 0 ? (
        <section className="panel bordered board-empty">
          <h2>The board is empty</h2>
          <p className="muted">
            Nothing has been posted yet. Sessions post with{" "}
            <code>waypoint board post &lt;channel&gt; &lt;message&gt;</code>, or
            use the composer above.
          </p>
        </section>
      ) : null}

      {state === "ready" && channels.length > 0 ? (
        <section className="board-grid">
          <aside className="panel board-rail" aria-label="Channels">
            <div className="board-rail-head">
              <h2 className="board-rail-title">Channels</h2>
              <span className="board-rail-count">{channels.length}</span>
            </div>
            <ul className="board-rail-list">
              {channels.map((channel) => (
                <li key={channel.channel}>
                  <button
                    type="button"
                    className={`board-rail-item${
                      channel.channel === activeChannel ? " is-active" : ""
                    }`}
                    onClick={() => setActiveChannel(channel.channel)}
                  >
                    <span className="board-rail-name">{channel.channel}</span>
                    <span className="board-rail-meta">
                      <span className="board-rail-badge">
                        {channel.entry_count}
                      </span>
                      <span className="board-rail-time">
                        {formatRelativeTime(channel.last_created_at)}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </aside>

          <div className="board-main">
            {activeChannel ? (
              <div className="board-main-head">
                <div>
                  <p className="board-main-eyebrow">channel</p>
                  <h2 className="board-main-title">{activeChannel}</h2>
                </div>
                <div className="board-actions">
                  <button
                    type="button"
                    className="board-action"
                    onClick={() => void handleClear(activeChannel)}
                  >
                    Clear posts
                  </button>
                  <button
                    type="button"
                    className="board-action board-action-danger"
                    onClick={() => void handleDeleteChannel(activeChannel)}
                  >
                    Delete channel
                  </button>
                </div>
              </div>
            ) : null}

            {cells.length > 0 ? (
              <section aria-label="Cells">
                <h3 className="board-group-label">Cells · latest value</h3>
                <div className="board-cell-grid">
                  {cells.map((entry) => (
                    <article
                      key={entry.id}
                      className={`board-cell${
                        expandedId === entry.id ? " is-expanded" : ""
                      }`}
                      role="button"
                      tabIndex={0}
                      aria-expanded={expandedId === entry.id}
                      onClick={() => activateEntry(entry.id)}
                      onKeyDown={(event) => onEntryKeyDown(event, entry.id)}
                    >
                      <header className="board-cell-head">
                        <span className="board-cell-key">{entry.key}</span>
                        <span className="board-cell-time">
                          {formatRelativeTime(entry.created_at)}
                          {entry.edited_at ? (
                            <span className="board-edited"> · edited</span>
                          ) : null}
                        </span>
                      </header>
                      {editingId === entry.id ? (
                        <EntryEditor
                          entry={entry}
                          draft={editDraft}
                          saving={savingEdit}
                          onChange={setEditDraft}
                          onSave={handleEditSave}
                          onCancel={cancelEdit}
                        />
                      ) : (
                        <p className="board-cell-value">{entry.text}</p>
                      )}
                      <footer className="board-cell-foot">
                        <span className="board-cell-author">
                          {entry.author_session_id
                            ? shortId(entry.author_session_id)
                            : "—"}
                        </span>
                      </footer>
                      <MetaChips metadata={entry.metadata} />
                      {expandedId === entry.id && editingId !== entry.id ? (
                        <EntryDetail
                          entry={entry}
                          confirmingDelete={confirmDeleteId === entry.id}
                          onStartEdit={startEdit}
                          onRequestDelete={requestDelete}
                          onConfirmDelete={handleDeleteEntry}
                          onCancelDelete={cancelDelete}
                        />
                      ) : null}
                    </article>
                  ))}
                </div>
              </section>
            ) : null}

            <section aria-label="Log">
              {cells.length > 0 ? (
                <h3 className="board-group-label">Log · newest first</h3>
              ) : null}
              {log.length === 0 ? (
                <p className="board-log-empty">
                  {cells.length > 0
                    ? "No log posts in this channel."
                    : "No entries yet."}
                </p>
              ) : (
                <ol className="board-log-list">
                  {log.map((entry) => (
                    <li key={entry.id} className="board-log-item">
                      <div className="board-log-rail" aria-hidden="true" />
                      <div
                        className={`board-log-body${
                          expandedId === entry.id ? " is-expanded" : ""
                        }`}
                        role="button"
                        tabIndex={0}
                        aria-expanded={expandedId === entry.id}
                        onClick={() => activateEntry(entry.id)}
                        onKeyDown={(event) => onEntryKeyDown(event, entry.id)}
                      >
                        <div className="board-log-head">
                          <span className="board-log-author">
                            {entry.author_session_id
                              ? shortId(entry.author_session_id)
                              : "—"}
                          </span>
                          <span className="board-log-time">
                            {formatRelativeTime(entry.created_at)}
                            {entry.edited_at ? (
                              <span className="board-edited"> · edited</span>
                            ) : null}
                          </span>
                        </div>
                        {editingId === entry.id ? (
                          <EntryEditor
                            entry={entry}
                            draft={editDraft}
                            saving={savingEdit}
                            onChange={setEditDraft}
                            onSave={handleEditSave}
                            onCancel={cancelEdit}
                          />
                        ) : (
                          <p className="board-log-text">{entry.text}</p>
                        )}
                        <MetaChips metadata={entry.metadata} />
                        {expandedId === entry.id && editingId !== entry.id ? (
                          <EntryDetail
                            entry={entry}
                            confirmingDelete={confirmDeleteId === entry.id}
                            onStartEdit={startEdit}
                            onRequestDelete={requestDelete}
                            onConfirmDelete={handleDeleteEntry}
                            onCancelDelete={cancelDelete}
                          />
                        ) : null}
                      </div>
                    </li>
                  ))}
                </ol>
              )}
              {hasOlder ? (
                <div className="board-log-more">
                  <button
                    type="button"
                    className="board-load-older"
                    onClick={() => void loadOlder()}
                    disabled={loadingOlder}
                  >
                    {loadingOlder ? "Loading…" : "Load older"}
                  </button>
                  <span className="board-log-count">
                    {log.length} of {logTotal}
                  </span>
                </div>
              ) : null}
            </section>
          </div>
        </section>
      ) : null}

      {state === "loading" ? (
        <section className="panel bordered board-empty" aria-busy="true">
          <p className="muted">Loading board…</p>
        </section>
      ) : null}

      {state === "error" ? (
        <section className="panel bordered board-empty">
          <h2>Couldn’t load the board</h2>
          <p className="muted">
            The backend didn’t respond. Check that Waypoint is running, then
            retry.
          </p>
          <button
            type="button"
            className="primary"
            onClick={() => void refreshChannels()}
          >
            Retry
          </button>
        </section>
      ) : null}
    </main>
  );
}
