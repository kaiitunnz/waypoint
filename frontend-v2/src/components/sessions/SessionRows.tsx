import Link from "next/link";
import type { ReactNode } from "react";

import type { SessionActivity } from "@waypoint/web-core/activity";
import {
  brightPreview,
  formatSessionTime,
  showsUnread,
  spokenStatus,
  type SessionLamp,
} from "@waypoint/web-core/sessionPresentation";
import { sessionHref } from "@waypoint/web-core/shellNav";
import type { SessionRecord } from "@waypoint/web-core/types";

import { ListRow } from "@/components/ui/List";
import { StatusLamp } from "@/components/ui/StatusLamp";

import { ActivityLine } from "./ActivityLine";
import { PinMark } from "./PinMark";
import styles from "./SessionRows.module.css";
import { cx } from "@/lib/cx";

interface RowProps {
  session: SessionRecord;
  lamp: SessionLamp;
  // The lamp's spoken name.
  word: string;
  now: number;
  agent: string;
  menu: ReactNode;
}

interface PreviewProps {
  previews: boolean;
  activity: SessionActivity | null | undefined;
}

const href = (session: SessionRecord) => sessionHref(session.id);
const time = (session: SessionRecord, now: number) => formatSessionTime(session.last_event_at, now);

function RowTime({ session, now }: { session: SessionRecord; now: number }) {
  if (!session.pinned_at) return time(session, now);
  return (
    <span className={styles.pinned}>
      <PinMark />
      {time(session, now)}
    </span>
  );
}

export function LiveRow({
  session,
  lamp,
  word,
  now,
  menu,
  previews,
  activity,
}: Omit<RowProps, "agent"> & PreviewProps) {
  const unread = showsUnread(session);
  return (
    <ListRow
      href={href(session)}
      lamp={lamp}
      lampLabel={spokenStatus(word, unread)}
      title={session.title}
      single={!previews}
      unread={unread}
      status={
        previews ? <ActivityLine fallback={word} activity={activity} bright={brightPreview(session, lamp)} /> : undefined
      }
      trailing={time(session, now)}
      actions={menu}
    />
  );
}

export function CompactRow({
  session,
  lamp,
  word,
  now,
  agent,
  menu,
  detailed,
}: RowProps & { detailed: boolean }) {
  const when = <RowTime session={session} now={now} />;
  return (
    <ListRow
      href={href(session)}
      lamp={lamp}
      lampLabel={word}
      title={session.title}
      compact
      dimmed
      trailing={
        detailed ? (
          <span className={styles.trail}>
            <span className={styles.agent}>{agent}</span>
            <span className={styles.when}>{when}</span>
          </span>
        ) : (
          when
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
  previews,
  activity,
}: RowProps & PreviewProps) {
  const unread = showsUnread(session);
  return (
    <li className={styles.tileItem}>
      <Link href={href(session)} className={cx(styles.tile, unread && styles.unread)}>
        <span className={styles.head}>
          <span className={styles.name}>{session.title}</span>
          <span className={styles.time}>{time(session, now)}</span>
        </span>
        <span className={styles.step}>
          <StatusLamp state={lamp} size="sm" label={spokenStatus(word, unread)} />
          {previews ? (
            <ActivityLine fallback={word} activity={activity} bright={brightPreview(session, lamp)} progressStyle="long" />
          ) : (
            word
          )}
        </span>
        <span className={styles.agentLine}>{agent}</span>
      </Link>
      <span className={styles.menu}>{menu}</span>
    </li>
  );
}

export function ResultRow({ session, lamp, word, now, agent, menu }: RowProps) {
  const where = [agent, session.repo_name, session.branch].filter(Boolean).join(", ");
  const unread = showsUnread(session);
  return (
    <ListRow
      href={href(session)}
      lamp={lamp}
      lampLabel={spokenStatus(word, unread)}
      title={session.title}
      unread={unread}
      dimmed={lamp === "exited"}
      status={where}
      trailing={<RowTime session={session} now={now} />}
      actions={menu}
    />
  );
}
