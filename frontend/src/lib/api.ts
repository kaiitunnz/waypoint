"use client";

import {
  AssistantAttachRequest,
  AssistantResetRequest,
  AssistantSummary,
  AttachmentSpec,
  Backend,
  BackendDescriptor,
  BackendModelListResponse,
  BoardChannel,
  BoardEntry,
  EventRecord,
  EventsPage,
  InboxApprovalAnswer,
  InboxAttachmentRef,
  InboxItem,
  InboxQuestionAnswer,
  InboxStatus,
  LaunchSettingsUpdate,
  MeResponse,
  MessageSchedule,
  ScheduleCreateRequest,
  ScheduledSession,
  SessionLaunchSettings,
  SessionCompletionsResponse,
  SessionCommandInvocation,
  SessionAttachment,
  SessionEnvelope,
  SessionPreset,
  SessionPresetSummary,
  SessionPresetWriteRequest,
  SessionRecord,
  UsageDashboardResponse,
} from "@/lib/types";
import { parseDiffPreviewPayload, type EventDiffPreview } from "@/lib/events";

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

export async function fetchBackendThreads<T = unknown>(
  host: string,
  token: string,
  backend: string,
  options: { launchTargetId?: string | null; accountProfileId?: string | null } = {},
): Promise<T[]> {
  const params = new URLSearchParams();
  if (options.launchTargetId) {
    params.set("launch_target_id", options.launchTargetId);
  }
  if (options.accountProfileId) {
    params.set("account_profile_id", options.accountProfileId);
  }
  const suffix = params.size ? `?${params.toString()}` : "";
  const response = await fetch(
    `${host}/api/backends/${backend}/threads${suffix}`,
    {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    },
  );
  await ensureOk(response, `failed to fetch ${backend} threads`);
  const payload = await response.json();
  return (payload.threads ?? []) as T[];
}

