import type { ReactNode } from "react";

import type { SessionActivity } from "@waypoint/web-core/activity";
import { highlightRuns } from "@waypoint/web-core/fuzzy";
import {
  brightPreview,
  formatSessionTime,
  lampWord,
  showsUnread,
  spokenStatus,
  type SessionLamp,
} from "@waypoint/web-core/sessionPresentation";
import type { SwitcherSection } from "@waypoint/web-core/switcher";
import type { PendingAsk, SessionRecord } from "@waypoint/web-core/types";

import { StatusLamp } from "@/components/ui/StatusLamp";
import { cx } from "@/lib/cx";

import { ActivityLine } from "./ActivityLine";
import styles from "./RailItem.module.css";
import { SessionTime } from "./SessionTime";

// The row of the session rail and the switcher: each draws its own element around it
// (a link, an option) and gives that element its place and fill.
export function railItemClass(session: SessionRecord, lamp: SessionLamp, single: boolean): string {
  return cx(
    styles.item,
    single && styles.single,
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
export function railSubLine({ session, lamp, section, ask, previews, activity }: SubLine): ReactNode {
  if (section === "exited" || (!previews && section !== "needs")) return null;
  if (section === "needs" && ask) return ask.title;
  return (
    <ActivityLine fallback={lampWord(lamp, session.status)} activity={activity} bright={brightPreview(session, lamp)} />
  );
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
