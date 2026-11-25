import { ApiError, cloneSession, forkSession, isAuthError, postAction, sendInput } from "../api";
import { liveTerminal, supportsFork, supportsReattachAfterExit, type BackendCatalog } from "../backends";
import { matchControlCommand } from "../composer-completions";
import type { SessionCommandInvocation, SessionRecord } from "../types";

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

// A control command created its session, and sending its arguments there failed:
// trying again would create another.
export class FollowUpError extends Error {
  readonly sessionId: string;
  readonly status: number | undefined;

  constructor(cause: Error, sessionId: string) {
    super(cause.message);
    this.name = "FollowUpError";
    this.sessionId = sessionId;
    this.status = cause instanceof ApiError ? cause.status : undefined;
  }
}

export interface ControlCommandOptions {
  host: string;
  token: string;
  sessionId: string;
  text: string;
  allowFork: boolean;
}

// ``/new`` and ``/fork`` are handled by the client, not forwarded to the agent.
// Returns the id of the session to open, or null when the text is neither.
export async function runControlCommand({
  host,
  token,
  sessionId,
  text,
  allowFork,
}: ControlCommandOptions): Promise<string | null> {
  const newArgs = matchControlCommand(text, "new");
  if (newArgs !== null) {
    // Cloned server-side so the child inherits the source's private
    // launch_env, which a public SessionRecord cannot carry.
    const created = await cloneSession(host, token, sessionId);
    await sendFollowUp(host, token, created.id, newArgs);
    return created.id;
  }
  const forkArgs = allowFork ? matchControlCommand(text, "fork") : null;
  if (forkArgs !== null) {
    const forked = await forkSession(host, token, sessionId);
    await sendFollowUp(host, token, forked.id, forkArgs);
    return forked.id;
  }
  return null;
}

async function sendFollowUp(host: string, token: string, sessionId: string, text: string): Promise<void> {
  if (!text) return;
  try {
    await sendInput(host, token, sessionId, text);
  } catch (error) {
    if (isAuthError(error)) throw error;
    throw new FollowUpError(error as Error, sessionId);
  }
}

export interface DeliverOptions {
  host: string;
  token: string;
  sessionId: string;
  session: SessionRecord | null;
  catalog: BackendCatalog;
  text: string;
  command?: SessionCommandInvocation;
  attachments?: string[];
  // False sends ``/new`` and ``/fork`` to the agent as text.
  controlCommands?: boolean;
}

export interface Delivery {
  // Set when a control command opened another session instead of sending here.
  openSessionId: string | null;
}

// Sends a turn, first restarting a stopped session whose agent can.
export async function deliverInput({
  host,
  token,
  sessionId,
  session,
  catalog,
  text,
  command,
  attachments,
  controlCommands = true,
}: DeliverOptions): Promise<Delivery> {
  if (composerAvailability(session, catalog).dormant) {
    await postAction(host, token, sessionId, "reattach");
  }
  // A turn carrying attachments is never a control command.
  if (controlCommands && session && !attachments?.length) {
    const allowFork = canFork(session, catalog);
    const openSessionId = await runControlCommand({ host, token, sessionId, text, allowFork });
    if (openSessionId !== null) return { openSessionId };
  }
  await sendInput(host, token, sessionId, text, command, attachments);
  return { openSessionId: null };
}
