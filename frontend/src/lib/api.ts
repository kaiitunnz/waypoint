"use client";

import {
  CodexThreadSummary,
  EventRecord,
  MeResponse,
  ScheduleCreateRequest,
  ScheduledSession,
  SessionEnvelope,
  SessionRecord,
} from "@/lib/types";

export class AuthError extends Error {
  constructor(message = "session expired") {
    super(message);
    this.name = "AuthError";
  }
}

export async function probeBackend(host: string, signal?: AbortSignal): Promise<boolean> {
  try {
    const response = await fetch(`${host}/health`, { cache: "no-store", signal });
    return response.ok;
  } catch {
    return false;
  }
}

export async function login(host: string, password: string): Promise<string> {
  const response = await fetch(`${host}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  const payload = await response.json();
  if (!response.ok || !payload.token) {
    throw new Error(payload.detail ?? "login failed");
  }
  return payload.token as string;
}

export async function fetchSessions(host: string, token: string): Promise<SessionRecord[]> {
  const response = await fetch(`${host}/api/sessions`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  await ensureOk(response, "failed to fetch sessions");
  const payload = await response.json();
  return payload.sessions as SessionRecord[];
}

export async function fetchCodexThreads(
  host: string,
  token: string,
  launchTargetId?: string,
): Promise<CodexThreadSummary[]> {
  const params = new URLSearchParams();
  if (launchTargetId) {
    params.set("launch_target_id", launchTargetId);
  }
  const suffix = params.size ? `?${params.toString()}` : "";
  const response = await fetch(`${host}/api/codex/threads${suffix}`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  await ensureOk(response, "failed to fetch codex threads");
  const payload = await response.json();
  return payload.threads as CodexThreadSummary[];
}

export async function fetchMe(host: string, token: string): Promise<MeResponse> {
  const response = await fetch(`${host}/api/me`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  await ensureOk(response, "failed to fetch backend settings");
  return (await response.json()) as MeResponse;
}

export async function fetchSession(host: string, token: string, sessionId: string): Promise<SessionRecord> {
  const response = await fetch(`${host}/api/sessions/${sessionId}`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  await ensureOk(response, "failed to fetch session");
  const payload = await response.json();
  return payload.session as SessionRecord;
}

export async function fetchEvents(host: string, token: string, sessionId: string): Promise<EventRecord[]> {
  const response = await fetch(`${host}/api/sessions/${sessionId}/events`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  await ensureOk(response, "failed to fetch events");
  const payload = await response.json();
  return payload.events as EventRecord[];
}

export async function fetchTerminalSnapshot(host: string, token: string, sessionId: string): Promise<string> {
  const response = await fetch(`${host}/api/sessions/${sessionId}/terminal-snapshot`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  await ensureOk(response, "failed to fetch terminal snapshot");
  const payload = await response.json();
  return payload.text as string;
}

export async function createSession(
  host: string,
  token: string,
  payload: Record<string, unknown>,
): Promise<SessionRecord> {
  const response = await fetch(`${host}/api/sessions`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  await ensureOk(response, "failed to create session");
  const body = await response.json();
  return body.session as SessionRecord;
}

export async function attachTmux(
  host: string,
  token: string,
  payload: Record<string, unknown>,
): Promise<SessionRecord> {
  const response = await fetch(`${host}/api/sessions/attach-tmux`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  await ensureOk(response, "failed to attach session");
  const body = await response.json();
  return body.session as SessionRecord;
}

export async function importCodexThread(
  host: string,
  token: string,
  payload: Record<string, unknown>,
): Promise<SessionRecord> {
  const response = await fetch(`${host}/api/sessions/import-codex`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  await ensureOk(response, "failed to import codex thread");
  const body = await response.json();
  return body.session as SessionRecord;
}

export async function postAction(
  host: string,
  token: string,
  sessionId: string,
  action: "interrupt" | "resume" | "terminate",
): Promise<void> {
  const response = await fetch(`${host}/api/sessions/${sessionId}/${action}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  await ensureOk(response, `failed to ${action}`);
}

export async function setSessionPinned(
  host: string,
  token: string,
  sessionId: string,
  pinned: boolean,
): Promise<SessionRecord> {
  const response = await fetch(`${host}/api/sessions/${sessionId}/pin`, {
    method: pinned ? "POST" : "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  await ensureOk(response, pinned ? "failed to pin session" : "failed to unpin session");
  const body = await response.json();
  return body.session as SessionRecord;
}

export async function fetchSchedules(host: string, token: string): Promise<ScheduledSession[]> {
  const response = await fetch(`${host}/api/schedules`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  await ensureOk(response, "failed to fetch schedules");
  const payload = await response.json();
  return payload.schedules as ScheduledSession[];
}

export async function createSchedule(
  host: string,
  token: string,
  payload: ScheduleCreateRequest,
): Promise<ScheduledSession> {
  const response = await fetch(`${host}/api/schedules`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  await ensureOk(response, "failed to create schedule");
  const body = await response.json();
  return body.schedule as ScheduledSession;
}

export async function cancelSchedule(host: string, token: string, scheduleId: string): Promise<void> {
  const response = await fetch(`${host}/api/schedules/${scheduleId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  await ensureOk(response, "failed to cancel schedule");
}

export async function clearScheduleHistory(host: string, token: string): Promise<number> {
  const response = await fetch(`${host}/api/schedules/clear-history`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  await ensureOk(response, "failed to clear schedule history");
  const payload = (await response.json()) as { removed?: number };
  return payload.removed ?? 0;
}

export async function deleteSession(host: string, token: string, sessionId: string): Promise<void> {
  const response = await fetch(`${host}/api/sessions/${sessionId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  await ensureOk(response, "failed to delete session");
}

export async function approveSession(
  host: string,
  token: string,
  sessionId: string,
  decision: string,
  text?: string,
): Promise<void> {
  const response = await fetch(`${host}/api/sessions/${sessionId}/approve`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ decision, text }),
  });
  await ensureOk(response, "failed to send approval");
}

export async function sendInput(
  host: string,
  token: string,
  sessionId: string,
  text: string,
): Promise<void> {
  const response = await fetch(`${host}/api/sessions/${sessionId}/input`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ text, submit: true }),
  });
  await ensureOk(response, "failed to send input");
}

interface SocketHandlers {
  onMessage: (message: SessionEnvelope) => void;
  onAuthFailure?: () => void;
  onOpen?: () => void;
  onClose?: (event: CloseEvent) => void;
}

function attachHandlers(socket: WebSocket, handlers: SocketHandlers): WebSocket {
  socket.onmessage = (event) => {
    handlers.onMessage(JSON.parse(event.data) as SessionEnvelope);
  };
  socket.onopen = () => {
    handlers.onOpen?.();
  };
  socket.onclose = (event) => {
    if (event.code === 4401) {
      handlers.onAuthFailure?.();
      return;
    }
    handlers.onClose?.(event);
  };
  return socket;
}

export function connectSessionsSocket(
  host: string,
  token: string,
  onMessage: (message: SessionEnvelope) => void,
  onAuthFailure?: () => void,
  extra: { onOpen?: () => void; onClose?: (event: CloseEvent) => void } = {},
): WebSocket {
  const url = `${host.replace(/^http/, "ws")}/ws/sessions?token=${encodeURIComponent(token)}`;
  return attachHandlers(new WebSocket(url), { onMessage, onAuthFailure, ...extra });
}

export function connectSessionSocket(
  host: string,
  token: string,
  sessionId: string,
  onMessage: (message: SessionEnvelope) => void,
  onAuthFailure?: () => void,
  extra: { onOpen?: () => void; onClose?: (event: CloseEvent) => void } = {},
): WebSocket {
  const url = `${host.replace(/^http/, "ws")}/ws/sessions/${sessionId}?token=${encodeURIComponent(token)}`;
  return attachHandlers(new WebSocket(url), { onMessage, onAuthFailure, ...extra });
}

export function isAuthError(error: unknown): error is AuthError {
  return error instanceof AuthError;
}

async function ensureOk(response: Response, fallbackMessage: string): Promise<void> {
  if (response.ok) {
    return;
  }
  const detail = await readErrorDetail(response);
  if (response.status === 401) {
    throw new AuthError(detail ?? "session expired");
  }
  throw new Error(detail ?? fallbackMessage);
}

async function readErrorDetail(response: Response): Promise<string | null> {
  try {
    const payload = (await response.json()) as { detail?: string };
    return payload.detail ?? null;
  } catch {
    return null;
  }
}
