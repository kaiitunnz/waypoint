import {
  buildBackendUrl,
  formatAddress,
  splitBackendUrl,
  type BackendProtocol,
} from "./backendAddress";
import type { RecentHost } from "./recentHosts";
import {
  canonicalBackendKey,
  DEFAULT_BACKEND_PORT,
  deploymentBackendPort,
  sameBackendUrl,
  type TailnetPeer,
  type TailnetSnapshot,
} from "./tailnet";
import type { LaunchTargetSummary } from "./types";

const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]"]);

// The port of the most recent sign-in under any of `hostnames` (newest first in
// `recents`), else Waypoint's default.
export function portForHost(
  hostnames: (string | null | undefined)[],
  recents: RecentHost[],
): number {
  const wanted = new Set(
    hostnames.filter((name): name is string => !!name).map((name) => name.toLowerCase()),
  );
  for (const recent of recents) {
    const address = splitBackendUrl(recent.url);
    if (address && wanted.has(address.hostname.toLowerCase())) {
      return address.port;
    }
  }
  return DEFAULT_BACKEND_PORT;
}

// A recent host the tailnet snapshot does not list has no known state.
export type HostOnline = "online" | "offline" | "unknown";

export interface HostRow {
  key: string;
  kind: "host" | "ssh";
  name: string;
  address: string;
  // Full backend URL; empty for SSH targets.
  url: string;
  online: HostOnline;
  lastUsedAt: number | null;
  // Set on SSH target rows.
  targetId?: string;
  searchText: string;
}

export interface HostGroups {
  thisDevice: HostRow | null;
  recent: HostRow[];
  online: HostRow[];
  offline: HostRow[];
  // The This device row's key, and the keys of every address that names the same
  // machine, so a selection made under an alias still lands on that row.
  deviceKey: string;
  deviceAliases: ReadonlySet<string>;
}

export interface BuildHostGroupsInput {
  pageHost: string;
  protocol?: BackendProtocol;
  // The deployment's backend port, used for the host serving this page.
  devicePort?: number;
  snapshot: TailnetSnapshot | null;
  // True when `snapshot` is the page host's own tailnet view, so its `is_self`
  // peer is This device. A signed-in backend's snapshot marks the backend as
  // `is_self`; This device is then the peer matching `pageHost`.
  selfIsPageHost?: boolean;
  recents: RecentHost[];
  query?: string;
}

// The machine serving this page: host name, scheme and the deployment's backend
// port. Reads as localhost outside a browser.
export function pageDevice(): { pageHost: string; protocol: BackendProtocol; devicePort: number } {
  const location = typeof window === "undefined" ? null : window.location;
  return {
    pageHost: location?.hostname || "localhost",
    protocol: location?.protocol === "https:" ? "https" : "http",
    devicePort: deploymentBackendPort(),
  };
}

function makeRow(
  fields: Omit<HostRow, "key" | "kind" | "address" | "searchText"> & { extraSearch?: (string | null)[] },
): HostRow {
  const { extraSearch = [], ...row } = fields;
  const address = formatAddress(row.url);
  const searchText = [row.name, address, ...extraSearch]
    .filter((part): part is string => !!part)
    .join(" ")
    .toLowerCase();
  return { ...row, kind: "host", key: canonicalBackendKey(row.url), address, searchText };
}

function matchesQuery(row: HostRow, tokens: string[]): boolean {
  return tokens.every((token) => row.searchText.includes(token));
}

const byName = (a: HostRow, b: HostRow) =>
  a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" });

function peerMatching(snapshot: TailnetSnapshot | null, hostname: string): TailnetPeer | null {
  if (!snapshot?.available) {
    return null;
  }
  const wanted = hostname.toLowerCase();
  return (
    snapshot.peers.find(
      (peer) =>
        peer.ip === wanted ||
        peer.dns_name?.toLowerCase() === wanted ||
        peer.name.toLowerCase() === wanted,
    ) ?? null
  );
}

