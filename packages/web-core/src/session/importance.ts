import { isModelChangeEvent, isModelSwitchEvent, isPlanEvent, isTaskNotificationEvent } from "../events";
import type { EventRecord } from "../types";

export function isImportantEvent(event: EventRecord): boolean {
  // A backend's own verdict on a note wins over the rules below.
  const visibility = event.metadata?.visibility;
  if (visibility === "detail") {
    return false;
  }
  if (visibility === "important") {
    return true;
  }
  switch (event.kind) {
    case "user_input":
    case "agent_output":
    case "tool_call":
    case "tool_result":
    case "approval_request":
      return true;
    case "system_note":
    case "status_update":
      if (event.metadata?.method === "approval.invalidated") {
        return true;
      }
      // Switch draws an inline divider; keep it in the important view.
      if (isModelChangeEvent(event)) {
        return isModelSwitchEvent(event);
      }
      if (isPlanEvent(event)) {
        return true;
      }
      if (isTaskNotificationEvent(event)) {
        // Task cards are first-class chronological events; the keyword regex
        // below would otherwise include only the ones that happen to say
        // "failed"/"error" and hide "finished"/"completed" ones.
        return true;
      }
      if (typeof event.metadata?.builtin_command === "string") {
        return true;
      }
      return /(approval response|approval timed out|attached|started|terminated|interrupt|resume|failed|error|exited|compact)/i.test(event.text);
    case "raw_terminal_chunk":
      return false;
    default:
      return false;
  }
}
