"use client";

const HOST_KEY = "waypoint.host";
const TOKEN_KEY = "waypoint.token";
const TARGETS_KEY = "waypoint.launch-targets";
const RECENT_CWDS_KEY = "waypoint.recent-cwds";
const RECENT_CWDS_LIMIT = 8;

export function readHost(): string {
  if (typeof window === "undefined") {
    return "";
  }
  const saved = window.localStorage.getItem(HOST_KEY);
  const inferred = inferBackendHost();
  if (!saved) {
    return inferred;
  }
  if (shouldReplaceSavedHost(saved, inferred)) {
    return inferred;
  }
  return saved;
}

export function writeHost(host: string): void {
  window.localStorage.setItem(HOST_KEY, host);
}

export function readToken(): string {
  if (typeof window === "undefined") {
    return "";
  }
  return window.localStorage.getItem(TOKEN_KEY) ?? "";
}

export function writeToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  window.localStorage.removeItem(TOKEN_KEY);
}

export function readLaunchTarget(host: string): string {
  if (typeof window === "undefined" || !host) {
    return "";
  }
  const selections = readLaunchTargetSelections();
  return selections[host] ?? "";
}

export function writeLaunchTarget(host: string, targetId: string): void {
  if (!host) {
    return;
  }
  const selections = readLaunchTargetSelections();
  if (targetId) {
    selections[host] = targetId;
  } else {
    delete selections[host];
  }
  window.localStorage.setItem(TARGETS_KEY, JSON.stringify(selections));
}

export function readRecentCwds(host: string, targetId: string): string[] {
  if (typeof window === "undefined" || !host) {
    return [];
  }
  const all = readRecentCwdSelections();
  const scoped = all[recentCwdScope(host, targetId)];
  return Array.isArray(scoped)
    ? scoped.filter((item): item is string => typeof item === "string" && item.length > 0)
    : [];
}

export function pushRecentCwd(host: string, targetId: string, cwd: string): string[] {
  const normalized = cwd.trim();
  if (!host || !normalized) {
    return readRecentCwds(host, targetId);
  }
  const all = readRecentCwdSelections();
  const scope = recentCwdScope(host, targetId);
  const current = Array.isArray(all[scope]) ? all[scope] : [];
  const next = [
    normalized,
    ...current.filter((item): item is string => typeof item === "string" && item !== normalized),
  ].slice(0, RECENT_CWDS_LIMIT);
  all[scope] = next;
  window.localStorage.setItem(RECENT_CWDS_KEY, JSON.stringify(all));
  return next;
}

// Seed the per-scope list with paths the server already knows about
// (existing sessions/schedules), so a returning user gets useful
// suggestions before they create anything new. `entries` should be in
// the order callers want to *prefer* — newer first wins ties. Existing
// localStorage entries are kept and stay ahead of seeded ones.
export function mergeRecentCwds(
  host: string,
  entries: { targetId: string; cwd: string }[],
): void {
  if (typeof window === "undefined" || !host || entries.length === 0) {
    return;
  }
  const all = readRecentCwdSelections();
  const byScope = new Map<string, string[]>();
  for (const { targetId, cwd } of entries) {
    const normalized = cwd.trim();
    if (!normalized) continue;
    const scope = recentCwdScope(host, targetId);
    const list = byScope.get(scope) ?? [];
    if (!list.includes(normalized)) {
      list.push(normalized);
    }
    byScope.set(scope, list);
  }
  let dirty = false;
  for (const [scope, seeded] of byScope) {
    const current = Array.isArray(all[scope])
      ? all[scope].filter((item): item is string => typeof item === "string" && item.length > 0)
      : [];
    const merged = [
      ...current,
      ...seeded.filter((item) => !current.includes(item)),
    ].slice(0, RECENT_CWDS_LIMIT);
    if (merged.length !== current.length || merged.some((item, idx) => item !== current[idx])) {
      all[scope] = merged;
      dirty = true;
    }
  }
  if (dirty) {
    window.localStorage.setItem(RECENT_CWDS_KEY, JSON.stringify(all));
  }
}

function inferBackendHost(): string {
  const protocol = window.location.protocol === "https:" ? "https:" : "http:";
  const hostname = window.location.hostname || "127.0.0.1";
  return `${protocol}//${hostname}:8787`;
}

function readLaunchTargetSelections(): Record<string, string> {
  const raw = window.localStorage.getItem(TARGETS_KEY);
  if (!raw) {
    return {};
  }
  try {
    const parsed = JSON.parse(raw) as Record<string, string>;
    return typeof parsed === "object" && parsed ? parsed : {};
  } catch {
    return {};
  }
}

function recentCwdScope(host: string, targetId: string): string {
  return `${host}::${targetId || "__local__"}`;
}

function readRecentCwdSelections(): Record<string, string[]> {
  const raw = window.localStorage.getItem(RECENT_CWDS_KEY);
  if (!raw) {
    return {};
  }
  try {
    const parsed = JSON.parse(raw) as Record<string, string[]>;
    return typeof parsed === "object" && parsed ? parsed : {};
  } catch {
    return {};
  }
}

function shouldReplaceSavedHost(saved: string, inferred: string): boolean {
  try {
    const savedUrl = new URL(saved);
    const inferredUrl = new URL(inferred);
    const savedIsLoopback = savedUrl.hostname === "127.0.0.1" || savedUrl.hostname === "localhost";
    const inferredIsRemote = inferredUrl.hostname !== "127.0.0.1" && inferredUrl.hostname !== "localhost";
    return savedIsLoopback && inferredIsRemote;
  } catch {
    return false;
  }
}
