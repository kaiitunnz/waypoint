import { spawn } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const MACOS_FALLBACK_BIN = "/Applications/Tailscale.app/Contents/MacOS/Tailscale";
const SUBPROCESS_TIMEOUT_MS = 4000;
// Mirrors backend/src/waypoint/tailnet.py: when the web tier has no local
// tailscale CLI (e.g. it runs as a host process while tailscale lives in a
// sidecar container), discover peers by exec'ing into the labelled sidecar.
const ROLE_LABEL = "waypoint.role=tailscale";
// Multiple Waypoint deployments (and unrelated containers) can share the
// role=tailscale label on one host. The sidecar this deployment owns is
// resolved from the waypointctl state tree (mirrors waypoint.tailnet):
// `waypoint_tailscale.sh` records the active profile under
// `$WAYPOINTCTL_STATE_DIR/tailscale` and names each container by slug.
const STATE_DIR_ENV = "WAYPOINTCTL_STATE_DIR";
const DEFAULT_STATE_DIR = "~/.waypoint";
const ACTIVE_PROFILE_FILE = "active-profile";
const CONTAINER_PREFIX = "waypoint-tailscale-";

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
  try {
    return NextResponse.json(await fetchSnapshot());
  } catch (error) {
    const message = error instanceof Error ? error.message : "tailscale status failed";
    return NextResponse.json(unavailable(message));
  }
}

async function fetchSnapshot(): Promise<TailnetSnapshot> {
  const binary = await resolveBinary();
  if (binary !== null) {
    return snapshotFromStatus(await runCommand(binary, ["status", "--json"]));
  }

  const docker = await firstResolvable("docker");
  if (docker === null) {
    return unavailable("tailscale binary not found on PATH");
  }

  const { container, error } = await selectSidecarContainer(docker);
  if (container === null) {
    return unavailable(error ?? "no waypoint tailscale sidecar running");
  }

  return snapshotFromStatus(
    await runCommand(docker, ["exec", container, "tailscale", "status", "--json"]),
  );
}

function snapshotFromStatus(raw: string): TailnetSnapshot {
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(raw || "{}") as Record<string, unknown>;
  } catch (error) {
    const message = error instanceof Error ? error.message : "invalid JSON";
    return unavailable(`could not parse tailscale output: ${message}`);
  }
  return parseSnapshot(payload);
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

// Resolve the tailscale sidecar this deployment owns. Mirrors the backend's
// _select_sidecar_container: foreign role=tailscale sidecars are excluded by
// cross-checking the running containers against this deployment's state tree —
// the most recently up-ed profile (active-profile marker) wins, falling back to
// the newest owned-and-running sidecar.
async function selectSidecarContainer(
  docker: string,
): Promise<{ container: string | null; error: string | null }> {
  const { names: running, error } = await listContainers(docker, [
    `label=${ROLE_LABEL}`,
    "status=running",
  ]);
  if (error !== null) {
    return { container: null, error };
  }
  if (running.length === 0) {
    return { container: null, error: "no waypoint tailscale sidecar running" };
  }

  const root = tailscaleStateRoot();

  const active = readActiveProfile(root);
  if (active) {
    const name = `${CONTAINER_PREFIX}${active}`;
    if (running.includes(name)) {
      return { container: name, error: null };
    }
  }

  // `running` is newest-first (docker ps order), so the first owned entry is
  // the most recently created sidecar this deployment controls.
  const owned = ownedContainerNames(root);
  const ownedRunning = running.find((name) => owned.has(name));
  if (ownedRunning !== undefined) {
    return { container: ownedRunning, error: null };
  }

  return {
    container: null,
    error: `no tailscale sidecar for this deployment is running (state dir ${root}); run \`waypointctl tailscale up <profile>\``,
  };
}

function tailscaleStateRoot(): string {
  const raw = (process.env[STATE_DIR_ENV] ?? "").trim() || DEFAULT_STATE_DIR;
  const expanded = raw === "~" || raw.startsWith("~/") ? join(homedir(), raw.slice(1)) : raw;
  return join(expanded, "tailscale");
}

function readActiveProfile(root: string): string | null {
  try {
    return readFileSync(join(root, ACTIVE_PROFILE_FILE), "utf-8").trim() || null;
  } catch {
    return null;
  }
}

function ownedContainerNames(root: string): Set<string> {
  try {
    return new Set(
      readdirSync(root, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => `${CONTAINER_PREFIX}${entry.name}`),
    );
  } catch {
    return new Set();
  }
}

async function listContainers(
  docker: string,
  filters: string[],
): Promise<{ names: string[]; error: string | null }> {
  const args = ["ps"];
  for (const spec of filters) {
    args.push("--filter", spec);
  }
  args.push("--format", "{{.Names}}");
  let stdout: string;
  try {
    stdout = await runCommand(docker, args);
  } catch (error) {
    return {
      names: [],
      error: error instanceof Error ? error.message : "docker ps failed",
    };
  }
  const names = stdout
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
  return { names, error: null };
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

function runCommand(command: string, args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    const proc = spawn(command, args, { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      proc.kill("SIGTERM");
      reject(new Error(`${command} timed out`));
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
      reject(new Error(stderr.trim() || `${command} exited with ${code}`));
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