export async function fetchMe(host: string, token: string): Promise<MeResponse> {
  const response = await fetch(`${host}/api/me`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  await ensureOk(response, "failed to fetch backend settings");
  return (await response.json()) as MeResponse;
}

export async function resetAssistant(
  host: string,
  token: string,
  body: AssistantResetRequest = {},
): Promise<AssistantSummary> {
  const response = await fetch(`${host}/api/assistant/reset`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  await ensureOk(response, "failed to reset assistant");
  return (await response.json()) as AssistantSummary;
}

export async function attachAssistant(
  host: string,
  token: string,
  body: AssistantAttachRequest,
): Promise<AssistantSummary> {
  const response = await fetch(`${host}/api/assistant/attach`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  await ensureOk(response, "failed to attach thread");
  return (await response.json()) as AssistantSummary;
}

export async function terminateAssistant(
  host: string,
  token: string,
): Promise<AssistantSummary> {
  const response = await fetch(`${host}/api/assistant/terminate`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  await ensureOk(response, "failed to terminate assistant");
  return (await response.json()) as AssistantSummary;
}

export async function reattachAssistant(
  host: string,
  token: string,
): Promise<AssistantSummary> {
  const response = await fetch(`${host}/api/assistant/reattach`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  await ensureOk(response, "failed to reattach assistant");
  return (await response.json()) as AssistantSummary;
}

export async function fetchBackends(
  host: string,
  token: string,
): Promise<BackendDescriptor[]> {
  const response = await fetch(`${host}/api/backends`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  await ensureOk(response, "failed to fetch backend catalog");
  const payload = await response.json();
  return (payload.backends ?? []) as BackendDescriptor[];
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

export async function fetchSessionCompletionsResponse(
  host: string,
  token: string,
  sessionId: string,
  trigger: string,
  prefix: string,
  forceRefresh = false,
  signal?: AbortSignal,
): Promise<SessionCompletionsResponse> {
  const params = new URLSearchParams();
  params.set("trigger", trigger);
  if (prefix) {
    params.set("prefix", prefix);
  }
  if (forceRefresh) {
    params.set("force_refresh", "true");
  }
  const response = await fetch(
    `${host}/api/sessions/${sessionId}/completions?${params.toString()}`,
    {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
      signal,
    },
  );
  await ensureOk(response, "failed to fetch command completions");
  const payload = (await response.json()) as Partial<SessionCompletionsResponse>;
  return {
    completions: payload.completions ?? [],
    refreshing: payload.refreshing ?? false,
  };
}

export async function fetchEvents(
  host: string,
  token: string,
  sessionId: string,
  options: { messages?: number; beforeSequence?: number } = {},
): Promise<EventsPage> {
  const params = new URLSearchParams();
  if (options.messages !== undefined) {
    params.set("messages", String(options.messages));
  }
  if (options.beforeSequence !== undefined) {
    params.set("before_sequence", String(options.beforeSequence));
  }
  const query = params.toString();
  const url = `${host}/api/sessions/${sessionId}/events${query ? `?${query}` : ""}`;
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  await ensureOk(response, "failed to fetch events");
  const payload = await response.json();
  return {
    events: (payload.events ?? []) as EventRecord[],
    has_more: Boolean(payload.has_more),
    latest_todo: (payload.latest_todo as EventRecord | null) ?? null,
  };
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

// Detail token the backend returns (HTTP 409) when a launch targets a
// password-auth SSH host whose ControlMaster is not connected yet.
export const SSH_MASTER_REQUIRED_DETAIL = "ssh-master-required";

// Detail token the backend returns (HTTP 400) when a launch's working
// directory does not exist; the client surfaces it inline on the cwd field.
export const CWD_NOT_FOUND_DETAIL = "cwd-not-found";

export interface LaunchTargetConnectResult {
  target_id: string;
  connected: boolean;
  detail?: string | null;
}

export async function connectLaunchTarget(
  host: string,
  token: string,
  targetId: string,
  password: string,
): Promise<LaunchTargetConnectResult> {
  const response = await fetch(
    `${host}/api/launch-targets/${encodeURIComponent(targetId)}/connect`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ password }),
    },
  );
  await ensureOk(response, "failed to connect launch target");
  return (await response.json()) as LaunchTargetConnectResult;
}

export async function fetchLaunchTargetStatus(
  host: string,
  token: string,
  targetId: string,
): Promise<LaunchTargetConnectResult> {
  const response = await fetch(
    `${host}/api/launch-targets/${encodeURIComponent(targetId)}/status`,
    {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    },
  );
  await ensureOk(response, "failed to fetch launch target status");
  return (await response.json()) as LaunchTargetConnectResult;
}

export async function disconnectLaunchTarget(
  host: string,
  token: string,
  targetId: string,
): Promise<void> {
  const response = await fetch(
    `${host}/api/launch-targets/${encodeURIComponent(targetId)}/disconnect`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    },
  );
  await ensureOk(response, "failed to disconnect launch target");
}

export async function forkSession(
  host: string,
  token: string,
  sessionId: string,
): Promise<SessionRecord> {
  const response = await fetch(`${host}/api/sessions/${sessionId}/fork`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
  });
  await ensureOk(response, "failed to fork session");
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

export async function importBackendThread(
  host: string,
  token: string,
  backend: string,
  payload: Record<string, unknown>,
): Promise<SessionRecord> {
  const response = await fetch(
    `${host}/api/backends/${backend}/sessions/import`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    },
  );
  await ensureOk(response, `failed to import ${backend} thread`);
  const body = await response.json();
  return body.session as SessionRecord;
}

export async function deleteThread(
  host: string,
  token: string,
  backend: string,
  threadId: string,
  launchTargetId?: string | null,
  accountProfileId?: string | null,
): Promise<void> {
  const params = new URLSearchParams();
  if (launchTargetId) {
    params.set("launch_target_id", launchTargetId);
  }
  if (accountProfileId) {
    params.set("account_profile_id", accountProfileId);
  }
  const suffix = params.size ? `?${params.toString()}` : "";
  const response = await fetch(
    `${host}/api/backends/${backend}/threads/${encodeURIComponent(threadId)}${suffix}`,
    {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` },
    },
  );
  await ensureOk(response, `failed to delete ${backend} thread`);
}

export async function postAction(
  host: string,
  token: string,
  sessionId: string,
  action: "interrupt" | "resume" | "terminate" | "reattach",
): Promise<void> {
  const response = await fetch(`${host}/api/sessions/${sessionId}/${action}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  await ensureOk(response, `failed to ${action}`);
}

export async function refreshSessionRateLimitUsage(
  host: string,
  token: string,
  sessionId: string,
): Promise<SessionRecord> {
  const response = await fetch(
    `${host}/api/sessions/${sessionId}/rate-limit-usage/refresh`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    },
  );
  await ensureOk(response, "failed to refresh rate-limit usage");
  const body = await response.json();
  return body.session as SessionRecord;
}

export async function fetchUsageDashboard(
  host: string,
  token: string,
): Promise<UsageDashboardResponse> {
  const response = await fetch(`${host}/api/usage`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  await ensureOk(response, "failed to fetch usage dashboard");
  return (await response.json()) as UsageDashboardResponse;
}

export async function refreshUsageDashboard(
  host: string,
  token: string,
): Promise<UsageDashboardResponse> {
  const response = await fetch(`${host}/api/usage/refresh`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  await ensureOk(response, "failed to refresh usage dashboard");
  return (await response.json()) as UsageDashboardResponse;
}

export async function setSessionPermissionMode(
  host: string,
  token: string,
  sessionId: string,
  mode: string,
): Promise<SessionRecord> {
  const response = await fetch(`${host}/api/sessions/${sessionId}/mode`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ mode }),
  });
  await ensureOk(response, "failed to update permission mode");
  const body = await response.json();
  return body.session as SessionRecord;
}

export async function fetchLaunchSettings(
  host: string,
  token: string,
  sessionId: string,
): Promise<SessionLaunchSettings> {
  const response = await fetch(
    `${host}/api/sessions/${sessionId}/launch-settings`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  await ensureOk(response, "failed to load launch settings");
  return (await response.json()) as SessionLaunchSettings;
}

export async function updateLaunchSettings(
  host: string,
  token: string,
  sessionId: string,
  update: LaunchSettingsUpdate,
): Promise<SessionRecord> {
  const response = await fetch(
    `${host}/api/sessions/${sessionId}/launch-settings`,
    {
      method: "PATCH",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(update),
    },
  );
  await ensureOk(response, "failed to update launch settings");
  const body = await response.json();
  return body.session as SessionRecord;
}

export async function setSessionModel(
  host: string,
  token: string,
  sessionId: string,
  model: string | null,
): Promise<SessionRecord> {
  const response = await fetch(`${host}/api/sessions/${sessionId}/model`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ model }),
  });
  await ensureOk(response, "failed to update model");
  const body = await response.json();
  return body.session as SessionRecord;
}

export async function setSessionEffort(
  host: string,
  token: string,
  sessionId: string,
  effort: string | null,
): Promise<SessionRecord> {
  const response = await fetch(`${host}/api/sessions/${sessionId}/effort`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ effort }),
  });
  await ensureOk(response, "failed to update effort");
  const body = await response.json();
  return body.session as SessionRecord;
}

