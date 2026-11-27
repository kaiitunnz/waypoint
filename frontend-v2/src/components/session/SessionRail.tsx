"use client";

import Link from "next/link";
import { useLayoutEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";

import type { BackendCatalog } from "@waypoint/web-core/backends";
import { useNow } from "@waypoint/web-core/hooks/useNow";
import { useConversationPreviews } from "@waypoint/web-core/hooks/useConversationPreviews";
import { useLongPress } from "@waypoint/web-core/hooks/useLongPress";
import { useSessionActivity } from "@waypoint/web-core/hooks/useSessionActivity";
import {
  agentModelLabel,
  brightPreview,
  formatSessionTime,
  lampWord,
  sessionLamp,
  showsUnread,
  spokenStatus,
  type SessionLamp,
} from "@waypoint/web-core/sessionPresentation";
import { sessionHref } from "@waypoint/web-core/shellNav";
import { rankSessionsForSwitcher, SWITCHER_SECTION_LABELS, type SwitcherSection } from "@waypoint/web-core/switcher";
import type { SessionRecord } from "@waypoint/web-core/types";

import { ActivityLine } from "@/components/sessions/ActivityLine";
import { ExitedGroup } from "@/components/sessions/ExitedGroup";
import { PinMark } from "@/components/sessions/PinMark";
import { SessionMenu, type SessionAction } from "@/components/sessions/SessionMenu";
import { useSwitcher } from "@/components/sessions/SwitcherProvider";
import { useExitedSessions } from "@/components/sessions/useExitedSessions";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { StatusLamp } from "@/components/ui/StatusLamp";
import { handleAuthFailure } from "@/lib/auth";
import { usePointerUI } from "@/lib/layout";
import { useShowExited } from "@/lib/show-exited";
import { cx } from "@/lib/cx";
import { useSessionsData, useShellData } from "@/lib/shell-data";

import { PanelButton } from "./PanelButton";
import styles from "./SessionRail.module.css";

const SECTIONS: SwitcherSection[] = ["needs", "pinned", "live"];

// The rail mounts again on every session switch; this is where it was scrolled to.
let scrollTop = 0;

interface RailRowProps {
  session: SessionRecord;
  current: boolean;
  now: number;
  lamp: SessionLamp;
  // The second line, or null for a one-line row.
  sub: ReactNode;
  // Under a heading that does not say it is pinned.
  pinMark: boolean;
  menuOpen: boolean;
  onMenuOpen: (open: boolean) => void;
  menuSubtitle: string;
  onAction: (action: SessionAction, session: SessionRecord) => void;
  onNavigate?: () => void;
}

function RailRow({
  session,
  current,
  now,
  lamp,
  sub,
  pinMark,
  menuOpen,
  onMenuOpen,
  menuSubtitle,
  onAction,
  onNavigate,
}: RailRowProps) {
  const pointer = usePointerUI();
  const press = useLongPress(() => onMenuOpen(true));
  const unread = showsUnread(session);
  const single = sub === null;
  const onContextMenu = (event: MouseEvent) => {
    press.onContextMenu(event);
    // Android also fires this for a long press, which opens the menu itself.
    if (!pointer) return;
    event.preventDefault();
    onMenuOpen(true);
  };
  return (
    <li className={cx(styles.row, single && styles.single)}>
      <Link
        {...press}
        onContextMenu={onContextMenu}
        draggable={pointer ? undefined : false}
        href={sessionHref(session.id)}
        onNavigate={onNavigate}
        aria-current={current ? "page" : undefined}
        className={cx(
          styles.item,
          unread && styles.unread,
          current && styles.on,
          lamp === "exited" && styles.exited,
        )}
      >
        <StatusLamp state={lamp} size="sm" label={spokenStatus(lampWord(lamp, session.status), unread)} />
        <span className={styles.name}>{session.title}</span>
        <span className={styles.time}>
          {pinMark ? <PinMark /> : null}
          {formatSessionTime(session.last_event_at, now)}
        </span>
        {single ? null : <span className={styles.sub}>{sub}</span>}
      </Link>
      <span className={styles.actions}>
        <SessionMenu
          session={session}
          subtitle={menuSubtitle}
          onAction={onAction}
          open={menuOpen}
          onOpenChange={onMenuOpen}
        />
      </span>
    </li>
  );
}

interface SessionRailProps {
  currentId: string;
  catalog: BackendCatalog;
  // A row's menu choice; the caller renders the dialogs, which outlive a drawer.
  onAction: (action: SessionAction, session: SessionRecord) => void;
  // For the rail drawn as a drawer: closing it, and a session picked in it.
  onClose?: () => void;
  onPick?: (anotherSession: boolean) => void;
}

export function SessionRail({ currentId, catalog, onAction, onClose, onPick }: SessionRailProps) {
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
  const { pinned, live, exited } = grouped;
  // Pinned exited sessions head the Exited group; the rest merge with the history.
  const pinnedExited = useMemo(() => exited.filter((session) => session.pinned_at), [exited]);
  const unpinnedExited = useMemo(() => exited.filter((session) => !session.pinned_at), [exited]);

  // The previews follow the rows that show them: pinned and live, not the exited.
  const previewed = useMemo(() => [...pinned, ...live], [pinned, live]);
  const [previews] = useConversationPreviews();
  const activities = useSessionActivity(host, token, previewed, handleAuthFailure, previews);
  const { count: exitedCount, listed, paging } = useExitedSessions({
    query: "",
    live: unpinnedExited,
    lead: pinnedExited,
    enabled: showExited,
    remember: true,
  });
  const asks = feed.pendingAsks?.items ?? [];

  // The session whose menu is open, held here so a row that changes section keeps it.
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const drawn = [...SECTIONS.flatMap((section) => grouped[section]), ...(showExited ? listed.items : [])];
  if (menuFor !== null && !drawn.some((session) => session.id === menuFor)) setMenuFor(null);

  const row = (session: SessionRecord, section: SwitcherSection) => {
    const lamp = sessionLamp(session, feed.askSessionIds.has(session.id));
    const ask = asks.find((item) => item.session_id === session.id);
    // Needs-you rows keep their ask line.
    const single = section === "exited" || (!previews && section !== "needs");
    const sub = single ? null : section === "needs" && ask ? (
      ask.title
    ) : (
      <ActivityLine
        fallback={lampWord(lamp, session.status)}
        activity={activities.get(session.id)}
        bright={brightPreview(session, lamp)}
      />
    );
    return (
      <RailRow
        key={session.id}
        session={session}
        current={session.id === currentId}
        now={now}
        lamp={lamp}
        sub={sub}
        pinMark={section !== "pinned" && Boolean(session.pinned_at)}
        menuOpen={menuFor === session.id}
        onMenuOpen={(open) => setMenuFor(open ? session.id : null)}
        menuSubtitle={agentModelLabel(session, catalog)}
        onAction={onAction}
        onNavigate={onPick && (() => onPick(session.id !== currentId))}
      />
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
