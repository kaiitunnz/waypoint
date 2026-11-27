"use client";

import type { ReactElement, ReactNode } from "react";

import { highlightRuns } from "@waypoint/web-core/fuzzy";
import { useConversationPreviews } from "@waypoint/web-core/hooks/useConversationPreviews";
import { useSessionActivity } from "@waypoint/web-core/hooks/useSessionActivity";
import {
  brightPreview,
  formatSessionTime,
  lampWord,
  sessionLamp,
  showsUnread,
  spokenStatus,
  type SessionLamp,
} from "@waypoint/web-core/sessionPresentation";
import type { SwitcherSection } from "@waypoint/web-core/switcher";
import type { SessionRecord } from "@waypoint/web-core/types";

import { StatusLamp } from "@/components/ui/StatusLamp";
import { handleAuthFailure } from "@/lib/auth";
import { cx } from "@/lib/cx";
import { useSessionsData, useShellData } from "@/lib/shell-data";

import { ActivityLine } from "./ActivityLine";
import styles from "./RailItem.module.css";
import { SessionTime } from "./SessionTime";

// Each row's lamp and second line, which is null on a one-line row. The previews load
// for `previewed`.
export function useRailRows(previewed: SessionRecord[]) {
  const { host, token } = useShellData();
  const { askSessionIds, pendingAsks } = useSessionsData();
  const [previews] = useConversationPreviews();
  const activities = useSessionActivity(host, token, previewed, handleAuthFailure, previews);
  return (session: SessionRecord, section: SwitcherSection): { lamp: SessionLamp; sub: ReactNode } => {
    const lamp = sessionLamp(session, askSessionIds.has(session.id));
    if (section === "exited" || (!previews && section !== "needs")) return { lamp, sub: null };
    // A Needs-you row keeps its ask line.
    const ask = section === "needs" ? pendingAsks?.items.find((item) => item.session_id === session.id) : undefined;
    const sub = ask ? (
      ask.title
    ) : (
      <ActivityLine
        fallback={lampWord(lamp, session.status)}
        activity={activities.get(session.id)}
        bright={brightPreview(session, lamp)}
      />
    );
    return { lamp, sub };
  };
}

interface RailHeadingProps {
  as: "h3" | "div";
  label: string;
  count: number;
  // The list's padding for the heading.
  className: string;
}

export function RailHeading({ as: Heading, label, count, className }: RailHeadingProps) {
  return (
    <Heading className={cx(styles.sectionHead, className)}>
      {label} <span className={styles.count}>{count}</span>
    </Heading>
  );
}

interface RailItemProps {
  session: SessionRecord;
  lamp: SessionLamp;
  section: SwitcherSection;
  now: number;
  // Characters of the title that match a search.
  positions?: number[];
  sub: ReactNode;
  // The row's place and fill in its list.
  className: string;
  timeClassName?: string;
  // Draws the row's element, a link or an option, with these.
  render: (row: { className: string; children: ReactNode }) => ReactElement;
}

// A row of the session rail and the switcher.
export function RailItem({
  session,
  lamp,
  section,
  now,
  positions = [],
  sub,
  className,
  timeClassName,
  render,
}: RailItemProps) {
  const unread = showsUnread(session);
  return render({
    className: cx(
      styles.item,
      sub === null && styles.single,
      unread && styles.unread,
      lamp === "exited" && styles.exited,
      className,
    ),
    children: (
      <>
        <StatusLamp state={lamp} size="sm" label={spokenStatus(lampWord(lamp, session.status), unread)} />
        <span className={styles.name}>
          {highlightRuns(session.title, positions).map((run, index) =>
            run.hit ? (
              <b key={index} className={styles.hit}>
                {run.value}
              </b>
            ) : (
              run.value
            ),
          )}
        </span>
        <span className={cx(styles.time, timeClassName)}>
          <SessionTime
            time={formatSessionTime(session.last_event_at, now)}
            // Under a heading that does not say it is pinned.
            pinned={section !== "pinned" && Boolean(session.pinned_at)}
            unread={unread}
          />
        </span>
        {sub === null ? null : <span className={styles.sub}>{sub}</span>}
      </>
    ),
  });
}