export async function fetchBackendModels(
  host: string,
  token: string,
  backend: Backend,
  options: {
    launchTargetId?: string | null;
    includeHidden?: boolean;
    accountProfileId?: string | null;
  } = {},
): Promise<BackendModelListResponse> {
  const params = new URLSearchParams();
  if (options.launchTargetId) {
    params.set("launch_target_id", options.launchTargetId);
  }
  if (options.includeHidden) {
    params.set("include_hidden", "true");
  }
  if (options.accountProfileId) {
    params.set("account_profile_id", options.accountProfileId);
  }
  const suffix = params.size ? `?${params.toString()}` : "";
  const response = await fetch(`${host}/api/backends/${backend}/models${suffix}`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  await ensureOk(response, "failed to fetch backend models");
  return (await response.json()) as BackendModelListResponse;
}

export async function setSessionTitle(
  host: string,
  token: string,
  sessionId: string,
  title: string,
): Promise<SessionRecord> {
  const response = await fetch(`${host}/api/sessions/${sessionId}/title`, {
    method: "PATCH",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ title }),
  });
  await ensureOk(response, "failed to update session title");
  const body = await response.json();
  return body.session as SessionRecord;
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

// ── Session presets ──────────────────────────────────────────────────────

// Fetch one preset. Pass includeSecretValues to get launch_env values back
// (required before hydrating a form from a preset that has env vars); the
// list/bootstrap payloads are always redacted.
export async function fetchSessionPreset(
  host: string,
  token: string,
  presetId: string,
  includeSecretValues = false,
): Promise<SessionPreset> {
  const query = includeSecretValues ? "?include_secret_values=true" : "";
  const response = await fetch(
    `${host}/api/session-presets/${presetId}${query}`,
    { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" },
  );
  await ensureOk(response, "failed to fetch preset");
  const body = await response.json();
  return body.preset as SessionPreset;
}

export async function createSessionPreset(
  host: string,
  token: string,
  payload: SessionPresetWriteRequest,
): Promise<SessionPresetSummary> {
  const response = await fetch(`${host}/api/session-presets`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  await ensureOk(response, "failed to create preset");
  const body = await response.json();
  return body.preset as SessionPresetSummary;
}

export async function updateSessionPreset(
  host: string,
  token: string,
  presetId: string,
  payload: SessionPresetWriteRequest,
): Promise<SessionPresetSummary> {
  const response = await fetch(`${host}/api/session-presets/${presetId}`, {
    method: "PATCH",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  await ensureOk(response, "failed to update preset");
  const body = await response.json();
  return body.preset as SessionPresetSummary;
}

export async function deleteSessionPreset(
  host: string,
  token: string,
  presetId: string,
): Promise<void> {
  const response = await fetch(`${host}/api/session-presets/${presetId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  await ensureOk(response, "failed to delete preset");
}

export async function setDefaultSessionPreset(
  host: string,
  token: string,
  presetId: string,
): Promise<SessionPresetSummary> {
  const response = await fetch(
    `${host}/api/session-presets/${presetId}/default`,
    { method: "POST", headers: { Authorization: `Bearer ${token}` } },
  );
  await ensureOk(response, "failed to set default preset");
  const body = await response.json();
  return body.preset as SessionPresetSummary;
}

export async function clearDefaultSessionPreset(
  host: string,
  token: string,
): Promise<void> {
  const response = await fetch(`${host}/api/session-presets/default`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  await ensureOk(response, "failed to clear default preset");
}

export async function fetchMessageSchedules(
  host: string,
  token: string,
  sessionId?: string,
): Promise<MessageSchedule[]> {
  const params = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  const response = await fetch(`${host}/api/message-schedules${params}`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  await ensureOk(response, "failed to fetch message schedules");
  const payload = await response.json();
  return (payload.message_schedules ?? []) as MessageSchedule[];
}

export async function createMessageSchedule(
  host: string,
  token: string,
  sessionId: string,
  text: string,
  options: {
    submit?: boolean;
    delaySeconds?: number | null;
    scheduledAt?: string | null;
  } = {},
): Promise<MessageSchedule> {
  const body: Record<string, unknown> = { text, submit: options.submit ?? true };
  if (options.delaySeconds != null) body.delay_seconds = options.delaySeconds;
  if (options.scheduledAt != null) body.scheduled_at = options.scheduledAt;
  const response = await fetch(
    `${host}/api/sessions/${sessionId}/message-schedules`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    },
  );
  await ensureOk(response, "failed to create message schedule");
  const payload = await response.json();
  return payload.message_schedule as MessageSchedule;
}

export async function deleteMessageSchedule(
  host: string,
  token: string,
  scheduleId: string,
): Promise<MessageSchedule> {
  const response = await fetch(
    `${host}/api/message-schedules/${encodeURIComponent(scheduleId)}`,
    {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` },
    },
  );
  await ensureOk(response, "failed to delete message schedule");
  const payload = await response.json();
  return payload.message_schedule as MessageSchedule;
}

export async function clearMessageScheduleHistory(
  host: string,
  token: string,
  sessionId?: string,
): Promise<number> {
  const params = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  const response = await fetch(
    `${host}/api/message-schedules/clear-history${params}`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    },
  );
  await ensureOk(response, "failed to clear message schedule history");
  const payload = (await response.json()) as { removed?: number };
  return payload.removed ?? 0;
}

export async function fetchBoardChannels(
  host: string,
  token: string,
): Promise<BoardChannel[]> {
  const response = await fetch(`${host}/api/board`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  await ensureOk(response, "failed to fetch board channels");
  const payload = await response.json();
  return (payload.channels ?? []) as BoardChannel[];
}

export interface BoardChannelPage {
  entries: BoardEntry[];
  logTotal: number;
}

export async function fetchBoardChannel(
  host: string,
  token: string,
  channel: string,
  options: { limit?: number; before?: number } = {},
): Promise<BoardChannelPage> {
  const params = new URLSearchParams();
  if (options.limit !== undefined) {
    params.set("limit", String(options.limit));
  }
  if (options.before !== undefined) {
    params.set("before", String(options.before));
  }
  const suffix = params.size ? `?${params.toString()}` : "";
  const response = await fetch(
    `${host}/api/board/${encodeURIComponent(channel)}${suffix}`,
    {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    },
  );
  await ensureOk(response, "failed to fetch board entries");
  const payload = await response.json();
  const entries = (payload.entries ?? []) as BoardEntry[];
  const logTotal =
    typeof payload.log_total === "number"
      ? payload.log_total
      : entries.filter((entry) => !entry.key).length;
  return { entries, logTotal };
}

export async function postBoardEntry(
  host: string,
  token: string,
  channel: string,
  body: { text: string; key?: string | null; metadata?: Record<string, unknown> },
): Promise<BoardEntry> {
  const response = await fetch(
    `${host}/api/board/${encodeURIComponent(channel)}`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        text: body.text,
        key: body.key ?? null,
        metadata: body.metadata ?? {},
      }),
    },
  );
  await ensureOk(response, "failed to post board entry");
  const payload = await response.json();
  return payload.entry as BoardEntry;
}

