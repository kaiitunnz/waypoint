import type { ReactNode } from "react";

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

// The class of the element around a RailItem, which lays out its grid.
export function railItemClass(session: SessionRecord, lamp: SessionLamp, sub: ReactNode): string {
  return cx(
    styles.item,
    sub === null && styles.single,
    showsUnread(session) && styles.unread,
    lamp === "exited" && styles.exited,
  );
}

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

interface RailItemProps {
  session: SessionRecord;
  lamp: SessionLamp;
  section: SwitcherSection;
  now: number;
  // Characters of the title that match a search.
  positions?: number[];
  sub: ReactNode;
  timeClassName?: string;
}

export function RailItem({ session, lamp, section, now, positions = [], sub, timeClassName }: RailItemProps) {
  const unread = showsUnread(session);
  return (
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
  );
}
