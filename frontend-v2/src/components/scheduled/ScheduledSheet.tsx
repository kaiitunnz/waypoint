"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import {
  cancelSchedule,
  clearMessageScheduleHistory,
  clearScheduleHistory,
  deleteMessageSchedule,
  isAuthError,
  sendMessageScheduleNow,
} from "@waypoint/web-core/api";
import type { BackendCatalog } from "@waypoint/web-core/backends";
import { useNow } from "@waypoint/web-core/hooks/useNow";
import { browserTimezone } from "@waypoint/web-core/recurrence";
import {
  launchRowView,
  messageRowView,
  sortScheduled,
  type ScheduledAction,
  type ScheduledLaunchView,
  type ScheduledMessageView,
} from "@waypoint/web-core/scheduledSheet";
import { sessionHref } from "@waypoint/web-core/shellNav";
import type { MessageSchedule, ScheduledSession } from "@waypoint/web-core/types";
import { splitPath } from "@waypoint/web-core/workspacePaths";

import { Clamped } from "@/components/session/cards/Clamped";
import { PathLabel } from "@/components/session/cards/PathLabel";
import { ActionChip } from "@/components/ui/ActionChip";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Dialog } from "@/components/ui/Dialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { useToast } from "@/components/ui/Toast";
import { handleAuthFailure } from "@/lib/auth";
import { cx } from "@/lib/cx";
import { useShellData } from "@/lib/shell-data";

import styles from "./ScheduledSheet.module.css";

// Finished rows shown before "N more".
const HISTORY_SHOWN = 5;

const ACTION_LABEL: Record<ScheduledAction, string> = {
  open: "Open session",
  openLast: "Open last run",
  sendNow: "Send now",
  cancel: "Cancel",
  dismiss: "Dismiss",
};

type RowView = ScheduledLaunchView | ScheduledMessageView;

function useFailure() {
  const { toast } = useToast();
  return (error: unknown, fallback: string) => {
    if (isAuthError(error)) handleAuthFailure();
    else toast({ message: error instanceof Error ? error.message : fallback, tone: "error" });
  };
}

interface ScheduledSheetProps {
  onClose: () => void;
  schedules: ScheduledSession[];
  messages: MessageSchedule[];
  titleById: ReadonlyMap<string, string>;
  catalog: BackendCatalog;
}

// Rows update from the schedule broadcast, so the actions only call the server.
export function ScheduledSheet({ onClose, schedules, messages, titleById, catalog }: ScheduledSheetProps) {
  const now = new Date(useNow(30_000));
  const zone = browserTimezone();
  const launches = useMemo(() => sortScheduled(schedules), [schedules]);
  const sends = useMemo(() => sortScheduled(messages), [messages]);
  const pending = [...launches, ...sends].filter((row) => row.status === "pending").length;

  return (
    <Dialog open onClose={onClose} title="Scheduled" subtitle={`${pending} pending`} size="lg">
      {launches.length === 0 && sends.length === 0 ? (
        <EmptyState title="Nothing scheduled" />
      ) : (
        <div className={styles.groups}>
          <Group
            kind="launch"
            title="Sessions"
            rows={launches.map((schedule) => launchRowView(schedule, catalog, now, zone))}
            onClose={onClose}
          />
          <Group
            kind="message"
            title="Messages"
            rows={sends.map((message) => messageRowView(message, titleById.get(message.session_id) ?? null, now, zone))}
            onClose={onClose}
          />
        </div>
      )}
    </Dialog>
  );
}

interface GroupProps {
  kind: "launch" | "message";
  title: string;
  rows: RowView[];
  onClose: () => void;
}

function Group({ kind, title, rows, onClose }: GroupProps) {
  const { host, token } = useShellData();
  const { toast } = useToast();
  const fail = useFailure();
  const [expanded, setExpanded] = useState(false);
  const [confirming, setConfirming] = useState(false);
  if (rows.length === 0) return null;
  const pending = rows.filter((row) => !row.resolved);
  const history = rows.filter((row) => row.resolved);
  const shown = expanded ? rows : [...pending, ...history.slice(0, HISTORY_SHOWN)];
  const hidden = rows.length - shown.length;
  const noun = kind === "launch" ? "session launch" : "message";

  async function clear() {
    try {
      const clearHistory = kind === "launch" ? clearScheduleHistory : clearMessageScheduleHistory;
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
          <Row key={row.key} row={row} onClose={onClose} />
        ))}
      </ul>
      {hidden > 0 ? (
        <ActionChip icon="chevron-down" onClick={() => setExpanded(true)}>
          {hidden} more
        </ActionChip>
      ) : null}
      <ConfirmDialog
        open={confirming}
        title="Clear history"
        detail={`Remove ${history.length} finished ${noun}${history.length === 1 ? "" : "s"}? Pending ones stay.`}
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
  const fail = useFailure();
  const [busy, setBusy] = useState(false);
  const launch = "folder" in row ? row : null;
  const message = "target" in row ? row : null;
  const id = row.key.slice(row.key.indexOf(":") + 1);

  async function act(action: ScheduledAction) {
    if (action === "open" || action === "openLast") {
      const sessionId = row.sessionId;
      if (!sessionId) return;
      onClose();
      router.push(sessionHref(sessionId));
      return;
    }
    setBusy(true);
    try {
      if (action === "sendNow") {
        const { held } = await sendMessageScheduleNow(host, token, id);
        toast({ message: held ? "Held: the session will send it when it can" : "Sent" });
      } else if (launch) {
        await cancelSchedule(host, token, id);
      } else {
        await deleteMessageSchedule(host, token, id);
      }
    } catch (error) {
      fail(error, "Could not update the schedule");
    } finally {
      setBusy(false);
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
            to <span className={styles.targetName}>{message.target}</span>
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
              className={action === "cancel" || action === "dismiss" ? styles.quiet : styles.strong}
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
