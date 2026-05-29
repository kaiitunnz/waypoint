export interface TailnetPeer {
  name: string;
  dns_name: string | null;
  ip: string;
  online: boolean;
  os: string | null;
  is_self: boolean;
}

export interface TailnetSnapshot {
  available: boolean;
  error: string | null;
  peers: TailnetPeer[];
}

export const DEFAULT_BACKEND_PORT = 8787;

// Two backend URLs address the same backend when their host and port match,
// ignoring protocol (http vs https) and a missing explicit default port.
// Selection matching compares on this canonical key rather than the raw string.
export function sameBackendUrl(
  a: string | null | undefined,
  b: string | null | undefined,
): boolean {
  if (!a || !b) {
    return false;
  }
  return canonicalBackendKey(a) === canonicalBackendKey(b);
}

function canonicalBackendKey(url: string): string {
  try {
    const parsed = new URL(url);
    const port = parsed.port || (parsed.protocol === "https:" ? "443" : "80");
    return `${parsed.hostname.toLowerCase()}:${port}`;
  } catch {
    return url.trim().toLowerCase().replace(/\/+$/, "");
  }
}

export function backendPortFromUrl(url: string | null | undefined): number | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    if (!parsed.port) return null;
    const parsedPort = Number.parseInt(parsed.port, 10);
    return Number.isFinite(parsedPort) && parsedPort > 0 ? parsedPort : null;
  } catch {
    return null;
  }
}

export async function fetchLocalTailnetSnapshot(signal?: AbortSignal): Promise<TailnetSnapshot> {
  const response = await fetch("/api/tailnet/peers", { cache: "no-store", signal });
  if (!response.ok) {
    return { available: false, error: `frontend route ${response.status}`, peers: [] };
  }
  return (await response.json()) as TailnetSnapshot;
}

export async function fetchBackendTailnetSnapshot(
  host: string,
  token: string,
  signal?: AbortSignal,
): Promise<TailnetSnapshot> {
  const response = await fetch(`${host}/api/tailnet/peers`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    return { available: false, error: `backend route ${response.status}`, peers: [] };
  }
  return (await response.json()) as TailnetSnapshot;
}

export interface BackendOption {
  url: string;
  label: string;
  hint: string | null;
  online: boolean;
  isLocal: boolean;
}

export function buildBackendOptions(
  pageHost: string,
  snapshot: TailnetSnapshot | null,
  port: number = DEFAULT_BACKEND_PORT,
): BackendOption[] {
  const options: BackendOption[] = [];
  const seen = new Map<string, BackendOption>();

  const localUrl = `http://${pageHost}:${port}`;
  const local: BackendOption = {
    url: localUrl,
    label: `Local (${pageHost})`,
    hint: "this device",
    online: true,
    isLocal: true,
  };
  options.push(local);
  seen.set(localUrl, local);

  if (snapshot?.available) {
    for (const peer of snapshot.peers) {
      const url = `http://${peer.ip}:${port}`;
      if (seen.has(url)) {
        const existing = seen.get(url);
        if (existing) {
          existing.hint = combineHints(existing.hint, peerHint(peer));
        }
        continue;
      }
      const option: BackendOption = {
        url,
        label: `${peer.name} (${peer.ip})`,
        hint: peerHint(peer),
        online: peer.online,
        isLocal: false,
      };
      seen.set(url, option);
      options.push(option);
    }
  }

  return options;
}

function peerHint(peer: TailnetPeer): string | null {
  const parts: string[] = [];
  if (peer.is_self) {
    parts.push("self");
  }
  if (peer.os) {
    parts.push(peer.os);
  }
  if (!peer.online) {
    parts.push("offline");
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}

function combineHints(a: string | null, b: string | null): string | null {
  if (!a) return b;
  if (!b) return a;
  return `${a} · ${b}`;
}
