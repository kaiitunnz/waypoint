import { sameBackendUrl } from "./tailnet";

const RECENT_HOSTS_KEY = "waypoint.recent-hosts";
const RECENT_HOSTS_LIMIT = 5;

// A backend the user signed in to. `url` is the full backend URL, so each entry
// carries its own port and two backends on one machine stay two entries.
export interface RecentHost {
  url: string;
  name: string;
  lastUsedAt: number;
}

// Tolerant parse of the stored value: malformed entries are dropped, the rest is
// de-duplicated by host and port, newest first, and capped.
export function parseRecentHosts(raw: unknown): RecentHost[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  const entries: RecentHost[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const { url, name, lastUsedAt } = item as Record<string, unknown>;
    if (typeof url !== "string" || !url.trim()) continue;
    entries.push({
      url: url.trim(),
      name: typeof name === "string" && name.trim() ? name.trim() : url.trim(),
      lastUsedAt: typeof lastUsedAt === "number" && Number.isFinite(lastUsedAt) ? lastUsedAt : 0,
    });
  }
  entries.sort((a, b) => b.lastUsedAt - a.lastUsedAt);
  return dedupe(entries).slice(0, RECENT_HOSTS_LIMIT);
}

// Puts `entry` first, replacing any earlier entry for the same host and port.
export function pushRecentHost(list: RecentHost[], entry: RecentHost): RecentHost[] {
  return dedupe([entry, ...list]).slice(0, RECENT_HOSTS_LIMIT);
}

export function readRecentHosts(): RecentHost[] {
  if (typeof window === "undefined") {
    return [];
  }
  try {
    const raw = window.localStorage.getItem(RECENT_HOSTS_KEY);
    return raw ? parseRecentHosts(JSON.parse(raw)) : [];
  } catch {
    return [];
  }
}

export function writeRecentHost(entry: Omit<RecentHost, "lastUsedAt">): void {
  const next = pushRecentHost(readRecentHosts(), { ...entry, lastUsedAt: Date.now() });
  try {
    window.localStorage.setItem(RECENT_HOSTS_KEY, JSON.stringify(next));
  } catch {
    // Best effort; the entry is dropped when storage is unavailable.
  }
}

function dedupe(entries: RecentHost[]): RecentHost[] {
  const out: RecentHost[] = [];
  for (const entry of entries) {
    if (!out.some((kept) => sameBackendUrl(kept.url, entry.url))) {
      out.push(entry);
    }
  }
  return out;
}
