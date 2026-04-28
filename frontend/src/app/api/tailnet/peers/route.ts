import { spawn } from "node:child_process";
import { existsSync } from "node:fs";

import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const MACOS_FALLBACK_BIN = "/Applications/Tailscale.app/Contents/MacOS/Tailscale";
const SUBPROCESS_TIMEOUT_MS = 4000;

interface TailnetPeer {
  name: string;
  dns_name: string | null;
  ip: string;
  online: boolean;
  os: string | null;
  is_self: boolean;
}

interface TailnetSnapshot {
  available: boolean;
  error: string | null;
  peers: TailnetPeer[];
}

export async function GET(): Promise<NextResponse> {
  const binary = await resolveBinary();
  if (binary === null) {
    return NextResponse.json(unavailable("tailscale binary not found on PATH"));
  }
  try {
    const raw = await runTailscaleStatus(binary);
    const payload = JSON.parse(raw) as Record<string, unknown>;
    return NextResponse.json(parseSnapshot(payload));
  } catch (error) {
    const message = error instanceof Error ? error.message : "tailscale status failed";
    return NextResponse.json(unavailable(message));
  }
}

async function resolveBinary(): Promise<string | null> {
  const fromPath = await firstResolvable("tailscale");
  if (fromPath !== null) {
    return fromPath;
  }
  if (existsSync(MACOS_FALLBACK_BIN)) {
    return MACOS_FALLBACK_BIN;
  }
  return null;
}

function firstResolvable(name: string): Promise<string | null> {
  return new Promise((resolve) => {
    const proc = spawn("/usr/bin/env", ["which", name], { stdio: ["ignore", "pipe", "ignore"] });
    let stdout = "";
    proc.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString();
    });
    proc.on("error", () => resolve(null));
    proc.on("close", (code: number | null) => {
      if (code === 0) {
        const trimmed = stdout.trim();
        resolve(trimmed.length > 0 ? trimmed : null);
        return;
      }
      resolve(null);
    });
  });
}

function runTailscaleStatus(binary: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const proc = spawn(binary, ["status", "--json"], { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      proc.kill("SIGTERM");
      reject(new Error("tailscale status timed out"));
    }, SUBPROCESS_TIMEOUT_MS);
    proc.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString();
    });
    proc.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });
    proc.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    proc.on("close", (code: number | null) => {
      clearTimeout(timer);
      if (code === 0) {
        resolve(stdout);
        return;
      }
      reject(new Error(stderr.trim() || `tailscale exited with ${code}`));
    });
  });
}

function parseSnapshot(payload: Record<string, unknown>): TailnetSnapshot {
  const backendState = payload.BackendState;
  if (typeof backendState === "string" && backendState !== "Running") {
    return unavailable(`tailscale state: ${backendState}`);
  }
  const peers: TailnetPeer[] = [];
  const selfPeer = peerFromNode(payload.Self, true);
  if (selfPeer !== null) {
    peers.push(selfPeer);
  }
  const peerMap = (payload.Peer ?? {}) as Record<string, unknown>;
  for (const node of Object.values(peerMap)) {
    const peer = peerFromNode(node, false);
    if (peer !== null) {
      peers.push(peer);
    }
  }
  peers.sort((a, b) => {
    if (a.is_self !== b.is_self) {
      return a.is_self ? -1 : 1;
    }
    if (a.online !== b.online) {
      return a.online ? -1 : 1;
    }
    return a.name.localeCompare(b.name);
  });
  return { available: true, error: null, peers };
}

function peerFromNode(node: unknown, isSelf: boolean): TailnetPeer | null {
  if (!node || typeof node !== "object") {
    return null;
  }
  const record = node as Record<string, unknown>;
  const ip = firstIpv4(record.TailscaleIPs);
  if (ip === null) {
    return null;
  }
  const hostName = typeof record.HostName === "string" ? record.HostName : null;
  const dnsRaw = typeof record.DNSName === "string" ? record.DNSName : null;
  const dnsName = dnsRaw ? dnsRaw.replace(/\.$/, "") : null;
  return {
    name: hostName ?? dnsName ?? ip,
    dns_name: dnsName,
    ip,
    online: Boolean(record.Online) || isSelf,
    os: typeof record.OS === "string" ? record.OS : null,
    is_self: isSelf,
  };
}

function firstIpv4(value: unknown): string | null {
  if (!Array.isArray(value)) {
    return null;
  }
  for (const entry of value) {
    if (typeof entry !== "string") {
      continue;
    }
    if (/^\d+\.\d+\.\d+\.\d+$/.test(entry)) {
      return entry;
    }
  }
  return null;
}

function unavailable(error: string): TailnetSnapshot {
  return { available: false, error, peers: [] };
}
