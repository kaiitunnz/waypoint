import { ApiError, cloneSession, forkSession, isAuthError, postAction, sendInput } from "./api";
import type { BackendCatalog } from "./backends";
import { matchControlCommand } from "./composer-completions";
import { canFork, composerAvailability } from "./session/send";
import type { SessionCommandInvocation, SessionRecord } from "./types";

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
    const created = await withFallback("failed to create session", () => cloneSession(host, token, sessionId));
    await sendFollowUp(host, token, created.id, newArgs, "failed to create session");
    return created.id;
  }
  const forkArgs = allowFork ? matchControlCommand(text, "fork") : null;
  if (forkArgs !== null) {
    const forked = await withFallback("failed to fork session", () => forkSession(host, token, sessionId));
    await sendFollowUp(host, token, forked.id, forkArgs, "failed to fork session");
    return forked.id;
  }
  return null;
}

async function sendFollowUp(
  host: string,
  token: string,
  sessionId: string,
  text: string,
  fallback: string,
): Promise<void> {
  if (!text) return;
  try {
    await withFallback(fallback, () => sendInput(host, token, sessionId, text));
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
    await withFallback("failed to reconnect", () => postAction(host, token, sessionId, "reattach"));
  }
  // A turn carrying attachments is never a control command.
  if (controlCommands && session && !attachments?.length) {
    const allowFork = canFork(session, catalog);
    const openSessionId = await runControlCommand({ host, token, sessionId, text, allowFork });
    if (openSessionId !== null) return { openSessionId };
  }
  await withFallback("failed to send input", () => sendInput(host, token, sessionId, text, command, attachments));
  return { openSessionId: null };
}

// Failures keep their own message and class; anything thrown that is not an
// Error gets the step's name.
async function withFallback<T>(message: string, run: () => Promise<T>): Promise<T> {
  try {
    return await run();
  } catch (error) {
    throw error instanceof Error ? error : new Error(message);
  }
}
