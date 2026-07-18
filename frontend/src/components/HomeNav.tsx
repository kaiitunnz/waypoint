"use client";

import Link from "next/link";

// Flat (non-glass) top navigation for the homepage: Sessions (current) plus
// links to the dedicated pages, with Scheduled pushed to the trailing edge
// opening the schedule sheet rather than a route. Counts are shown where cheap;
// Inbox carries a needs-you dot when unresolved.
interface HomeNavProps {
  activeSessions: number;
  boardChannels: number;
  telemetryAccounts: number | null;
  inboxCount: number;
  scheduledCount: number;
  onOpenScheduled: () => void;
}

export function HomeNav({
  activeSessions,
  boardChannels,
  telemetryAccounts,
  inboxCount,
  scheduledCount,
  onOpenScheduled,
}: HomeNavProps) {
  return (
    <nav className="home-nav" aria-label="Primary">
      <span className="home-nav-tab is-active" aria-current="page">
        Sessions
        {activeSessions > 0 ? (
          <span className="home-nav-count">{activeSessions} active</span>
        ) : null}
      </span>
      <Link className="home-nav-tab" href="/board">
        Board
        {boardChannels > 0 ? (
          <span className="home-nav-count">{boardChannels}</span>
        ) : null}
      </Link>
      <Link className="home-nav-tab" href="/telemetry">
        Telemetry
        {telemetryAccounts !== null && telemetryAccounts > 0 ? (
          <span className="home-nav-count">{telemetryAccounts}</span>
        ) : null}
      </Link>
      <Link className="home-nav-tab" href="/inbox">
        Inbox
        {inboxCount > 0 ? (
          <span
            className="home-nav-dot"
            aria-label={`${inboxCount} need you`}
          />
        ) : null}
      </Link>
      <Link className="home-nav-tab" href="/assistant">
        Assistant
      </Link>
      <span className="home-nav-spacer" />
      <button type="button" className="home-nav-tab" onClick={onOpenScheduled}>
        Scheduled
        {scheduledCount > 0 ? (
          <span className="home-nav-count">{scheduledCount}</span>
        ) : null}
      </button>
    </nav>
  );
}
