"use client";

import { EventRecord, SessionEnvelope, SessionRecord } from "@/lib/types";

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
  if (!response.ok) {
    throw new Error("failed to fetch sessions");
  }
  const payload = await response.json();
  return payload.sessions as SessionRecord[];
}

export async function fetchSession(host: string, token: string, sessionId: string): Promise<SessionRecord> {
  const response = await fetch(`${host}/api/sessions/${sessionId}`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error("failed to fetch session");
  }
  const payload = await response.json();
  return payload.session as SessionRecord;
}

export async function fetchEvents(host: string, token: string, sessionId: string): Promise<EventRecord[]> {
  const response = await fetch(`${host}/api/sessions/${sessionId}/events`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error("failed to fetch events");
  }
  const payload = await response.json();
  return payload.events as EventRecord[];
}

export async function fetchTerminalSnapshot(host: string, token: string, sessionId: string): Promise<string> {
  const response = await fetch(`${host}/api/sessions/${sessionId}/terminal-snapshot`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error("failed to fetch terminal snapshot");
  }
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
  if (!response.ok) {
    throw new Error("failed to create session");
  }
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
  if (!response.ok) {
    throw new Error("failed to attach session");
  }
  const body = await response.json();
  return body.session as SessionRecord;
}

export async function postAction(
  host: string,
  token: string,
  sessionId: string,
  action: "interrupt" | "resume",
): Promise<void> {
  const response = await fetch(`${host}/api/sessions/${sessionId}/${action}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    throw new Error(`failed to ${action}`);
  }
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
  if (!response.ok) {
    throw new Error("failed to send input");
  }
}

export function connectSessionsSocket(
  host: string,
  token: string,
  onMessage: (message: SessionEnvelope) => void,
): WebSocket {
  const url = `${host.replace(/^http/, "ws")}/ws/sessions?token=${encodeURIComponent(token)}`;
  const socket = new WebSocket(url);
  socket.onmessage = (event) => {
    onMessage(JSON.parse(event.data) as SessionEnvelope);
  };
  return socket;
}

export function connectSessionSocket(
  host: string,
  token: string,
  sessionId: string,
  onMessage: (message: SessionEnvelope) => void,
): WebSocket {
  const url = `${host.replace(/^http/, "ws")}/ws/sessions/${sessionId}?token=${encodeURIComponent(token)}`;
  const socket = new WebSocket(url);
  socket.onmessage = (event) => {
    onMessage(JSON.parse(event.data) as SessionEnvelope);
  };
  return socket;
}
