"use client";

import Link from "next/link";
import { useLayoutEffect, useMemo, useRef } from "react";

import { useNow } from "@waypoint/web-core/hooks/useNow";
import { useSessionActivity } from "@waypoint/web-core/hooks/useSessionActivity";
import { formatSessionTime, lampWord, sessionLamp } from "@waypoint/web-core/sessionPresentation";
import { sessionHref } from "@waypoint/web-core/shellNav";
import { rankSessionsForSwitcher, SWITCHER_SECTION_LABELS, type SwitcherSection } from "@waypoint/web-core/switcher";
import type { SessionRecord } from "@waypoint/web-core/types";

import { ActivityLine } from "@/components/sessions/ActivityLine";
import { ExitedGroup } from "@/components/sessions/ExitedGroup";
import { useSwitcher } from "@/components/sessions/SwitcherProvider";
import { useExitedSessions } from "@/components/sessions/useExitedSessions";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { StatusLamp } from "@/components/ui/StatusLamp";
import { handleAuthFailure } from "@/lib/auth";
import { useShowExited } from "@/lib/show-exited";
import { cx } from "@/lib/cx";
import { useSessionsData, useShellData } from "@/lib/shell-data";

import { PanelButton } from "./PanelButton";
import styles from "./SessionRail.module.css";

const SECTIONS: SwitcherSection[] = ["needs", "pinned", "live"];

// The rail mounts again on every session switch; this is where it was scrolled to.
let scrollTop = 0;

interface SessionRailProps {
  currentId: string;
  // For the rail drawn as a drawer: closing it, and a session picked in it.
  onClose?: () => void;
  onPick?: (anotherSession: boolean) => void;
}

export function SessionRail({ currentId, onClose, onPick }: SessionRailProps) {
  const { host, token } = useShellData();
  const feed = useSessionsData();
  const switcher = useSwitcher();
  const now = useNow();
  const [showExited, setShowExited] = useShowExited();
  const scroller = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (scroller.current) scroller.current.scrollTop = scrollTop;
  }, []);

  const entries = useMemo(
    () => rankSessionsForSwitcher(feed.visible, feed.askSessionIds, ""),
    [feed.visible, feed.askSessionIds],
  );
  const grouped = useMemo(() => {
    const groups: Record<SwitcherSection, SessionRecord[]> = { needs: [], pinned: [], live: [], exited: [] };
    for (const entry of entries) groups[entry.section].push(entry.session);
    return groups;
  }, [entries]);
  const { pinned, live, exited: exitedLive } = grouped;

  // The previews follow the rows that show them: pinned and live, not the exited.
  const previewed = useMemo(() => [...pinned, ...live], [pinned, live]);
  const activities = useSessionActivity(host, token, previewed, handleAuthFailure);
  const { count: exitedCount, listed, paging } = useExitedSessions({ query: "", live: exitedLive, enabled: showExited, remember: true });
  const asks = feed.pendingAsks?.items ?? [];

  const row = (session: SessionRecord, section: SwitcherSection) => {
    const lamp = sessionLamp(session, feed.askSessionIds.has(session.id));
    const word = lampWord(lamp, session.status);
    const ask = asks.find((item) => item.session_id === session.id);
    const single = section === "exited";
    return (
      <li key={session.id}>
        <Link
          href={sessionHref(session.id)}
          onNavigate={onPick && (() => onPick(session.id !== currentId))}
          aria-current={session.id === currentId ? "page" : undefined}
          className={cx(styles.item, session.id === currentId && styles.on, single && styles.single)}
        >
          <StatusLamp state={lamp} size="sm" label={word} />
          <span className={styles.name}>{session.title}</span>
          <span className={styles.time}>{formatSessionTime(session.last_event_at, now)}</span>
          {single ? null : (
            <span className={styles.sub}>
              {section === "needs" && ask ? ask.title : <ActivityLine fallback={word} activity={activities.get(session.id)} bright={lamp === "done"} />}
            </span>
          )}
        </Link>
      </li>
    );
  };

  return (
    <aside className={styles.rail} aria-label="Sessions">
      <div className={cx(styles.top, onClose && styles.closable)}>
        {onClose ? <PanelButton panel="rail" size="sm" control={{ open: true, toggle: onClose }} /> : null}
        <h2 className={styles.heading}>Sessions</h2>
        <IconButton label="Search sessions" size="sm" onClick={switcher.open}>
          <Icon name="search" size={18} />
        </IconButton>
      </div>
      <div ref={scroller} className={styles.scroll} onScroll={(event) => (scrollTop = event.currentTarget.scrollTop)}>
        {SECTIONS.map((section) => {
          const sessions = grouped[section];
          if (sessions.length === 0) return null;
          return (
            <section key={section}>
              <h3 className={styles.sectionHead}>
                {SWITCHER_SECTION_LABELS[section]} <span className={styles.count}>{sessions.length}</span>
              </h3>
              <ul className={styles.list}>{sessions.map((session) => row(session, section))}</ul>
            </section>
          );
        })}
        {exitedCount > 0 ? (
          <ExitedGroup
            count={exitedCount}
            short
            open={showExited}
            onToggle={() => setShowExited(!showExited)}
            paging={paging}
          >
            <ul className={styles.list}>{listed.items.map((session) => row(session, "exited"))}</ul>
          </ExitedGroup>
        ) : null}
      </div>
    </aside>
  );
}
