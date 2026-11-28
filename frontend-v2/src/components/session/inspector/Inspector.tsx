"use client";

import { useMemo, useState, type ReactNode } from "react";

import type { BackendCatalog } from "@waypoint/web-core/backends";
import { useWorkspaceGitStatus } from "@waypoint/web-core/hooks/useWorkspaceGitStatus";
import { backgroundTasks } from "@waypoint/web-core/session/backgroundTasks";
import { isExitedSession } from "@waypoint/web-core/session/send";
import type { InspectorActionId, InspectorActions } from "@waypoint/web-core/session/inspectorActions";
import { sessionDetails } from "@waypoint/web-core/session/sessionDetails";
import { currentTodoEvent, readTodoEntries, summarizeTodos } from "@waypoint/web-core/todos";
import type { EventRecord, HeldMessage, SessionRecord } from "@waypoint/web-core/types";

import type { ScheduledMessages } from "@/components/session/attention/useQueueHandlers";
import { useSessionContext } from "@/components/session/SessionContext";
import { cx } from "@/lib/cx";
import { useShellData } from "@/lib/shell-data";

import { ActionsFoot, ActionTiles } from "./Actions";
import { BackgroundSection, backgroundCountText } from "./BackgroundSection";
import { ChangesSection, changesCountText } from "./ChangesSection";
import { DetailsSection } from "./DetailsSection";
import { FocusRow } from "./FocusRow";
import styles from "./Inspector.module.css";
import { LimitsSection } from "./LimitsSection";
import { QueueSection } from "./QueueSection";
import { TasksSection } from "./TasksSection";

export interface InspectorProps {
  // The pane keeps its actions at the foot; the sheet leads with them as tiles.
  surface: "pane" | "sheet";
  session: SessionRecord;
  catalog: BackendCatalog;
  events: EventRecord[];
  latestTodo: EventRecord | null;
  heldMessages: HeldMessage[];
  scheduled: ScheduledMessages;
  focus: { on: boolean; busy: boolean; set: (on: boolean) => Promise<boolean> };
  actions: InspectorActions;
  onAction: (id: InspectorActionId) => void;
  onSession: (session: SessionRecord) => void;
  // The sheet's lead: who the session is.
  identity?: ReactNode;
  // The sheet's transcript density control, where the conversation has one.
  density?: ReactNode;
}

type Summary = "tasks" | "changes" | "background";

// A session's Overview: what it is doing, what it has used, what waits for it, and what it
// is. The side pane and the touch sheet show the same sections.
export function Inspector({
  surface,
  session,
  catalog,
  events,
  latestTodo,
  heldMessages,
  scheduled,
  focus,
  actions,
  onAction,
  onSession,
  identity,
  density,
}: InspectorProps) {
  const { host, token, sessionId } = useSessionContext();
  const { launchTargets } = useShellData();
  const sheet = surface === "sheet";
  const changes = useWorkspaceGitStatus(host, token, sessionId, session.status);
  const progress = useMemo(() => summarizeTodos(readTodoEntries(currentTodoEvent(events, latestTodo))), [events, latestTodo]);
  const exited = isExitedSession(session);
  const background = useMemo(() => backgroundTasks(events, { exited }), [events, exited]);
  const hasBackground = background.running.length + background.finished.length > 0;
  const targetName = launchTargets.targets.find((target) => target.id === session.launch_target_id)?.name ?? null;
  const details = useMemo(
    () => sessionDetails(session, { catalog, launchTargetName: targetName, liveBranch: changes?.branch ?? null }),
    [session, catalog, targetName, changes?.branch],
  );
  const [open, setOpen] = useState<Summary | null>(null);

  const tasks = progress ? <TasksSection progress={progress} /> : null;
  const changed = changes ? <ChangesSection summary={changes} /> : null;
  const running = hasBackground ? <BackgroundSection tasks={background} /> : null;

  // On touch the three are one row of summaries; one opens at a time below it.
  const cells: { id: Summary; label: string; value: string; section: ReactNode }[] = [
    ...(progress ? [{ id: "tasks" as const, label: "Tasks", value: `${progress.completed} of ${progress.total}`, section: tasks }] : []),
    ...(changes ? [{ id: "changes" as const, label: "Changes", value: changesCountText(changes.count), section: changed }] : []),
    ...(hasBackground ? [{ id: "background" as const, label: "Background", value: backgroundCountText(background), section: running }] : []),
  ];

  const focusRow = (
    <FocusRow on={focus.on} busy={focus.busy} onChange={(on) => void focus.set(on)} detail={sheet ? "Hold incoming messages" : undefined} />
  );

  return (
    <div className={cx(styles.root, sheet && styles.sheet)}>
      <div className={styles.body}>
        {identity}
        {sheet ? (
          <div className={styles.tiles}>
            <ActionTiles actions={actions} onAction={onAction} />
          </div>
        ) : null}
        <LimitsSection session={session} catalog={catalog} onSession={onSession} />
        {sheet ? (
          cells.length > 0 ? (
            <div className={styles.summary}>
              <div className={styles.cells} role="group" aria-label="Tasks, changes and background">
                {cells.map((cell) => (
                  <button
                    key={cell.id}
                    type="button"
                    className={styles.cell}
                    aria-expanded={open === cell.id}
                    onClick={() => setOpen(open === cell.id ? null : cell.id)}
                  >
                    <span className={styles.cellLabel}>{cell.label}</span>
                    <span className={styles.cellValue}>{cell.value}</span>
                  </button>
                ))}
              </div>
              {cells.find((cell) => cell.id === open)?.section}
            </div>
          ) : null
        ) : (
          <>
            {tasks}
            {changed}
            {running}
          </>
        )}
        <QueueSection heldMessages={heldMessages} scheduled={scheduled} focus={Boolean(session.focus)} />
        {sheet ? <div className={styles.focus}>{focusRow}</div> : null}
        {sheet && density ? (
          <div className={styles.density}>
            <span>Transcript</span>
            {density}
          </div>
        ) : null}
        <DetailsSection rows={details}>{sheet ? null : <div className={styles.paneFocus}>{focusRow}</div>}</DetailsSection>
      </div>
      <div className={styles.foot}>
        <ActionsFoot actions={actions} onAction={onAction} surface={surface} />
      </div>
    </div>
  );
}
