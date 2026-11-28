import type { TaskNotificationView } from "../events";
import { formatDuration } from "./cardText";
import type { ToolBadge } from "./toolPresentation";
import type { RunGlyph, ToolFamily } from "./toolRun";

// A task lifecycle card: subagent/Agent completion, Monitor events and terminal
// state, or background-command completion.
export const TASK_NOTIFICATION_BADGES: Record<TaskNotificationView["kind"], ToolBadge> = {
  agent: { glyph: "◇", variant: "task", label: "Agent" },
  monitor: { glyph: "◉", variant: "monitor", label: "Monitor" },
  background_command: { glyph: "›_", variant: "bash", label: "Command" },
  message: { glyph: "↘", variant: "default", label: "Message" },
  unknown: { glyph: "⁂", variant: "default", label: "Task" },
};

// A card's glyph takes the colour of the tool family it reports on.
export const TASK_NOTIFICATION_FAMILIES: Record<TaskNotificationView["kind"], ToolFamily | null> = {
  agent: "agent",
  monitor: "bash",
  background_command: "bash",
  message: null,
  unknown: null,
};

export type TaskStatusTone = "ok" | "danger" | "warn" | "off" | "info";

// The terminal state in words, never colour alone.
export function taskStatusLamp(
  view: TaskNotificationView,
): { tone: TaskStatusTone; label: string } | null {
  const status = view.status?.trim();
  if (status) {
    switch (status.toLowerCase()) {
      case "completed":
        return { tone: "ok", label: "Completed" };
      case "failed":
        return { tone: "danger", label: "Failed" };
      case "stopped":
        return { tone: "warn", label: "Stopped" };
      default:
        return { tone: "off", label: status };
    }
  }
  if (view.kind === "monitor" && view.event) {
    return { tone: "info", label: "Event" };
  }
  return null;
}

export function taskUsageParts(usage: TaskNotificationView["usage"]): string[] {
  if (!usage) return [];
  const parts: string[] = [];
  if (usage.subagentTokens != null) {
    parts.push(`${usage.subagentTokens.toLocaleString()} tokens`);
  }
  if (usage.toolUses != null) {
    parts.push(`${usage.toolUses} tool ${usage.toolUses === 1 ? "use" : "uses"}`);
  }
  if (usage.durationMs != null) {
    parts.push(formatDuration(usage.durationMs));
  }
  return parts;
}

export function reportUnavailableText(view: TaskNotificationView): string | null {
  if (view.outputAvailable) {
    // Capture was expected but produced no attachment (file vanished/unreadable).
    return "Report unavailable";
  }
  switch (view.outputUnavailableReason) {
    case null:
      return null;
    case "full output not captured on import":
      return "Full report wasn't captured on import";
    default:
      return view.outputUnavailableReason;
  }
}

export function taskNoteHeadline(view: TaskNotificationView): string {
  return view.summary || view.event || "Task notification";
}

// A card's line where its output stays folded: what happened, or the kind of task.
export function taskNoteTitle(view: TaskNotificationView): string {
  return view.summary || TASK_NOTIFICATION_BADGES[view.kind].label;
}

export interface NotificationRunSummary {
  // "Reports from 2 subagents, 1 background command".
  phrase: string;
  glyphs: RunGlyph[];
}

const NOTIFICATION_NOUNS: Record<TaskNotificationView["kind"], [one: string, many: string]> = {
  agent: ["subagent", "subagents"],
  monitor: ["monitor", "monitors"],
  background_command: ["background command", "background commands"],
  message: ["message", "messages"],
  unknown: ["task", "tasks"],
};

// A run of task notes in plain words, one entry of `kinds` per note.
export function summarizeNotificationRun(kinds: readonly TaskNotificationView["kind"][]): NotificationRunSummary {
  const counts = new Map<TaskNotificationView["kind"], number>();
  for (const kind of kinds) counts.set(kind, (counts.get(kind) ?? 0) + 1);
  const order = Object.keys(NOTIFICATION_NOUNS) as TaskNotificationView["kind"][];
  const present = order.filter((kind) => counts.has(kind));
  const parts = present.map((kind) => {
    const count = counts.get(kind) ?? 0;
    return `${count} ${NOTIFICATION_NOUNS[kind][count === 1 ? 0 : 1]}`;
  });
  return {
    phrase: parts.length > 0 ? `Reports from ${parts.join(", ")}` : "Reports",
    glyphs: present.map((kind) => {
      const many = NOTIFICATION_NOUNS[kind][1];
      return {
        glyph: TASK_NOTIFICATION_BADGES[kind].glyph,
        label: many.charAt(0).toUpperCase() + many.slice(1),
        count: counts.get(kind) ?? 0,
        family: TASK_NOTIFICATION_FAMILIES[kind],
      };
    }),
  };
}