export async function clearBoardChannel(
  host: string,
  token: string,
  channel: string,
): Promise<number> {
  const response = await fetch(
    `${host}/api/board/${encodeURIComponent(channel)}/clear`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    },
  );
  await ensureOk(response, "failed to clear board channel");
  const payload = (await response.json()) as { cleared?: number };
  return payload.cleared ?? 0;
}

export async function deleteBoardChannel(
  host: string,
  token: string,
  channel: string,
): Promise<number> {
  const response = await fetch(
    `${host}/api/board/${encodeURIComponent(channel)}`,
    {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` },
    },
  );
  await ensureOk(response, "failed to delete board channel");
  const payload = (await response.json()) as { deleted?: number };
  return payload.deleted ?? 0;
}

export async function deleteBoardEntry(
  host: string,
  token: string,
  channel: string,
  entryId: number,
): Promise<void> {
  const response = await fetch(
    `${host}/api/board/${encodeURIComponent(channel)}/entries/${entryId}`,
    {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` },
    },
  );
  await ensureOk(response, "failed to delete board entry");
}

export async function updateBoardEntry(
  host: string,
  token: string,
  channel: string,
  entryId: number,
  body: { text: string; metadata?: Record<string, unknown> },
): Promise<BoardEntry> {
  const response = await fetch(
    `${host}/api/board/${encodeURIComponent(channel)}/entries/${entryId}`,
    {
      method: "PATCH",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        text: body.text,
        metadata: body.metadata ?? {},
      }),
    },
  );
  await ensureOk(response, "failed to update board entry");
  const payload = await response.json();
  return payload.entry as BoardEntry;
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
  approvalId?: string,
): Promise<void> {
  const response = await fetch(`${host}/api/sessions/${sessionId}/approve`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ decision, text, approval_id: approvalId }),
  });
  await ensureOk(response, "failed to send approval");
}

