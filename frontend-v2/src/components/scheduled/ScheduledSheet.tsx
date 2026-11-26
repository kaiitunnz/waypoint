"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import {
  cancelSchedule,
  clearMessageScheduleHistory,
  clearScheduleHistory,
  deleteMessageSchedule,
  sendMessageScheduleNow,
} from "@waypoint/web-core/api";
import type { BackendCatalog } from "@waypoint/web-core/backends";
import { useScheduleModels } from "@waypoint/web-core/hooks/useScheduleModels";
import { browserTimezone } from "@waypoint/web-core/recurrence";
import {
  launchRowView,
  messageRowView,
  sortScheduled,
  type ScheduledAction,
  type ScheduledLaunchView,
  type ScheduledMessageView,
} from "@waypoint/web-core/scheduledSheet";
import { sendNowOutcome } from "@waypoint/web-core/session/messageQueue";
import { shortSessionId } from "@waypoint/web-core/session/scheduledMessages";
import { sessionHref } from "@waypoint/web-core/shellNav";
import type { MessageSchedule, ScheduledSession } from "@waypoint/web-core/types";
import { splitPath } from "@waypoint/web-core/workspacePaths";

import { Clamped } from "@/components/session/cards/Clamped";
import { Disclosure } from "@/components/session/cards/Disclosure";
import { PathLabel } from "@/components/session/cards/PathLabel";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Dialog } from "@/components/ui/Dialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { useToast } from "@/components/ui/Toast";
import { cx } from "@/lib/cx";
import { useShellData } from "@/lib/shell-data";
import { useFailureToast } from "@/lib/useFailureToast";

import styles from "./ScheduledSheet.module.css";

// Finished rows shown while the history is folded.
const HISTORY_SHOWN = 5;

const GROUPS = {
  launch: { title: "Sessions", one: "session launch", many: "session launches", clear: clearScheduleHistory },
  message: { title: "Messages", one: "message", many: "messages", clear: clearMessageScheduleHistory },
};

const ACTION_LABEL: Record<ScheduledAction, string> = {
  open: "Open session",
  openLast: "Open last run",
  sendNow: "Send now",
  cancel: "Cancel",
  dismiss: "Dismiss",
};

type RowView = ScheduledLaunchView | ScheduledMessageView;

interface ScheduledSheetProps {
  onClose: () => void;
  schedules: ScheduledSession[];
  messages: MessageSchedule[];
  titleById: ReadonlyMap<string, string>;
  catalog: BackendCatalog;
  // The home's clock, so the sheet and the summary behind it count down alike.
  now: number;
}

// Rows update from the schedule broadcast, so the actions only call the server.
export function ScheduledSheet({ onClose, schedules, messages, titleById, catalog, now: clock }: ScheduledSheetProps) {
  const { host, token } = useShellData();
  const modelLabel = useScheduleModels(host, token, schedules);
  const now = new Date(clock);
  const zone = browserTimezone();
  const launches = sortScheduled(schedules);
  const sends = sortScheduled(messages);
  const pending = [...launches, ...sends].filter((row) => row.status === "pending").length;

  return (
    <Dialog open onClose={onClose} title="Scheduled" subtitle={`${pending} pending`} size="lg">
      {launches.length === 0 && sends.length === 0 ? (
        <EmptyState title="Nothing scheduled" />
      ) : (
        <div className={styles.groups}>
          <Group
            kind="launch"
            rows={launches.map((schedule) => launchRowView(schedule, catalog, now, zone, modelLabel(schedule)))}
            onClose={onClose}
          />
          <Group
            kind="message"
            rows={sends.map((message) => messageRowView(message, titleById.get(message.session_id) ?? null, now, zone))}
            onClose={onClose}
          />
        </div>
      )}
    </Dialog>
  );
}

interface GroupProps {
  kind: keyof typeof GROUPS;
  rows: RowView[];
  onClose: () => void;
}

