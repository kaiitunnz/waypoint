import type { TaskNotificationView } from "../events";

export interface TaskNotificationBadge {
  glyph: string;
  variant: string;
  label: string;
}

// A task lifecycle card: subagent/Agent completion, Monitor events and terminal
// state, or background-command completion.
export const TASK_NOTIFICATION_BADGES: Record<TaskNotificationView["kind"], TaskNotificationBadge> = {
  agent: { glyph: "◇", variant: "task", label: "Agent" },
  monitor: { glyph: "◉", variant: "monitor", label: "Monitor" },
  background_command: { glyph: "›_", variant: "bash", label: "Command" },
  message: { glyph: "↘", variant: "default", label: "Message" },
  unknown: { glyph: "⁂", variant: "default", label: "Task" },
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

export function formatTaskDuration(ms: number): string {
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
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
    parts.push(formatTaskDuration(usage.durationMs));
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

// Collapse whitespace so containment survives wrapping and trailing newlines.
export function flattenWhitespace(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

export function taskNoteHeadline(view: TaskNotificationView): string {
  return view.summary || view.event || "Task notification";
}