// Groups for the picker, top to bottom: This device, Recent (newest first),
// Online (alphabetical), Offline. A host appears once, in its highest group.
export function buildHostGroups({
  pageHost,
  protocol = "http",
  devicePort = DEFAULT_BACKEND_PORT,
  snapshot,
  selfIsPageHost = true,
  recents,
  query = "",
}: BuildHostGroupsInput): HostGroups {
  const claimed = new Set<string>();
  const available = snapshot?.available ? snapshot : null;
  const selfPeer = selfIsPageHost
    ? (available?.peers.find((peer) => peer.is_self) ?? null)
    : peerMatching(snapshot, pageHost);

  const deviceUrl = buildBackendUrl({ protocol, hostname: pageHost, port: devicePort });
  const deviceKey = canonicalBackendKey(deviceUrl);
  claimed.add(deviceKey);
  // Every hostname that reaches the machine serving this page. Loopback counts
  // only when the page itself is on loopback: from another machine `localhost`
  // is the browser's own machine.
  const deviceHosts = new Set([pageHost.toLowerCase()]);
  if (selfPeer) {
    for (const name of [selfPeer.ip, selfPeer.dns_name, selfPeer.name]) {
      if (name) deviceHosts.add(name.toLowerCase());
    }
  }
  if (LOOPBACK.has(pageHost.toLowerCase())) {
    LOOPBACK.forEach((name) => deviceHosts.add(name));
  }
  const deviceAliases = new Set([...deviceHosts].map((name) => `${name}:${devicePort}`));
  // A sign-in to this machine shows on its This device row, not in Recent.
  let deviceLastUsed: number | null = null;
  const foldedHosts: string[] = [];

  // A recent may name a peer by IP, DNS name or short name; that peer is not also
  // listed under Online or Offline at the port.
  const claimedPeerPorts = new Set<string>();
  const recent: HostRow[] = [];
  for (const entry of recents) {
    const address = splitBackendUrl(entry.url);
    if (!address) continue;
    if (address.port === devicePort && deviceHosts.has(address.hostname.toLowerCase())) {
      deviceLastUsed = Math.max(deviceLastUsed ?? 0, entry.lastUsedAt);
      foldedHosts.push(address.hostname);
      continue;
    }
    const peer = peerMatching(snapshot, address.hostname);
    const row = makeRow({
      name: entry.name,
      url: entry.url,
      online: peer ? (peer.online ? "online" : "offline") : "unknown",
      lastUsedAt: entry.lastUsedAt,
      extraSearch: peer ? [peer.dns_name, peer.ip, peer.os] : [],
    });
    if (claimed.has(row.key)) continue;
    if (peer) claimedPeerPorts.add(`${peer.ip}:${address.port}`);
    claimed.add(row.key);
    recent.push(row);
  }

  const thisDevice = makeRow({
    name: selfPeer?.name ?? pageHost,
    url: deviceUrl,
    online: "online",
    lastUsedAt: deviceLastUsed,
    extraSearch: [selfPeer?.dns_name ?? null, selfPeer?.ip ?? null, selfPeer?.os ?? null, ...foldedHosts],
  });

  const online: HostRow[] = [];
  const offline: HostRow[] = [];
  for (const peer of available?.peers ?? []) {
    if (peer === selfPeer) continue;
    const port = portForHost([peer.ip, peer.dns_name, peer.name], recents);
    const row = makeRow({
      name: peer.name,
      url: buildBackendUrl({ protocol, hostname: peer.ip, port }),
      online: peer.online ? "online" : "offline",
      lastUsedAt: null,
      extraSearch: [peer.dns_name, peer.os],
    });
    if (claimed.has(row.key) || claimedPeerPorts.has(`${peer.ip}:${port}`)) continue;
    claimed.add(row.key);
    (peer.online ? online : offline).push(row);
  }
  online.sort(byName);
  offline.sort(byName);

  const tokens = query.toLowerCase().split(/\s+/).filter(Boolean);
  const keep = (row: HostRow) => matchesQuery(row, tokens);
  return {
    thisDevice: keep(thisDevice) ? thisDevice : null,
    recent: recent.filter(keep),
    online: online.filter(keep),
    offline: offline.filter(keep),
    deviceKey,
    deviceAliases,
  };
}

// Whether the backend runs on the machine serving the page: the same host name,
// or loopback on both sides. Its tailnet snapshot then marks This device `is_self`.
export function isPageDeviceBackend(backendUrl: string, pageHost: string): boolean {
  const address = splitBackendUrl(backendUrl);
  if (!address) {
    return false;
  }
  const host = address.hostname.toLowerCase();
  const page = pageHost.toLowerCase();
  return host === page || (LOOPBACK.has(host) && LOOPBACK.has(page));
}

// The This device row's key for any address of this machine, else the address's own.
export function rowKeyFor(groups: Pick<HostGroups, "deviceKey" | "deviceAliases">, url: string): string {
  const key = canonicalBackendKey(url);
  return groups.deviceAliases.has(key) ? groups.deviceKey : key;
}

// Rows for the host switcher's SSH group; `online` reads as connected.
export function buildSshRows(
  targets: Pick<LaunchTargetSummary, "id" | "name" | "auth" | "connected" | "supported_backends">[],
  query = "",
): HostRow[] {
  const tokens = query.toLowerCase().split(/\s+/).filter(Boolean);
  return targets
    .map((target): HostRow => {
      const connected = (target.auth ?? "key") === "key" || Boolean(target.connected);
      return {
        key: `ssh:${target.id}`,
        kind: "ssh",
        name: target.name,
        address: target.supported_backends.join(", "),
        url: "",
        online: connected ? "online" : "offline",
        lastUsedAt: null,
        targetId: target.id,
        searchText: `${target.name} ${target.supported_backends.join(" ")} ssh`.toLowerCase(),
      };
    })
    .filter((row) => matchesQuery(row, tokens));
}

// "Today", "Yesterday", a weekday within the last week, else "Nov 3".
export function formatLastUsed(timestamp: number, now: number = Date.now()): string {
  const day = (ms: number) => {
    const date = new Date(ms);
    return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  };
  const diffDays = Math.round((day(now) - day(timestamp)) / 86_400_000);
  if (diffDays <= 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  const date = new Date(timestamp);
  if (diffDays < 7) {
    return date.toLocaleDateString("en-US", { weekday: "short" });
  }
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

// The host chip's label: the SSH target's name, the name recorded at sign-in,
// "Local" for loopback, the whole hostname for an IP, else the first DNS label.
export function describeHost(
  url: string,
  options: { targetName?: string | null; recents?: RecentHost[] } = {},
): string {
  if (options.targetName) {
    return options.targetName;
  }
  const recent = options.recents?.find((entry) => sameBackendUrl(entry.url, url));
  if (recent?.name) {
    return recent.name;
  }
  const address = splitBackendUrl(url);
  if (!address) {
    return url;
  }
  if (LOOPBACK.has(address.hostname)) {
    return "Local";
  }
  if (/^\d+\.\d+\.\d+\.\d+$/.test(address.hostname) || address.hostname.startsWith("[")) {
    return address.hostname;
  }
  return address.hostname.split(".")[0] || address.hostname;
}
