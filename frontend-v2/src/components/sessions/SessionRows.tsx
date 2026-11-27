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
import { SessionTime } from "./SessionTime";
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

// A result or exited row's time, marking a pin no heading names.
function RowTime({ session, now, unread }: { session: SessionRecord; now: number; unread?: boolean }) {
  return <SessionTime time={time(session, now)} pinned={Boolean(session.pinned_at)} unread={unread} />;
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

// A two-line card: title and time, then the lamp, the state and the agent. The
// preview, when on, adds a line.
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
          <span className={styles.time}>
            <SessionTime time={time(session, now)} unread={unread} />
          </span>
        </span>
        <span className={styles.state}>
          <StatusLamp state={lamp} size="sm" label={spokenStatus(word, unread)} />
          <span>{word}</span>
          <span className={styles.sep} aria-hidden="true">
            ·
          </span>
          <span className={styles.stateAgent}>{agent}</span>
        </span>
        {previews ? (
          // The line above says the state, so an empty preview stays empty.
          <span className={styles.preview}>
            <ActivityLine fallback="" activity={activity} bright={brightPreview(session, lamp)} progressStyle="long" />
          </span>
        ) : null}
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
      trailing={<RowTime session={session} now={now} unread={unread} />}
      actions={menu}
    />
  );
}
