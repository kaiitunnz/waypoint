import type { SessionRecord } from "../types";

export type ConnectionState = "connecting" | "open" | "reconnecting";

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

export function reconnectDelay(attempt: number): number {
  return Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** attempt);
}

export function isAgentBusy(
  session: SessionRecord,
  connection: ConnectionState,
): boolean {
  // session.status is the source of truth: the runtime sets it to running when
  // the human's input lands and clears it when the turn ends. Events lag that
  // update, so deriving busy from them can report idle between the send and
  // the agent's first chunk.
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
