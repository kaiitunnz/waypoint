"use client";

const HOST_KEY = "waypoint.host";
const TOKEN_KEY = "waypoint.token";
const TARGETS_KEY = "waypoint.launch-targets";
const RECENT_CWDS_KEY = "waypoint.recent-cwds";
const RECENT_CWDS_LIMIT = 8;
const USAGE_DASHBOARD_OPEN_KEY = "waypoint.usage-dashboard-open";

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
  if (!Array.isArray(scoped)) {
    return [];
  }
  const seen = new Set<string>();
  const out: string[] = [];
  for (const item of scoped) {
    if (typeof item !== "string") continue;
    const normalized = normalizeCwd(item);
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    out.push(normalized);
  }
  return out;
}

export function pushRecentCwd(host: string, targetId: string, cwd: string): string[] {
  const normalized = normalizeCwd(cwd);
  if (!host || !normalized) {
    return readRecentCwds(host, targetId);
  }
  const all = readRecentCwdSelections();
  const scope = recentCwdScope(host, targetId);
  const current = Array.isArray(all[scope]) ? all[scope] : [];
  const next: string[] = [normalized];
  const seen = new Set<string>([normalized]);
  for (const item of current) {
    if (typeof item !== "string") continue;
    const itemNormalized = normalizeCwd(item);
    if (!itemNormalized || seen.has(itemNormalized)) continue;
    seen.add(itemNormalized);
    next.push(itemNormalized);
  }
  const trimmed = next.slice(0, RECENT_CWDS_LIMIT);
  all[scope] = trimmed;
  window.localStorage.setItem(RECENT_CWDS_KEY, JSON.stringify(all));
  return trimmed;
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
    const normalized = normalizeCwd(cwd);
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
    const currentRaw = Array.isArray(all[scope]) ? all[scope] : [];
    const current: string[] = [];
    const seen = new Set<string>();
    for (const item of currentRaw) {
      if (typeof item !== "string") continue;
      const normalized = normalizeCwd(item);
      if (!normalized || seen.has(normalized)) continue;
      seen.add(normalized);
      current.push(normalized);
    }
    const merged = [
      ...current,
      ...seeded.filter((item) => !seen.has(item)),
    ].slice(0, RECENT_CWDS_LIMIT);
    if (
      merged.length !== currentRaw.length ||
      merged.some((item, idx) => item !== currentRaw[idx])
    ) {
      all[scope] = merged;
      dirty = true;
    }
  }
  if (dirty) {
    window.localStorage.setItem(RECENT_CWDS_KEY, JSON.stringify(all));
  }
}

// Cheap path canonicalisation so trivially equivalent paths stop
// fragmenting the recents list (e.g. `/repo/` vs `/repo`, `//foo/bar`
// vs `/foo/bar`). Anything beyond syntax — `~` expansion, relative-
// vs-absolute, symlinks — needs the backend's filesystem and is
// deliberately out of scope here.
function normalizeCwd(cwd: string): string {
  const trimmed = cwd.trim();
  if (!trimmed) return "";
  const collapsed = trimmed.replace(/\/{2,}/g, "/");
  if (collapsed === "/") return collapsed;
  return collapsed.replace(/\/+$/, "");
}

export function readUsageDashboardOpen(): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  return window.localStorage.getItem(USAGE_DASHBOARD_OPEN_KEY) === "1";
}

export function writeUsageDashboardOpen(open: boolean): void {
  if (typeof window === "undefined") {
    return;
  }
  if (open) {
    window.localStorage.setItem(USAGE_DASHBOARD_OPEN_KEY, "1");
  } else {
    window.localStorage.removeItem(USAGE_DASHBOARD_OPEN_KEY);
  }
}

function inferBackendHost(): string {
  const protocol = window.location.protocol === "https:" ? "https:" : "http:";
  const hostname = window.location.hostname || "127.0.0.1";
  const port = process.env.NEXT_PUBLIC_BACKEND_PORT || "8787";
  return `${protocol}//${hostname}:${port}`;
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
