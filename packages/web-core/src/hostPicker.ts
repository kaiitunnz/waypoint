import type { RecentHost } from "./recentHosts";
import {
  DEFAULT_BACKEND_PORT,
  deploymentBackendPort,
  sameBackendUrl,
  type TailnetPeer,
  type TailnetSnapshot,
} from "./tailnet";

export type BackendProtocol = "http" | "https";

const LOOPBACK = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);

export interface BackendAddress {
  protocol: BackendProtocol;
  hostname: string;
  port: number;
}

const MAX_PORT = 65535;

export function isValidPort(value: number): boolean {
  return Number.isInteger(value) && value >= 1 && value <= MAX_PORT;
}

// Parses a port field; null for anything that is not a whole number in 1-65535.
export function parsePort(text: string): number | null {
  const trimmed = text.trim();
  if (!/^\d{1,5}$/.test(trimmed)) {
    return null;
  }
  const value = Number.parseInt(trimmed, 10);
  return isValidPort(value) ? value : null;
}

function protocolDefaultPort(protocol: BackendProtocol): number {
  return protocol === "https" ? 443 : 80;
}

// `http://host:port`; the port is dropped only when it is the protocol default.
export function buildBackendUrl({ protocol, hostname, port }: BackendAddress): string {
  return port === protocolDefaultPort(protocol)
    ? `${protocol}://${hostname}`
    : `${protocol}://${hostname}:${port}`;
}

export function splitBackendUrl(url: string): BackendAddress | null {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return null;
    }
    const protocol: BackendProtocol = parsed.protocol === "https:" ? "https" : "http";
    return {
      protocol,
      hostname: parsed.hostname,
      port: parsed.port ? Number.parseInt(parsed.port, 10) : protocolDefaultPort(protocol),
    };
  } catch {
    return null;
  }
}

// Whether a typed host is finished enough to be worth probing: a host made of
// digits and dots must be a whole IPv4 address, and none may end in a dot.
export function isCompleteHost(hostname: string): boolean {
  if (!hostname || hostname.endsWith(".")) {
    return false;
  }
  if (/^[\d.]+$/.test(hostname)) {
    return /^(?:\d{1,3}\.){3}\d{1,3}$/.test(hostname);
  }
  return true;
}

export interface ParsedAddress {
  // Null when the text names no scheme, so the caller keeps its own.
  protocol: BackendProtocol | null;
  hostname: string;
  // Null when the text names no port, so the caller keeps its own default.
  port: number | null;
}