export async function approvePlan(
  host: string,
  token: string,
  sessionId: string,
  planItemId: string,
  decision: "accept" | "acceptForSession" | "decline" | "cancel",
  text?: string,
): Promise<SessionRecord> {
  const response = await fetch(`${host}/api/sessions/${sessionId}/approve-plan`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ plan_item_id: planItemId, decision, text }),
  });
  await ensureOk(response, "failed to approve plan");
  const body = await response.json();
  return body.session as SessionRecord;
}

export interface AskAnswerPayload {
  question: string;
  answer: string | null;
  notes?: string;
}

export async function answerAskQuestion(
  host: string,
  token: string,
  sessionId: string,
  answer: string,
  toolUseId?: string,
  answers?: AskAnswerPayload[],
): Promise<void> {
  const response = await fetch(
    `${host}/api/sessions/${sessionId}/answer-question`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        answer,
        tool_use_id: toolUseId,
        answers,
      }),
    },
  );
  await ensureOk(response, "failed to send answer");
}

export async function sendInput(
  host: string,
  token: string,
  sessionId: string,
  text: string,
  command?: SessionCommandInvocation,
  attachments?: string[],
): Promise<void> {
  const response = await fetch(`${host}/api/sessions/${sessionId}/input`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      text,
      submit: true,
      command,
      attachments: attachments?.length ? attachments : undefined,
    }),
  });
  await ensureOk(response, "failed to send input");
}

export async function uploadAttachment(
  host: string,
  token: string,
  sessionId: string,
  file: File,
  options: { pin?: boolean } = {},
): Promise<AttachmentSpec> {
  const body = new FormData();
  body.append("file", file, file.name);
  // Reply attachments are pinned at upload so the orphan sweep can't reap
  // them before the requesting session reads them back.
  if (options.pin) {
    body.append("pin", "true");
  }
  const response = await fetch(
    `${host}/api/sessions/${sessionId}/attachments`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body,
    },
  );
  await ensureOk(response, "failed to upload attachment");
  return (await response.json()) as AttachmentSpec;
}

// Every attachment stored for a session, newest first — backs the files
// manager.
export async function fetchSessionAttachments(
  host: string,
  token: string,
  sessionId: string,
): Promise<SessionAttachment[]> {
  const response = await fetch(
    `${host}/api/sessions/${sessionId}/attachments`,
    {
      headers: { Authorization: `Bearer ${token}` },
    },
  );
  await ensureOk(response, "failed to list attachments");
  return (await response.json()) as SessionAttachment[];
}

// Delete every attachment stored for a session ("Delete all").
export async function deleteAllAttachments(
  host: string,
  token: string,
  sessionId: string,
): Promise<void> {
  const response = await fetch(
    `${host}/api/sessions/${sessionId}/attachments`,
    {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` },
    },
  );
  await ensureOk(response, "failed to delete attachments");
}

// Free a server-side attachment blob. Used when a pending attachment is
// removed from the composer before it is sent, so eager uploads don't orphan.
export async function deleteAttachment(
  host: string,
  token: string,
  sessionId: string,
  attachmentId: string,
): Promise<void> {
  const response = await fetch(
    `${host}/api/sessions/${sessionId}/attachments/${attachmentId}`,
    {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` },
    },
  );
  // A 404 means it's already gone — fine for a best-effort cleanup.
  if (!response.ok && response.status !== 404) {
    await ensureOk(response, "failed to delete attachment");
  }
}

export interface WorkspaceTreeEntry {
  name: string;
  kind: "file" | "dir" | "symlink";
  size: number;
  mtime: number;
}

