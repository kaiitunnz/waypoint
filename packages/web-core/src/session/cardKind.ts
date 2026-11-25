import {
  isModelChangeEvent,
  isModelSwitchEvent,
  parseEvent,
  parseTaskNotification,
  planTextForEvent,
  readToolName,
} from "../events";
import { isTodoToolEvent } from "../todos";
import type { EventRecord } from "../types";
import { parseAskUserQuestion } from "./askQuestion";
import { isBlankAgentMessage } from "./cardText";
import type { ToolPair } from "./transcript";

// Claude Code's tool for handing local files to the human.
const SEND_USER_FILE_TOOL = "SendUserFile";

// What a transcript entry renders as. `none` renders nothing.
export type CardKind =
  | "user_message"
  | "ask_answer"
  | "agent_message"
  | "reasoning"
  | "ask_question"
  | "send_file"
  | "todo"
  | "tool"
  | "plan"
  | "status"
  | "model_switch"
  | "task_notification"
  | "diff_note"
  | "rule"
  | "heuristic"
  | "none";

export function cardKindOf(event: EventRecord): CardKind {
  switch (event.kind) {
    case "user_input":
      return event.metadata?.kind === "ask_user_question_answer" ? "ask_answer" : "user_message";
    case "agent_output":
      if (isBlankAgentMessage(event)) return "none";
      return event.metadata?.item_kind === "reasoning" ? "reasoning" : "agent_message";
    case "tool_call":
      if (parseAskUserQuestion(event)) return "ask_question";
      if (readToolName(event) === SEND_USER_FILE_TOOL) return "send_file";
      return isTodoToolEvent(event) ? "todo" : "tool";
    case "tool_result":
      return isTodoToolEvent(event) ? "todo" : "tool";
    // The interactive approval card above the composer carries the prompt; the
    // record is the "Approval response sent" note.
    case "approval_request":
      return "none";
    case "system_note":
    case "status_update":
      if (planTextForEvent(event)) return "plan";
      if (event.metadata?.builtin_command === "/status") return "status";
      if (isModelChangeEvent(event)) {
        // A launch-time mismatch renders nothing here.
        return isModelSwitchEvent(event) ? "model_switch" : "none";
      }
      if (parseTaskNotification(event)) return "task_notification";
      return parseEvent(event).diffPreview ? "diff_note" : "rule";
    default:
      return "heuristic";
  }
}

export function pairCardKindOf({ call, result }: ToolPair): CardKind {
  if (call && parseAskUserQuestion(call)) return "ask_question";
  const first = call ?? result;
  if (first && readToolName(first) === SEND_USER_FILE_TOOL) return "send_file";
  return isTodoToolEvent(call) || isTodoToolEvent(result) ? "todo" : "tool";
}
