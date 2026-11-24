import type { SessionRecord } from "../types";

export type ConnectionState = "connecting" | "open" | "reconnecting";

export function isAgentBusy(
  session: SessionRecord,
  connection: ConnectionState,
): boolean {
  // session.status is the source of truth: the runtime flips it to running when
  // the human's input lands and back once the turn ends. Events flush through
  // startTransition + rAF and lag that update, so reading them can report idle
  // between the send and the agent's first chunk.
  return connection === "open" && session.status === "running";
}

export function connectionLabel(
  connection: ConnectionState,
  status: SessionRecord["status"],
): string {
  if (connection === "connecting") return "Connecting";
  if (connection === "reconnecting") return "Reconnecting";
  switch (status) {
    case "running":
      return "Running";
    case "waiting_input":
      return "Waiting on you";
    case "interrupted":
      return "Interrupted";
    case "error":
      return "Error";
    case "exited":
      return "Exited";
    case "starting":
      return "Starting";
    case "idle":
    default:
      return "Live";
  }
}

export function connectionTitle(
  connection: ConnectionState,
  status: SessionRecord["status"],
): string {
  if (connection !== "open") {
    return `Socket ${connection}`;
  }
  return `Socket open · session ${status.replace("_", " ")}`;
}
