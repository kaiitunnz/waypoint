"use client";

import Link from "next/link";
import { useMemo } from "react";

import { useNow } from "@waypoint/web-core/hooks/useNow";
import { useSessionActivity } from "@waypoint/web-core/hooks/useSessionActivity";
import { formatSessionTime, lampWord, sessionLamp } from "@waypoint/web-core/sessionPresentation";
import { HOME_HIDDEN_SOURCES } from "@waypoint/web-core/sessionSections";
import { useSessionHistory } from "@waypoint/web-core/sessionHistory";
import { sessionHref } from "@waypoint/web-core/shellNav";
import { rankSessionsForSwitcher, SWITCHER_SECTION_LABELS, type SwitcherSection } from "@waypoint/web-core/switcher";
import type { SessionRecord } from "@waypoint/web-core/types";

import { ActivityLine } from "@/components/sessions/ActivityLine";
import { useSwitcher } from "@/components/sessions/SwitcherProvider";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { StatusLamp } from "@/components/ui/StatusLamp";
import { handleAuthFailure } from "@/lib/auth";
import { cx } from "@/lib/cx";
import { useSessionsData, useShellData } from "@/lib/shell-data";

import styles from "./SessionRail.module.css";

const SECTIONS: SwitcherSection[] = ["needs", "pinned", "live"];

// The desktop rail: every live session by attention, with the open one marked.
export function SessionRail({ currentId }: { currentId: string }) {
  const { host, token } = useShellData();
  const feed = useSessionsData();
  const switcher = useSwitcher();
  const now = useNow();

  const entries = useMemo(
    () => rankSessionsForSwitcher(feed.visible, feed.askSessionIds, ""),
    [feed.visible, feed.askSessionIds],
  );
  const bySection = (section: SwitcherSection) =>
    entries.filter((entry) => entry.section === section).map((entry) => entry.session);
  const live = useMemo(() => entries.filter((entry) => entry.section === "live").map((entry) => entry.session), [entries]);
  const exitedLive = entries.filter((entry) => entry.section === "exited").length;

  const activities = useSessionActivity(host, token, live, handleAuthFailure);
  const history = useSessionHistory({
    host,
    token,
    query: "",
    withItems: false,
    revision: feed.historyRevision,
    excludeSources: HOME_HIDDEN_SOURCES,
    onAuthError: handleAuthFailure,
  });
  const exited = exitedLive + (history.total ?? 0);
  const asks = feed.pendingAsks?.items ?? [];

  const row = (session: SessionRecord, section: SwitcherSection) => {
    const lamp = sessionLamp(session, feed.askSessionIds.has(session.id));
    const word = lampWord(lamp, session.status);
    const ask = asks.find((item) => item.session_id === session.id);
    const compact = section === "pinned";
    return (
      <li key={session.id}>
        <Link
          href={sessionHref(session.id)}
          aria-current={session.id === currentId ? "page" : undefined}
          className={cx(styles.item, session.id === currentId && styles.on, compact && styles.compact, section === "pinned" && styles.pinned)}
        >
          <StatusLamp state={lamp} size="sm" label={word} />
          <span className={styles.name}>{session.title}</span>
          <span className={styles.time}>{formatSessionTime(session.last_event_at, now)}</span>
          {compact ? null : (
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
      <div className={styles.top}>
        <h2 className={styles.heading}>Sessions</h2>
        <IconButton label="Search sessions" size="sm" onClick={switcher.open}>
          <Icon name="search" size={18} />
        </IconButton>
      </div>
      <div className={styles.scroll}>
        {SECTIONS.map((section) => {
          const sessions = bySection(section);
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
        {exited > 0 ? (
          <Link href="/" className={styles.exited}>
            <StatusLamp state="exited" size="sm" />
            <span className={styles.exitedLabel}>{exited} exited</span>
            <Icon name="chevron-right" size={14} />
          </Link>
        ) : null}
      </div>
    </aside>
  );
}