export interface WorkspaceTreePage {
  root: { cwd: string; worktreePath: string | null };
  path: string;
  entries: WorkspaceTreeEntry[];
  truncated: boolean;
  overflow: number | null;
}

export interface WorkspaceFile {
  path: string;
  size: number;
  mtime: number;
  truncated: boolean;
  binary: boolean;
  content: string | null;
  encoding?: string;
}

export async function fetchWorkspaceTree(
  host: string,
  token: string,
  sessionId: string,
  relPath = "",
  opts: { offset?: number; limit?: number } = {},
): Promise<WorkspaceTreePage> {
  const params = new URLSearchParams();
  if (relPath) params.set("path", relPath);
  if (opts.offset) params.set("offset", String(opts.offset));
  if (opts.limit) params.set("limit", String(opts.limit));
  const suffix = params.size ? `?${params.toString()}` : "";
  const response = await fetch(
    `${host}/api/sessions/${sessionId}/workspace/tree${suffix}`,
    {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    },
  );
  await ensureOk(response, "failed to fetch workspace tree");
  const payload = await response.json();
  const rootRaw = payload.root ?? {};
  return {
    root: {
      cwd: typeof rootRaw.cwd === "string" ? rootRaw.cwd : "",
      worktreePath: typeof rootRaw.worktree_path === "string" ? rootRaw.worktree_path : null,
    },
    path: typeof payload.path === "string" ? payload.path : "",
    entries: Array.isArray(payload.entries) ? (payload.entries as WorkspaceTreeEntry[]) : [],
    truncated: Boolean(payload.truncated),
    overflow: typeof payload.overflow === "number" ? payload.overflow : null,
  };
}

export interface WorkspaceResolve {
  path: string;
  kind: "file" | "dir";
}

// Resolve an absolute or base-relative path to its canonical workspace-relative
// path and kind. Used to turn an agent-printed filesystem path in the transcript
// into something the preview/tree can open. Rejects (throws) paths outside the
// workspace or on the denylist.
export async function resolveWorkspacePath(
  host: string,
  token: string,
  sessionId: string,
  path: string,
): Promise<WorkspaceResolve> {
  const params = new URLSearchParams({ path });
  const response = await fetch(
    `${host}/api/sessions/${sessionId}/workspace/resolve?${params.toString()}`,
    {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    },
  );
  await ensureOk(response, "failed to resolve workspace path");
  const payload = await response.json();
  return {
    path: typeof payload.path === "string" ? payload.path : "",
    kind: payload.kind === "dir" ? "dir" : "file",
  };
}

export async function fetchWorkspaceFile(
  host: string,
  token: string,
  sessionId: string,
  relPath: string,
): Promise<WorkspaceFile> {
  const params = new URLSearchParams({ path: relPath });
  const response = await fetch(
    `${host}/api/sessions/${sessionId}/workspace/file?${params.toString()}`,
    {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    },
  );
  await ensureOk(response, "failed to fetch workspace file");
  return (await response.json()) as WorkspaceFile;
}

// Authenticated URL for a workspace file served inline (used by <img> and
// "Open raw" links). Token rides as a query param — same pattern as
// attachmentUrl — because <img>/<a> cannot send an Authorization header.
export function workspaceRawUrl(
  host: string,
  token: string,
  sessionId: string,
  relPath: string,
): string {
  return `${host}/api/sessions/${sessionId}/workspace/file?path=${encodeURIComponent(relPath)}&raw=1&token=${encodeURIComponent(token)}`;
}

export interface WorkspaceGitFileStatus {
  path: string;
  oldPath: string | null;
  // The two porcelain columns. A space means unmodified in that area.
  indexStatus: string;
  worktreeStatus: string;
  untracked: boolean;
}

export interface WorkspaceGitStatus {
  enabled: boolean;
  branch: string | null;
  detached: boolean;
  files: WorkspaceGitFileStatus[];
}

export async function fetchWorkspaceGitStatus(
  host: string,
  token: string,
  sessionId: string,
): Promise<WorkspaceGitStatus> {
  const response = await fetch(
    `${host}/api/sessions/${sessionId}/workspace/git/status`,
    {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    },
  );
  await ensureOk(response, "failed to fetch git status");
  const payload = await response.json();
  const filesRaw = Array.isArray(payload.files) ? payload.files : [];
  return {
    enabled: Boolean(payload.enabled),
    branch: typeof payload.branch === "string" ? payload.branch : null,
    detached: payload.detached === true,
    files: filesRaw.map(
      (entry: Record<string, unknown>): WorkspaceGitFileStatus => ({
        path: typeof entry.path === "string" ? entry.path : "",
        oldPath: typeof entry.old_path === "string" ? entry.old_path : null,
        indexStatus: typeof entry.index_status === "string" ? entry.index_status : " ",
        worktreeStatus:
          typeof entry.worktree_status === "string" ? entry.worktree_status : " ",
        untracked: entry.untracked === true,
      }),
    ),
  };
}