function Group({ kind, rows, onClose }: GroupProps) {
  const { host, token } = useShellData();
  const { toast } = useToast();
  const fail = useFailureToast();
  const [expanded, setExpanded] = useState(false);
  const [confirming, setConfirming] = useState(false);
  if (rows.length === 0) return null;
  const pending = rows.filter((row) => !row.resolved);
  const history = rows.filter((row) => row.resolved);
  const shown = expanded ? rows : [...pending, ...history.slice(0, HISTORY_SHOWN)];
  const { title, one, many, clear: clearHistory } = GROUPS[kind];

  async function clear() {
    try {
      const removed = await clearHistory(host, token);
      toast({ message: `Cleared ${removed} finished ${removed === 1 ? "entry" : "entries"}` });
    } catch (error) {
      fail(error, "Could not clear the history");
    }
  }

  return (
    <section aria-label={title}>
      <div className={styles.head}>
        <h3 className={styles.title}>{title}</h3>
        <span className={styles.count}>{pending.length} pending</span>
        {history.length > 0 ? (
          <Button variant="text" size="sm" className={styles.clear} onClick={() => setConfirming(true)}>
            Clear history
          </Button>
        ) : null}
      </div>
      <ul className={styles.rows}>
        {shown.map((row) => (
          <Row key={row.id} row={row} onClose={onClose} />
        ))}
      </ul>
      {history.length > HISTORY_SHOWN ? (
        <Disclosure open={expanded} label={`${history.length - HISTORY_SHOWN} more`} onToggle={() => setExpanded(!expanded)} />
      ) : null}
      <ConfirmDialog
        open={confirming}
        title="Clear history"
        detail={`Remove ${history.length} finished ${history.length === 1 ? one : many}? Pending ones stay.`}
        confirmLabel="Clear"
        onConfirm={clear}
        onClose={() => setConfirming(false)}
      />
    </section>
  );
}

function Row({ row, onClose }: { row: RowView; onClose: () => void }) {
  const { host, token } = useShellData();
  const router = useRouter();
  const { toast } = useToast();
  const fail = useFailureToast();
  const [sending, setSending] = useState(false);
  // `resolved` as it stood when a cancel or dismiss went through. The list changes with
  // the broadcast that follows, and a second cancel before then would dismiss the record.
  const [removedWhen, setRemovedWhen] = useState<boolean | null>(null);
  const busy = sending || removedWhen === row.resolved;
  const launch = row.kind === "launch" ? row : null;
  const message = row.kind === "message" ? row : null;

  async function act(action: ScheduledAction) {
    if (action === "open" || action === "openLast") {
      const sessionId = row.sessionId;
      if (!sessionId) return;
      onClose();
      router.push(sessionHref(sessionId));
      return;
    }
    setSending(true);
    try {
      if (action === "sendNow") {
        const { held } = await sendMessageScheduleNow(host, token, row.id);
        toast({ message: sendNowOutcome(held) });
      } else {
        if (row.kind === "launch") await cancelSchedule(host, token, row.id);
        else await deleteMessageSchedule(host, token, row.id);
        setRemovedWhen(row.resolved);
      }
    } catch (error) {
      fail(error, "Could not update the schedule");
    } finally {
      setSending(false);
    }
  }

  return (
    <li className={cx(styles.row, row.resolved && styles.resolved)}>
      <span className={styles.glyph} aria-hidden="true">
        {row.recurring ? "↻" : "◷"}
      </span>
      <div className={styles.body}>
        <div className={styles.top}>
          {launch ? (
            <p className={styles.name}>{row.title}</p>
          ) : (
            <div className={styles.text}>
              <Clamped lines={3}>{row.title}</Clamped>
            </div>
          )}
          {row.countdown ? <span className={styles.countdown}>{row.countdown}</span> : null}
        </div>
        {message ? (
          <p className={styles.target}>
            {message.target ? (
              <>
                to <span className={styles.targetName}>{message.target}</span>
              </>
            ) : (
              <>
                to session <code className={styles.targetId}>{shortSessionId(message.sessionId)}</code>
              </>
            )}
          </p>
        ) : null}
        <p className={cx(styles.state, row.failed && styles.failed)}>{row.state}</p>
        {launch ? (
          <div className={styles.meta}>
            <span>{launch.agent}</span>
            <PathLabel {...splitPath(launch.folder)} full={launch.folder} />
          </div>
        ) : null}
        {row.lastRun ? <p className={cx(styles.meta, row.lastRun.failed && styles.failed)}>{row.lastRun.text}</p> : null}
        <div className={styles.actions}>
          {row.actions.map((action) => (
            <Button
              key={action}
              variant="text"
              size="sm"
              className={action === "cancel" || action === "dismiss" ? undefined : styles.strong}
              disabled={busy}
              onClick={() => void act(action)}
            >
              {ACTION_LABEL[action]}
            </Button>
          ))}
        </div>
      </div>
    </li>
  );
}
