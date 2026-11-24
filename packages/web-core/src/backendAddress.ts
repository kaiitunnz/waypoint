import { DEFAULT_BACKEND_PORT } from "./tailnet";

export type BackendProtocol = "http" | "https";

export interface BackendAddress {
  protocol: BackendProtocol;
  hostname: string;
  port: number;
}

function isValidPort(value: number): boolean {
  return Number.isInteger(value) && value >= 1 && value <= 65535;
}

// Null for anything that is not a whole number in 1-65535.
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

// The port is dropped only when it is the protocol default.
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

// A host made of digits and dots must be a whole IPv4 address, and none may end
// in a dot: anything less is still being typed.
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
  // Null when the text names no scheme.
  protocol: BackendProtocol | null;
  hostname: string;
  // Null when the text names no port.
  port: number | null;
}

const HOST_WITH_PORT = /^(\[[^\]]+\]|[^\s:/?#]+)(?::(\d+))?(?:[/?#].*)?$/;
const VALID_HOSTNAME = /^(?:\[[0-9a-f:.]+\]|[a-z0-9_.-]+)$/;

// Reads a bare host, `host:port` or a full URL. A URL with a scheme and no port is
// on the scheme's default port.
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

// A host from the address box with the port field's text, which the caller validates.
export interface TypedAddress {
  protocol: BackendProtocol;
  hostname: string;
  portText: string;
}

// The scheme in the text wins, else `pageProtocol` (an https page cannot reach an
// http backend).
export function resolveTypedAddress(
  hostText: string,
  portText: string,
  pageProtocol: BackendProtocol,
): TypedAddress | null {
  const parsed = parseAddress(hostText);
  return parsed ? { protocol: parsed.protocol ?? pageProtocol, hostname: parsed.hostname, portText } : null;
}

// The port field follows a port the host text newly names (`host:9000`,
// `https://host`) and otherwise keeps what was entered there.
export function portAfterHostEdit(previousText: string, nextText: string, portText: string): string {
  const next = parseAddress(nextText)?.port ?? null;
  return next !== null && next !== (parseAddress(previousText)?.port ?? null) ? String(next) : portText;
}

// The hostname, plus `:port` unless the port is Waypoint's default (8787) or
// the one https implies (443).
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