export async function fetchWorkspaceGitDiff(
  host: string,
  token: string,
  sessionId: string,
  relPath: string,
  staged = false,
): Promise<EventDiffPreview | null> {
  const params = new URLSearchParams({ path: relPath });
  if (staged) params.set("staged", "1");
  const response = await fetch(
    `${host}/api/sessions/${sessionId}/workspace/git/diff?${params.toString()}`,
    {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    },
  );
  await ensureOk(response, "failed to fetch git diff");
  return parseDiffPreviewPayload(await response.json());
}

export interface WorkspaceFindMatch {
  path: string;
  kind: "file" | "dir";
}

export interface WorkspaceFindResult {
  matches: WorkspaceFindMatch[];
  truncated: boolean;
}

// Fuzzy file finder ("Go to file"). The backend lists candidates via git (or a
// capped filesystem walk outside a repo), subsequence-matches `q`, and returns
// the top-ranked paths.
export async function fetchWorkspaceFind(
  host: string,
  token: string,
  sessionId: string,
  q: string,
  limit?: number,
): Promise<WorkspaceFindResult> {
  const params = new URLSearchParams({ q });
  if (limit) params.set("limit", String(limit));
  const response = await fetch(
    `${host}/api/sessions/${sessionId}/workspace/find?${params.toString()}`,
    {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    },
  );
  await ensureOk(response, "failed to search workspace");
  const payload = await response.json();
  const matchesRaw = Array.isArray(payload.matches) ? payload.matches : [];
  return {
    matches: matchesRaw
      .map(
        (entry: Record<string, unknown>): WorkspaceFindMatch => ({
          path: typeof entry.path === "string" ? entry.path : "",
          kind: entry.kind === "dir" ? "dir" : "file",
        }),
      )
      .filter((match: WorkspaceFindMatch) => match.path),
    truncated: Boolean(payload.truncated),
  };
}

