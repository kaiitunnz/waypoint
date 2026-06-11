"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { ThemeToggle } from "@/components/ThemeToggle";
import {
  clearBoardChannel,
  connectSessionsSocket,
  fetchBoardChannels,
  fetchBoardEntries,
  isAuthError,
  postBoardEntry,
} from "@/lib/api";
import { clearToken, readHost, readToken } from "@/lib/store";
import { useTheme } from "@/lib/theme";
import { BoardChannel, BoardEntry, SessionEnvelope } from "@/lib/types";

const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 15000;

type LoadState = "loading" | "ready" | "error";

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function shortId(value: string): string {
  return value.length > 14 ? `${value.slice(0, 14)}…` : value;
}

export default function BoardPage() {
  const router = useRouter();
  const { theme } = useTheme();
  const [host, setHost] = useState("");
  const [token, setToken] = useState("");
  const [channels, setChannels] = useState<BoardChannel[]>([]);
  const [activeChannel, setActiveChannel] = useState<string | null>(null);
  const [entries, setEntries] = useState<BoardEntry[]>([]);
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState("");
  const [draftChannel, setDraftChannel] = useState("");
  const [draftText, setDraftText] = useState("");
  const [draftKey, setDraftKey] = useState("");
  const [posting, setPosting] = useState(false);

  // Read inside the live socket handler without making the socket effect
  // depend on (and reconnect on) every channel switch.
  const activeChannelRef = useRef<string | null>(null);
  useEffect(() => {
    activeChannelRef.current = activeChannel;
  }, [activeChannel]);

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
      setActiveChannel((current) =>
        current && list.some((c) => c.channel === current)
          ? current
          : (list[0]?.channel ?? null),
      );
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
        setEntries(await fetchBoardEntries(host, token, channel));
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

  useEffect(() => {
    void refreshChannels();
  }, [refreshChannels]);

  useEffect(() => {
    if (activeChannel) {
      setDraftChannel(activeChannel);
      void refreshEntries(activeChannel);
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
          `Clear all entries in "${channel}"? This cannot be undone.`,
        )
      ) {
        return;
      }
      setError("");
      try {
        await clearBoardChannel(host, token, channel);
        await refreshChannels();
      } catch (err) {
        if (isAuthError(err)) {
          handleAuthFailure();
          return;
        }
        setError(err instanceof Error ? err.message : "failed to clear channel");
      }
    },
    [host, token, refreshChannels, handleAuthFailure],
  );

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

      <section className="panel board-compose" aria-label="Post to a channel">
        <div className="board-compose-row">
          <input
            className="board-input board-input-channel"
            placeholder="channel (e.g. topic:plan)"
            value={draftChannel}
            onChange={(event) => setDraftChannel(event.target.value)}
            aria-label="Channel"
          />
          <input
            className="board-input board-input-key"
            placeholder="key (optional — upserts a cell)"
            value={draftKey}
            onChange={(event) => setDraftKey(event.target.value)}
            aria-label="Key"
          />
        </div>
        <div className="board-compose-row">
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
            Post
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
        <section className="board-layout">
          <aside className="panel board-channels" aria-label="Channels">
            <h2 className="board-section-title">Channels</h2>
            <ul className="board-channel-list">
              {channels.map((channel) => (
                <li key={channel.channel}>
                  <button
                    type="button"
                    className={`board-channel${
                      channel.channel === activeChannel ? " is-active" : ""
                    }`}
                    onClick={() => setActiveChannel(channel.channel)}
                  >
                    <span className="board-channel-name">{channel.channel}</span>
                    <span className="board-channel-count">
                      {channel.entry_count}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </aside>

          <div className="panel board-entries">
            <div className="board-entries-head">
              <h2 className="board-section-title">{activeChannel}</h2>
              {activeChannel ? (
                <button
                  type="button"
                  className="board-clear"
                  onClick={() => void handleClear(activeChannel)}
                >
                  Clear channel
                </button>
              ) : null}
            </div>
            {entries.length === 0 ? (
              <p className="muted board-entries-empty">No entries.</p>
            ) : (
              <ul className="board-entry-list">
                {entries.map((entry) => (
                  <li key={entry.id} className="board-entry">
                    <div className="board-entry-head">
                      {entry.key ? (
                        <span className="board-entry-key">{entry.key}</span>
                      ) : null}
                      <span className="board-entry-author">
                        {entry.author_session_id
                          ? shortId(entry.author_session_id)
                          : "—"}
                      </span>
                      <span className="board-entry-time">
                        {formatTime(entry.created_at)}
                      </span>
                    </div>
                    <p className="board-entry-text">{entry.text}</p>
                    {Object.keys(entry.metadata).length > 0 ? (
                      <div className="board-entry-meta">
                        {Object.entries(entry.metadata).map(([key, value]) => (
                          <span key={key} className="board-meta-chip">
                            {key}={String(value)}
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
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
