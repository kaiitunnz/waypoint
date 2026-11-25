import Link from "next/link";
import type { ReactNode } from "react";

import type { SessionActivity } from "@waypoint/web-core/activity";
import { formatSessionTime, type SessionLamp } from "@waypoint/web-core/sessionPresentation";
import { sessionHref } from "@waypoint/web-core/shellNav";
import type { SessionRecord } from "@waypoint/web-core/types";

import { ListRow } from "@/components/ui/List";
import { StatusLamp } from "@/components/ui/StatusLamp";

import { ActivityLine } from "./ActivityLine";
import styles from "./SessionRows.module.css";

interface RowProps {
  session: SessionRecord;
  lamp: SessionLamp;
  // The lamp's spoken name.
  word: string;
  now: number;
  agent: string;
  menu: ReactNode;
}

const href = (session: SessionRecord) => sessionHref(session.id);
const time = (session: SessionRecord, now: number) => formatSessionTime(session.last_event_at, now);

export function LiveRow({
  session,
  lamp,
  word,
  now,
  menu,
  activity,
}: Omit<RowProps, "agent"> & { activity?: SessionActivity }) {
  return (
    <ListRow
      href={href(session)}
      lamp={lamp}
      lampLabel={word}
      title={session.title}
      status={
        <ActivityLine
          fallback={word}
          activity={activity}
          bright={lamp === "done"}
        />
      }
      trailing={time(session, now)}
      actions={menu}
    />
  );
}

// One line: a pinned or exited session.
export function CompactRow({
  session,
  lamp,
  word,
  now,
  agent,
  menu,
  desktop,
  pinned,
}: RowProps & { desktop: boolean; pinned?: boolean }) {
  return (
    <ListRow
      href={href(session)}
      lamp={lamp}
      lampLabel={word}
      title={session.title}
      compact
      pinned={pinned}
      dimmed={lamp === "exited"}
      trailing={
        desktop ? (
          <span className={styles.trail}>
            <span className={styles.agent}>{agent}</span>
            <span className={styles.when}>{time(session, now)}</span>
          </span>
        ) : (
          time(session, now)
        )
      }
      actions={menu}
    />
  );
}

export function LiveTile({
  session,
  lamp,
  word,
  now,
  agent,
  menu,
  activity,
}: RowProps & { activity?: SessionActivity }) {
  return (
    <li className={styles.tileItem}>
      <Link href={href(session)} className={styles.tile}>
        <span className={styles.head}>
          <span className={styles.name}>{session.title}</span>
          <span className={styles.time}>{time(session, now)}</span>
        </span>
        <span className={styles.step}>
          <StatusLamp state={lamp} size="sm" label={word} />
          <ActivityLine
            fallback={word}
            activity={activity}
            bright={lamp === "done"}
            progressStyle="long"
          />
        </span>
        <span className={styles.agentLine}>{agent}</span>
      </Link>
      <span className={styles.menu}>{menu}</span>
    </li>
  );
}

export function ResultRow({ session, lamp, word, now, agent, menu }: RowProps) {
  const where = [agent, session.repo_name, session.branch].filter(Boolean).join(", ");
  return (
    <ListRow
      href={href(session)}
      lamp={lamp}
      lampLabel={word}
      title={session.title}
      dimmed={lamp === "exited"}
      status={where}
      trailing={time(session, now)}
      actions={menu}
    />
  );
}
