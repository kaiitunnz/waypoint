"use client";

import Link from "next/link";
import { useLayoutEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";

import type { BackendCatalog } from "@waypoint/web-core/backends";
import { useServerNow } from "@waypoint/web-core/hooks/useServerNow";
import { useLongPress } from "@waypoint/web-core/hooks/useLongPress";
import { agentModelLabel, type SessionLamp } from "@waypoint/web-core/sessionPresentation";
import { sessionHref } from "@waypoint/web-core/shellNav";
import { rankSessionsForSwitcher, SWITCHER_SECTION_LABELS, type SwitcherSection } from "@waypoint/web-core/switcher";
import type { SessionRecord } from "@waypoint/web-core/types";

import { ExitedGroup } from "@/components/sessions/ExitedGroup";
import { RailHeading, RailItem, useRailRows } from "@/components/sessions/RailItem";
import { SessionMenu, type SessionAction } from "@/components/sessions/SessionMenu";
import { useSwitcher } from "@/components/sessions/SwitcherProvider";
import { useExitedSessions } from "@/components/sessions/useExitedSessions";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { usePointerUI } from "@/lib/layout";
import { useShowExited } from "@/lib/show-exited";
import { cx } from "@/lib/cx";
import { useSessionsData } from "@/lib/shell-data";

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
  section: SwitcherSection;
  // The second line, or null for a one-line row.
  sub: ReactNode;
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
  section,
  sub,
  menuOpen,
  onMenuOpen,
  menuSubtitle,
  onAction,
  onNavigate,
}: RailRowProps) {
  const pointer = usePointerUI();
  const press = useLongPress(() => onMenuOpen(true));
  const onContextMenu = (event: MouseEvent) => {
    press.onContextMenu(event);
    // Android also fires this for a long press, which opens the menu itself.
    if (!pointer) return;
    event.preventDefault();
    onMenuOpen(true);
  };
  return (
    <li className={cx(styles.row, sub === null && styles.single)}>
      <RailItem
        session={session}
        lamp={lamp}
        section={section}
        now={now}
        sub={sub}
        className={cx(styles.item, current && styles.on)}
        timeClassName={styles.time}
        render={(row) => (
          <Link
            {...press}
            {...row}
            onContextMenu={onContextMenu}
            draggable={pointer ? undefined : false}
            href={sessionHref(session.id)}
            onNavigate={onNavigate}
            aria-current={current ? "page" : undefined}
          />
        )}
      />
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
  const feed = useSessionsData();
  const switcher = useSwitcher();
  const now = useServerNow();
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
  const railRow = useRailRows(previewed);
  const { count: exitedCount, listed, paging } = useExitedSessions({
    query: "",
    live: unpinnedExited,
    lead: pinnedExited,
    enabled: showExited,
    remember: true,
  });

  // The session whose menu is open, held here so a row that changes section keeps it.
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const drawn = [...SECTIONS.flatMap((section) => grouped[section]), ...(showExited ? listed.items : [])];
  if (menuFor !== null && !drawn.some((session) => session.id === menuFor)) setMenuFor(null);

  const row = (session: SessionRecord, section: SwitcherSection) => {
    const { lamp, sub } = railRow(session, section);
    return (
      <RailRow
        key={session.id}
        session={session}
        current={session.id === currentId}
        now={now}
        lamp={lamp}
        section={section}
        sub={sub}
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
              <RailHeading
                as="h3"
                label={SWITCHER_SECTION_LABELS[section]}
                count={sessions.length}
                className={styles.sectionHead}
              />
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
