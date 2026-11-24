import Link from "next/link";
import type { ReactNode } from "react";

import type { SessionActivity } from "@waypoint/web-core/activity";
import { formatSessionTime, lampWord, type SessionLamp } from "@waypoint/web-core/sessionPresentation";
import type { SessionRecord } from "@waypoint/web-core/types";

import { ListRow } from "@/components/ui/List";
import { StatusLamp } from "@/components/ui/StatusLamp";

import { ActivityLine } from "./ActivityLine";
import styles from "./SessionRows.module.css";

interface RowProps {
  session: SessionRecord;
  lamp: SessionLamp;
  now: number;
  // The agent and model; desktop rows and tiles show it, mobile rows keep it for the menu.
  agent: string;
  menu: ReactNode;
}

const href = (session: SessionRecord) => `/session/${encodeURIComponent(session.id)}`;
const time = (session: SessionRecord, now: number) => formatSessionTime(session.last_event_at, now);

// A running or idle session: its name, then one line of what it is doing.
export function LiveRow({
  session,
  lamp,
  now,
  menu,
  activity,
}: RowProps & { activity?: SessionActivity }) {
  return (
    <ListRow
      href={href(session)}
      lamp={lamp}
      lampLabel={lampWord(lamp, session.status)}
      title={session.title}
      status={
        <ActivityLine
          fallback={lampWord(lamp, session.status)}
          activity={activity}
          bright={lamp === "done"}
        />
      }
      trailing={time(session, now)}
      actions={menu}
    />
  );
}

// One line: a pinned session, or any session in a result list.
export function CompactRow({
  session,
  lamp,
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
      lampLabel={lampWord(lamp, session.status)}
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

// A live session on the desktop home: a quiet raised tile, no border.
export function LiveTile({
  session,
  lamp,
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
          <StatusLamp state={lamp} size="sm" label={lampWord(lamp, session.status)} />
          <ActivityLine
            fallback={lampWord(lamp, session.status)}
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

// A search result: the name, then where the session runs.
export function ResultRow({ session, lamp, now, agent, menu }: RowProps) {
  const where = [agent, session.repo_name, session.branch].filter(Boolean).join(", ");
  return (
    <ListRow
      href={href(session)}
      lamp={lamp}
      lampLabel={lampWord(lamp, session.status)}
      title={session.title}
      dimmed={lamp === "exited"}
      status={where}
      trailing={time(session, now)}
      actions={menu}
    />
  );
}
