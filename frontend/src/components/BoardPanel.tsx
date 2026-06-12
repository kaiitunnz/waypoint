"use client";

import Link from "next/link";

import { formatRelativeTime } from "@/lib/usage";
import { BoardChannel } from "@/lib/types";

const MAX_ROWS = 5;

interface BoardPanelProps {
  channels: BoardChannel[];
}

export function BoardPanel({ channels }: BoardPanelProps) {
  // The API returns channels most-recently-active first.
  const rows = channels.slice(0, MAX_ROWS);
  const overflow = channels.length - rows.length;
  const totalPosts = channels.reduce((sum, c) => sum + c.entry_count, 0);

  return (
    <section className="panel board-summary" aria-label="Blackboard">
      <div className="board-summary-head">
        <div className="board-summary-titles">
          <p className="board-summary-eyebrow">Blackboard</p>
          <h2 className="board-summary-title">
            {channels.length === 0
              ? "Shared channels"
              : `${channels.length} channel${channels.length === 1 ? "" : "s"} · ${totalPosts} post${totalPosts === 1 ? "" : "s"}`}
          </h2>
        </div>
        <Link className="board-summary-link" href="/board">
          Open board →
        </Link>
      </div>

      {channels.length === 0 ? (
        <p className="board-summary-empty muted">
          No channels yet. Sessions coordinate by posting with{" "}
          <code>waypoint board post &lt;channel&gt; &lt;message&gt;</code>.
        </p>
      ) : (
        <ul className="board-summary-list">
          {rows.map((channel) => (
            <li key={channel.channel}>
              <Link
                className="board-summary-row"
                href={`/board?channel=${encodeURIComponent(channel.channel)}`}
              >
                <span className="board-summary-name">{channel.channel}</span>
                <span className="board-summary-meta">
                  <span className="board-summary-count">
                    {channel.entry_count}
                  </span>
                  <span className="board-summary-time">
                    {formatRelativeTime(channel.last_created_at)}
                  </span>
                </span>
              </Link>
            </li>
          ))}
          {overflow > 0 ? (
            <li className="board-summary-more">
              <Link href="/board">+{overflow} more</Link>
            </li>
          ) : null}
        </ul>
      )}
    </section>
  );
}
