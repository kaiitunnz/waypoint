import type { ReactNode } from "react";

import type { SessionActivity } from "@waypoint/web-core/activity";
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
import type { PendingAsk, SessionRecord } from "@waypoint/web-core/types";

import { StatusLamp } from "@/components/ui/StatusLamp";
import { handleAuthFailure } from "@/lib/auth";
import { cx } from "@/lib/cx";
import { useSessionsData, useShellData } from "@/lib/shell-data";

import { ActivityLine } from "./ActivityLine";
import styles from "./RailItem.module.css";
import { SessionTime } from "./SessionTime";

// The row of the session rail and the switcher: each draws its own element around it
// (a link, an option) and gives that element its place and fill.
export function railItemClass(session: SessionRecord, lamp: SessionLamp, sub: ReactNode): string {
  return cx(
    styles.item,
    sub === null && styles.single,
    showsUnread(session) && styles.unread,
    lamp === "exited" && styles.exited,
  );
}

interface SubLine {
  session: SessionRecord;
  lamp: SessionLamp;
  section: SwitcherSection;
  ask: PendingAsk | undefined;
  previews: boolean;
  activity: SessionActivity | null | undefined;
}

// A row's second line, or null for a one-line row. A Needs-you row keeps its ask line.
function subLine({ session, lamp, section, ask, previews, activity }: SubLine): ReactNode {
  if (section === "exited" || (!previews && section !== "needs")) return null;
  if (section === "needs" && ask) return ask.title;
  return (
    <ActivityLine fallback={lampWord(lamp, session.status)} activity={activity} bright={brightPreview(session, lamp)} />
  );
}

// Each row's lamp and second line. The previews load for `previewed`, the rows that
// can show one.
export function useRailRows(previewed: SessionRecord[]) {
  const { host, token } = useShellData();
  const { askSessionIds, pendingAsks } = useSessionsData();
  const [previews] = useConversationPreviews();
  const activities = useSessionActivity(host, token, previewed, handleAuthFailure, previews);
  return (session: SessionRecord, section: SwitcherSection) => {
    const lamp = sessionLamp(session, askSessionIds.has(session.id));
    const ask = pendingAsks?.items.find((item) => item.session_id === session.id);
    return { lamp, sub: subLine({ session, lamp, section, ask, previews, activity: activities.get(session.id) }) };
  };
}

export function MatchedTitle({ title, positions }: { title: string; positions: number[] }) {
  return highlightRuns(title, positions).map((run, index) =>
    run.hit ? (
      <b key={index} className={styles.hit}>
        {run.value}
      </b>
    ) : (
      run.value
    ),
  );
}

interface RailItemProps {
  session: SessionRecord;
  lamp: SessionLamp;
  section: SwitcherSection;
  now: number;
  // The title, when the caller marks it up.
  title?: ReactNode;
  sub: ReactNode;
  timeClassName?: string;
}

export function RailItem({ session, lamp, section, now, title = session.title, sub, timeClassName }: RailItemProps) {
  const unread = showsUnread(session);
  return (
    <>
      <StatusLamp state={lamp} size="sm" label={spokenStatus(lampWord(lamp, session.status), unread)} />
      <span className={styles.name}>{title}</span>
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
  );
}