// Authenticated URL for an uploaded attachment. The token rides as a query
// param because <img>/<a> can't send an Authorization header (mirrors the
// WebSocket endpoints).
export function attachmentUrl(
  host: string,
  token: string,
  sessionId: string,
  attachmentId: string,
): string {
  return `${host}/api/sessions/${sessionId}/attachments/${attachmentId}?token=${encodeURIComponent(token)}`;
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

export interface InboxListPage {
  items: InboxItem[];
  hasMore: boolean;
  cursor: string | null;
}

export interface InboxBlockSubmit {
  answer?: InboxQuestionAnswer | InboxApprovalAnswer | null;
  reply?: { notes?: string | null; attachments?: InboxAttachmentRef[] } | null;
}

export async function fetchInboxList(
  host: string,
  token: string,
  options: { status?: InboxStatus; q?: string; limit?: number; cursor?: string | null } = {},
): Promise<InboxListPage> {
  const params = new URLSearchParams();
  if (options.status) params.set("status", options.status);
  if (options.q) params.set("q", options.q);
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  if (options.cursor) params.set("cursor", options.cursor);
  const suffix = params.size ? `?${params.toString()}` : "";
  const response = await fetch(`${host}/api/inbox${suffix}`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  await ensureOk(response, "failed to fetch inbox");
  const payload = await response.json();
  return {
    items: (payload.items ?? []) as InboxItem[],
    hasMore: Boolean(payload.has_more),
    cursor: (payload.cursor as string | null) ?? null,
  };
}

export async function fetchInboxItem(
  host: string,
  token: string,
  id: string,
): Promise<InboxItem> {
  const response = await fetch(`${host}/api/inbox/${id}`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  await ensureOk(response, "failed to fetch inbox item");
  const payload = await response.json();
  return payload.item as InboxItem;
}

export async function fetchInboxUnresolvedCount(
  host: string,
  token: string,
): Promise<number> {
  const response = await fetch(`${host}/api/inbox/unresolved-count`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  await ensureOk(response, "failed to fetch inbox count");
  const payload = (await response.json()) as { unresolved_count?: number };
  return payload.unresolved_count ?? 0;
}

export async function submitInboxBlock(
  host: string,
  token: string,
  id: string,
  blockId: string,
  body: InboxBlockSubmit,
): Promise<InboxItem> {
  const response = await fetch(`${host}/api/inbox/${id}/blocks/${blockId}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  await ensureOk(response, "failed to submit inbox reply");
  const payload = await response.json();
  return payload.item as InboxItem;
}

export async function markInboxRead(
  host: string,
  token: string,
  id: string,
): Promise<InboxItem> {
  const response = await fetch(`${host}/api/inbox/${id}/read`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  await ensureOk(response, "failed to mark inbox item read");
  const payload = await response.json();
  return payload.item as InboxItem;
}

export async function deleteInboxItem(
  host: string,
  token: string,
  id: string,
): Promise<void> {
  const response = await fetch(`${host}/api/inbox/${id}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  await ensureOk(response, "failed to delete inbox item");
}

// Batch delete a set of items; returns the ids that actually existed and were
// removed (the server ignores unknown ids). Live clients also receive one
// inbox_update {deleted} frame per removed id.
export async function batchDeleteInboxItems(
  host: string,
  token: string,
  ids: string[],
): Promise<string[]> {
  const response = await fetch(`${host}/api/inbox/batch-delete`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ item_ids: ids }),
  });
  await ensureOk(response, "failed to delete inbox items");
  const payload = (await response.json()) as { deleted_ids?: string[] };
  return payload.deleted_ids ?? [];
}

// Empty the resolved folder: removes every resolved item server-side,
// regardless of what the client has loaded. Returns the removed ids.
export async function deleteResolvedInboxItems(
  host: string,
  token: string,
): Promise<string[]> {
  const response = await fetch(`${host}/api/inbox/delete-resolved`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  await ensureOk(response, "failed to delete resolved inbox items");
  const payload = (await response.json()) as { deleted_ids?: string[] };
  return payload.deleted_ids ?? [];
}

export function connectInboxSocket(
  host: string,
  token: string,
  id: string,
  onMessage: (message: SessionEnvelope) => void,
  onAuthFailure?: () => void,
  extra: { onOpen?: () => void; onClose?: (event: CloseEvent) => void } = {},
): WebSocket {
  const url = `${host.replace(/^http/, "ws")}/ws/inbox/${id}?token=${encodeURIComponent(token)}`;
  return attachHandlers(new WebSocket(url), { onMessage, onAuthFailure, ...extra });
}

interface TerminalSocketHandlers {
  onChunk: (text: string) => void;
  // Fixed-grid (emulated) panes own their geometry server-side and announce
  // it out of band as a ``{type:"size"}`` JSON frame, since xterm ignores
  // in-band resize ops. The client applies it via term.resize() so its grid
  // matches the cell-positioned stream.
  onResize?: (cols: number, rows: number) => void;
  onAuthFailure?: () => void;
  // Backend sends 4410 when the underlying tmux pane has exited. The
  // user has to click Reconnect explicitly; we don't auto-retry.
  onSessionExited?: () => void;
  onOpen?: () => void;
  onClose?: (event: CloseEvent) => void;
}

export function connectTerminalSocket(
  host: string,
  token: string,
  sessionId: string,
  handlers: TerminalSocketHandlers,
): WebSocket {
  const url = `${host.replace(/^http/, "ws")}/ws/sessions/${sessionId}/terminal?token=${encodeURIComponent(token)}`;
  const socket = new WebSocket(url);
  socket.onmessage = (event) => {
    if (typeof event.data !== "string") return;
    // Terminal output is always wrapped in a sync-update escape, so it begins
    // with ESC; a leading "{" marks an out-of-band control frame (e.g. size).
    if (event.data.charCodeAt(0) === 0x7b) {
      try {
        const msg = JSON.parse(event.data);
        if (msg?.type === "size" && typeof msg.cols === "number" && typeof msg.rows === "number") {
          handlers.onResize?.(msg.cols, msg.rows);
          return;
        }
      } catch {
        // Not a control frame after all — fall through and treat as output.
      }
    }
    handlers.onChunk(event.data);
  };
  socket.onopen = () => {
    handlers.onOpen?.();
  };
  socket.onclose = (event) => {
    if (event.code === 4401) {
      handlers.onAuthFailure?.();
      return;
    }
    if (event.code === 4410) {
      handlers.onSessionExited?.();
      return;
    }
    handlers.onClose?.(event);
  };
  return socket;
}

export function isAuthError(error: unknown): error is AuthError {
  return error instanceof AuthError;
}

export async function forkSideQuestion(
  host: string,
  token: string,
  sessionId: string,
  sqid: string,
): Promise<SessionRecord> {
  const response = await fetch(
    `${host}/api/sessions/${sessionId}/side-questions/${sqid}/fork`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    },
  );
  await ensureOk(response, "failed to fork side question");
  const body = await response.json();
  return body.session as SessionRecord;
}

export async function dismissSideQuestion(
  host: string,
  token: string,
  sessionId: string,
  sqid: string,
): Promise<void> {
  const response = await fetch(
    `${host}/api/sessions/${sessionId}/side-questions/${sqid}`,
    {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` },
    },
  );
  await ensureOk(response, "failed to dismiss side question");
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