const HOST_WITH_PORT = /^(\[[^\]]+\]|[^\s:/?#]+)(?::(\d+))?(?:[/?#].*)?$/;
const VALID_HOSTNAME = /^(?:\[[0-9a-f:.]+\]|[a-z0-9_.-]+)$/;

// Reads what a person types or pastes into "Enter an address": a bare host, a
// `host:port`, or a full URL. A port in the text is returned so the port field
// can follow it; a URL with a scheme and no port is on the scheme's default.
export function parseAddress(text: string): ParsedAddress | null {
  const trimmed = text.trim();
  if (!trimmed) {
    return null;
  }
  let protocol: BackendProtocol | null = null;
  let rest = trimmed;
  const scheme = /^([a-z][a-z0-9+.-]*):\/\//i.exec(trimmed);
  if (scheme) {
    const name = scheme[1].toLowerCase();
    if (name !== "http" && name !== "https") {
      return null;
    }
    protocol = name;
    rest = trimmed.slice(scheme[0].length);
  }
  const match = HOST_WITH_PORT.exec(rest);
  if (!match) {
    return null;
  }
  const hostname = match[1].toLowerCase();
  if (!VALID_HOSTNAME.test(hostname)) {
    return null;
  }
  if (match[2] === undefined) {
    return { protocol, hostname, port: protocol ? protocolDefaultPort(protocol) : null };
  }
  const port = parsePort(match[2]);
  return port === null ? null : { protocol, hostname, port };
}

// What the address box hands the picker: a host with the port field's text, which
// the caller validates.
export interface TypedAddress {
  protocol: BackendProtocol;
  hostname: string;
  portText: string;
}

// The scheme in the text wins, else `pageProtocol` (an https page cannot reach an
// http backend); the port is the port field's.
export function resolveTypedAddress(
  hostText: string,
  portText: string,
  pageProtocol: BackendProtocol,
): TypedAddress | null {
  const parsed = parseAddress(hostText);
  return parsed ? { protocol: parsed.protocol ?? pageProtocol, hostname: parsed.hostname, portText } : null;
}

// The port field after the host text changes: it follows a port the text newly
// names (`host:9000`, `https://host`), and otherwise keeps what was entered there.
export function portAfterHostEdit(previousText: string, nextText: string, portText: string): string {
  const next = parseAddress(nextText)?.port ?? null;
  return next !== null && next !== (parseAddress(previousText)?.port ?? null) ? String(next) : portText;
}

// The address shown next to a host: the hostname, plus `:port` unless the port
// is the Waypoint default (8787) or the one its scheme implies on https (443).
export function formatAddress(url: string): string {
  const address = splitBackendUrl(url);
  if (!address) {
    return url;
  }
  const implied = address.protocol === "https" && address.port === 443;
  return address.port === DEFAULT_BACKEND_PORT || implied
    ? address.hostname
    : `${address.hostname}:${address.port}`;
}

// The port a host should start with: the port of its most recent sign-in under
// any of `hostnames` (an address with no port is on its scheme's default), else
// `fallback`. `recents` is newest first.
export function portForHost(
  hostnames: (string | null | undefined)[],
  recents: RecentHost[],
  fallback: number = DEFAULT_BACKEND_PORT,
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
  return fallback;
}

// Online is three-valued: a recent host that the tailnet snapshot does not list
// has no known state, and nothing probes it to find out.
export type HostOnline = "online" | "offline" | "unknown";

export interface HostRow {
  key: string;
  kind: "host" | "ssh";
  name: string;
  address: string;
  // Full backend URL; empty for SSH targets.
  url: string;
  hostname: string;
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
  // True when the snapshot is the page host's own tailnet view, so its `is_self`
  // peer is This device. A signed-in backend's snapshot is that backend's view,
  // where `is_self` is the backend, so This device is the peer matching `pageHost`.
  selfIsPageHost?: boolean;
  recents: RecentHost[];
  query?: string;
}

// The machine serving this page as the picker sees it: its host name, scheme and
// the deployment's backend port. Browser only; elsewhere it reads as localhost.
export function pageDevice(): { pageHost: string; protocol: BackendProtocol; devicePort: number } {
  const location = typeof window === "undefined" ? null : window.location;
  return {
    pageHost: location?.hostname || "localhost",
    protocol: location?.protocol === "https:" ? "https" : "http",
    devicePort: deploymentBackendPort(),
  };
}

export function hostKey(url: string): string {
  const address = splitBackendUrl(url);
  return address ? `${address.hostname.toLowerCase()}:${address.port}` : url.trim().toLowerCase();
}

function makeRow(fields: Omit<HostRow, "key" | "address" | "searchText"> & {
  extraSearch?: (string | null)[];
}): HostRow {
  const { extraSearch = [], ...row } = fields;
  const address = row.url ? formatAddress(row.url) : "";
  const haystack = [row.name, row.hostname, address, ...extraSearch]
    .filter((part): part is string => !!part)
    .join(" ")
    .toLowerCase();
  return {
    ...row,
    key: hostKey(row.url),
    address,
    searchText: haystack,
  };
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

// Groups the reachable hosts for the picker, top to bottom: This device, Recent
// (newest first), Online (alphabetical), Offline. A host appears once, in its
// highest group. Nothing here probes a host.
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
  const deviceKey = hostKey(deviceUrl);
  claimed.add(deviceKey);
  // Every hostname that reaches the machine serving this page. Loopback counts
  // only when the page itself was opened on loopback; from another machine
  // `localhost` is the browser's own machine.
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
  // A sign-in to this machine is kept on its This device row, not listed twice.
  let deviceLastUsed: number | null = null;
  const foldedHosts: string[] = [];

  // A recent entry may name a peer by IP, DNS name or short name; the peer must
  // not also be listed under Online or Offline at that port.
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
      kind: "host",
      name: entry.name,
      url: entry.url,
      hostname: address.hostname,
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
    kind: "host",
    name: selfPeer?.name ?? pageHost,
    url: deviceUrl,
    hostname: pageHost,
    online: "online",
    lastUsedAt: deviceLastUsed,
    extraSearch: [selfPeer?.dns_name ?? null, selfPeer?.ip ?? null, selfPeer?.os ?? null, ...foldedHosts],
  });

  const online: HostRow[] = [];
  const offline: HostRow[] = [];
  for (const peer of available?.peers ?? []) {
    if (peer === selfPeer) continue;
    const port = portForHost([peer.ip, peer.dns_name, peer.name], recents, DEFAULT_BACKEND_PORT);
    const row = makeRow({
      kind: "host",
      name: peer.name,
      url: buildBackendUrl({ protocol, hostname: peer.ip, port }),
      hostname: peer.ip,
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

// The key of the row that stands for `url`: the This device row for any address
// of this machine, else the address's own key.
export function rowKeyFor(groups: Pick<HostGroups, "deviceKey" | "deviceAliases">, url: string): string {
  const key = hostKey(url);
  return groups.deviceAliases.has(key) ? groups.deviceKey : key;
}

// Rows for the host switcher's SSH group; `online` reads as connected.
export function buildSshRows(
  targets: {
    id: string;
    name: string;
    auth?: "key" | "password";
    connected?: boolean;
    supported_backends: string[];
  }[],
  query = "",
): HostRow[] {
  const tokens = query.toLowerCase().split(/\s+/).filter(Boolean);
  return targets
    .map((target) => {
      const auth = target.auth ?? "key";
      const connected = auth === "key" || Boolean(target.connected);
      const row: HostRow = {
        key: `ssh:${target.id}`,
        kind: "ssh",
        name: target.name,
        address: target.supported_backends.join(", "),
        url: "",
        hostname: "",
        online: connected ? "online" : "offline",
        lastUsedAt: null,
        targetId: target.id,
        searchText: `${target.name} ${target.supported_backends.join(" ")} ssh`.toLowerCase(),
      };
      return row;
    })
    .filter((row) => matchesQuery(row, tokens));
}

// "Today", "Yesterday", a weekday within the last week, else "Nov 3". Local time.
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

// The short name a host goes by in the host chip: the SSH target's name, the
// name recorded at sign-in, "Local" for loopback, the whole hostname for an IP,
// else the first DNS label.
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
