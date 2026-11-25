import { liveTerminal, supportsFork, supportsReattachAfterExit, type BackendCatalog } from "../backends";
import type { SessionRecord } from "../types";

// The backend process has stopped (clean shutdown or crash).
export function isExitedSession(session: SessionRecord | null): boolean {
  return session !== null && (session.status === "exited" || session.status === "error");
}

export interface ComposerAvailability {
  disabled: boolean;
  // The session has stopped and sending to it restarts it.
  dormant: boolean;
}

export function composerAvailability(session: SessionRecord | null, catalog: BackendCatalog): ComposerAvailability {
  if (!session) return { disabled: true, dormant: false };
  const exited = isExitedSession(session);
  const dormant = exited && supportsReattachAfterExit(session.backend, catalog);
  return { disabled: exited && !dormant, dormant };
}

// A turn is running, or parked on something an interrupt would end.
export function canInterrupt(session: SessionRecord | null): boolean {
  if (!session) return false;
  return session.status === "running" || (session.status === "waiting_input" && !session.awaiting_answer);
}

// Fork needs the agent to support it and a transport with a fork path: a live
// terminal pane has none.
export function canFork(session: SessionRecord | null, catalog: BackendCatalog): boolean {
  return Boolean(session && supportsFork(session.backend, catalog) && !liveTerminal(session.transport, catalog));
}

// The field after a send that did not go. An immediate send emptied it to `kept`
// when it started, so the message goes back ahead of anything typed since; a
// queued one left it alone. `rest` is what a successful retry leaves behind.
export function draftAfterFailedSend({
  immediate,
  sent,
  current,
  before,
  kept,
}: {
  immediate: boolean;
  // The text that was sent; empty for files alone.
  sent: string;
  current: string;
  before: string;
  kept: string;
}): { draft: string; rest: string } {
  const untouched = current === (immediate ? kept : before);
  let draft = current;
  if (immediate) draft = untouched ? before : [sent, current].filter(Boolean).join("\n");
  return { draft, rest: untouched ? kept : current };
}
