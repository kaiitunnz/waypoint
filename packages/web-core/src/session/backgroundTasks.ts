import { parseTaskNotification, readSummary, readToolName, type TaskNotificationKind } from "../events";
import type { EventRecord } from "../types";
import { taskUsageParts } from "./taskNotificationView";
import { commandForEvent, subagentDescription, toolInputForEvent } from "./toolPresentation";
import { buildTranscriptItems, type LeafItem, type ToolPair } from "./transcript";

export type BackgroundStatus = "running" | "completed" | "failed" | "stopped";

export interface BackgroundTask {
  // The launching call's id.
  id: string;
  kind: TaskNotificationKind;
  label: string;
  status: BackgroundStatus;
  startedAt: string;
  // When the task's end was reported.
  endedAt: string | null;
  // "18 tool uses", "2m"; empty while it runs.
  usage: string[];
  // The latest event a monitor reported.
  lastEvent: string | null;
}

export interface BackgroundTasks {
  // In launch order.
  running: BackgroundTask[];
  // Newest end first.
  finished: BackgroundTask[];
}

const TERMINAL: ReadonlySet<string> = new Set<BackgroundStatus>(["completed", "failed", "stopped"]);

// Claude names a launched task in the launch's result text: a background command,
// a monitor and a subagent each use a form of their own.
const TASK_ID_IN_RESULT = [/running in background with ID:\s*([\w-]+)/i, /Monitor started \(task\s+([\w-]+)/i, /agentId:\s*([\w-]+)/i];

function taskIdFromResult(result: EventRecord | null): string | null {
  const text = result?.text ?? "";
  for (const pattern of TASK_ID_IN_RESULT) {
    const found = pattern.exec(text)?.[1];
    if (found) return found;
  }
  return null;
}

function kindOf(tool: string | null): TaskNotificationKind {
  if (tool === "Monitor") return "monitor";
  if (tool === "Agent" || tool === "Task") return "agent";
  return "background_command";
}

// A call that hands its work to the background: its result only acknowledges the
// launch, or it asked to run in the background, or it is a monitor.
function isLaunch(pair: ToolPair): boolean {
  const call = pair.call;
  const tool = readToolName(call ?? pair.result ?? ({ metadata: {} } as EventRecord));
  if (tool === "Monitor") return true;
  if (pair.result?.metadata?.result_deferred === true) return true;
  return call !== null && toolInputForEvent(call)?.run_in_background === true;
}

function labelOf(pair: ToolPair, kind: TaskNotificationKind): string {
  const call = pair.call;
  if (call) {
    const described = readSummary(call) ?? subagentDescription(call);
    if (described) return described;
    const command = commandForEvent(call)?.trim().split("\n")[0];
    if (command) return command;
  }
  return kind === "agent" ? "Subagent" : kind === "monitor" ? "Monitor" : "Background command";
}

function* pairsOf(items: ReturnType<typeof buildTranscriptItems>): Generator<ToolPair> {
  const leaves = (list: LeafItem[]) => list.flatMap((leaf) => (leaf.kind === "pair" ? [leaf.pair] : []));
  for (const item of items) {
    if (item.kind === "pair") yield item.pair;
    else if (item.kind === "tool_run" || item.kind === "notification_run") yield* leaves(item.items);
  }
}

// The background tasks the loaded events show: those launched in the background and
// not yet reported finished. Only the loaded window is seen, so a task launched
// before it is missed. A report that finishes a task names its launching call or the
// task; the sweep that follows a resume names neither, and ends every task of its kind
// launched before it, since their processes went with the session.
export function backgroundTasks(events: readonly EventRecord[], options: { exited?: boolean } = {}): BackgroundTasks {
  const ordered = [...events].sort((a, b) => a.sequence - b.sequence);
  const tasks = new Map<string, BackgroundTask>();
  const launchedAt = new Map<string, number>();
  const byTaskId = new Map<string, string>();
  for (const pair of pairsOf(buildTranscriptItems(ordered))) {
    if (!isLaunch(pair)) continue;
    const kind = kindOf(readToolName((pair.call ?? pair.result) as EventRecord));
    const taskId = taskIdFromResult(pair.result);
    tasks.set(pair.itemId, {
      id: pair.itemId,
      kind,
      label: labelOf(pair, kind),
      status: "running",
      startedAt: (pair.call ?? pair.result)?.ts ?? "",
      endedAt: null,
      usage: [],
      lastEvent: null,
    });
    launchedAt.set(pair.itemId, (pair.call ?? pair.result)?.sequence ?? 0);
    if (taskId) byTaskId.set(taskId, pair.itemId);
  }

  for (const event of ordered) {
    const note = parseTaskNotification(event);
    if (!note) continue;
    const id = (note.toolUseId && tasks.has(note.toolUseId) ? note.toolUseId : null) ?? (note.taskId ? byTaskId.get(note.taskId) : undefined);
    const task = id ? tasks.get(id) : undefined;
    const status = note.status?.toLowerCase() ?? null;
    const terminal = status !== null && TERMINAL.has(status);
    if (task && task.status === "running") {
      if (terminal) {
        task.status = status as BackgroundStatus;
        task.endedAt = event.ts;
        task.usage = taskUsageParts(note.usage);
      } else if (note.event) {
        task.lastEvent = note.event;
      }
    }
    if (status === "stopped" && !note.toolUseId) {
      for (const other of tasks.values()) {
        if (other.status === "running" && other.kind === note.kind && (launchedAt.get(other.id) ?? 0) < event.sequence) {
          other.status = "stopped";
          other.endedAt = event.ts;
        }
      }
    }
  }

  const all = [...tasks.values()];
  return {
    running: options.exited ? [] : all.filter((task) => task.status === "running"),
    finished: all.filter((task) => task.status !== "running").sort((a, b) => (b.endedAt ?? "").localeCompare(a.endedAt ?? "")),
  };
}
