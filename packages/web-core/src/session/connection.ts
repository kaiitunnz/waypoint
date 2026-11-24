import type { SessionRecord } from "../types";

export type ConnectionState = "connecting" | "open" | "reconnecting";

export function isAgentBusy(
  session: SessionRecord,
  connection: ConnectionState,
): boolean {
  // Trust session.status: the runtime flips it to RUNNING when the user's
  // input lands and to IDLE/ERROR/etc. once the turn ends. The previous
  // implementation walked `events` to corroborate, but events flush via
  // startTransition + rAF, lagging the urgent setSession update — so the
  // walk could see the prior turn's `result` system_note and report idle
  // for the entire window between "user sent" and "agent's first chunk."
  return connection === "open" && session.status === "running";
}

export function connectionVariant(
  connection: ConnectionState,
  status: SessionRecord["status"],
): string {
  if (connection !== "open") return connection;
  if (status === "running" || status === "waiting_input") return "open running";
  if (status === "error" || status === "interrupted") return "open warn";
  if (status === "exited") return "open dim";
  return "open";
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
