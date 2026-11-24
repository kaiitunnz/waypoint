import { describe, expect, it } from "vitest";

import {
  buildBackendUrl,
  buildHostGroups,
  buildSshRows,
  describeHost,
  formatAddress,
  formatLastUsed,
  parseAddress,
  parsePort,
  portForHost,
  splitBackendUrl,
} from "./hostPicker";
import type { RecentHost } from "./recentHosts";
import type { TailnetPeer, TailnetSnapshot } from "./tailnet";

const peer = (name: string, ip: string, online = true, extra: Partial<TailnetPeer> = {}): TailnetPeer => ({
  name,
  dns_name: `${name}.tail1234.ts.net`,
  ip,
  online,
  os: "linux",
  is_self: false,
  ...extra,
});

const snapshot = (peers: TailnetPeer[]): TailnetSnapshot => ({ available: true, error: null, peers });
const recent = (url: string, lastUsedAt: number, name = url): RecentHost => ({ url, name, lastUsedAt });
const names = (rows: { name: string }[]) => rows.map((row) => row.name);

describe("parseAddress", () => {
  it("reads a bare host", () => {
    expect(parseAddress("10.0.4.12")).toEqual({ protocol: "http", hostname: "10.0.4.12", port: null });
  });

  it("reads host:port", () => {
    expect(parseAddress("Studio.local:9000")).toEqual({
      protocol: "http",
      hostname: "studio.local",
      port: 9000,
    });
  });

  it("fills the port from a pasted URL and keeps https", () => {
    expect(parseAddress("https://box.example.com:8443/health")).toEqual({
      protocol: "https",
      hostname: "box.example.com",
      port: 8443,
    });
  });

  it("reads bracketed IPv6", () => {
    expect(parseAddress("[::1]:8787")).toEqual({ protocol: "http", hostname: "[::1]", port: 8787 });
  });

  it("rejects empty text, other schemes and bad ports", () => {
    expect(parseAddress("   ")).toBeNull();
    expect(parseAddress("ftp://host")).toBeNull();
    expect(parseAddress("host:0")).toBeNull();
    expect(parseAddress("host:70000")).toBeNull();
    expect(parseAddress("a b")).toBeNull();
  });
});

describe("ports and URLs", () => {
  it("parses ports strictly", () => {
    expect(parsePort("8787")).toBe(8787);
    expect(parsePort(" 9000 ")).toBe(9000);
    expect(parsePort("")).toBeNull();
    expect(parsePort("80a")).toBeNull();
    expect(parsePort("65536")).toBeNull();
  });

  it("builds and splits backend URLs", () => {
    expect(buildBackendUrl({ protocol: "http", hostname: "h", port: 8787 })).toBe("http://h:8787");
    expect(buildBackendUrl({ protocol: "https", hostname: "h", port: 443 })).toBe("https://h");
    expect(splitBackendUrl("http://h:9000")).toEqual({ protocol: "http", hostname: "h", port: 9000 });
    expect(splitBackendUrl("https://h")).toEqual({ protocol: "https", hostname: "h", port: 443 });
    expect(splitBackendUrl("not a url")).toBeNull();
  });

  it("shows the port only when it is not the default", () => {
    expect(formatAddress("http://100.91.2.17:8797")).toBe("100.91.2.17:8797");
    expect(formatAddress("http://100.91.2.17:8787")).toBe("100.91.2.17");
    expect(formatAddress("https://box.example.com")).toBe("box.example.com");
  });

  it("takes a host's port from its most recent sign-in", () => {
    const recents = [recent("http://100.1.1.1:9000", 2), recent("http://100.1.1.1:8800", 1)];
    expect(portForHost(["100.1.1.1"], recents, 8787)).toBe(9000);
    expect(portForHost(["100.2.2.2"], recents, 8787)).toBe(8787);
    expect(portForHost(["100.2.2.2"], recents, 8123)).toBe(8123);
    expect(portForHost([null, "100.1.1.1"], [recent("http://100.1.1.1", 1)], 8787)).toBe(8787);
  });
});

describe("buildHostGroups", () => {
  const manyPeers = (): TailnetPeer[] => {
    const peers: TailnetPeer[] = [
      peer("studio", "100.83.12.4", true, { is_self: true }),
    ];
    for (let i = 1; i <= 32; i += 1) {
      peers.push(peer(`node-${i}`, `100.64.0.${i}`, true));
    }
    for (let i = 1; i <= 61; i += 1) {
      peers.push(peer(`old-${i}`, `100.65.0.${i}`, false));
    }
    return peers;
  };

  it("orders This device, Recent, Online, Offline", () => {
    const groups = buildHostGroups({
      pageHost: "100.83.12.4",
      devicePort: 8787,
      snapshot: snapshot(manyPeers()),
      recents: [recent("http://100.91.2.17:8797", 5, "gpu-box"), recent("http://100.70.8.33:8787", 4, "nus-a100-03")],
    });
    expect(groups.thisDevice?.name).toBe("studio");
    expect(names(groups.recent)).toEqual(["gpu-box", "nus-a100-03"]);
    expect(groups.recent[0].address).toBe("100.91.2.17:8797");
    expect(groups.recent[1].address).toBe("100.70.8.33");
    expect(groups.online).toHaveLength(32);
    expect(groups.offline).toHaveLength(61);
    expect(groups.total).toBe(1 + 2 + 32 + 61);
  });

  it("sorts online hosts alphabetically with natural numbers", () => {
    const groups = buildHostGroups({
      pageHost: "h",
      snapshot: snapshot([
        peer("ci-runner-10", "100.0.0.10"),
        peer("Beta", "100.0.0.3"),
        peer("ci-runner-2", "100.0.0.2"),
        peer("alpha", "100.0.0.1"),
      ]),
      recents: [],
    });
    expect(names(groups.online)).toEqual(["alpha", "Beta", "ci-runner-2", "ci-runner-10"]);
  });

  it("lists a host once, in its highest group", () => {
    const groups = buildHostGroups({
      pageHost: "100.83.12.4",
      snapshot: snapshot([
        peer("studio", "100.83.12.4", true, { is_self: true }),
        peer("gpu-box", "100.91.2.17"),
        peer("lab", "100.7.7.7", false),
      ]),
      recents: [recent("http://100.91.2.17:8787", 3, "gpu-box"), recent("http://100.83.12.4:8787", 2, "studio")],
    });
    expect(names(groups.recent)).toEqual(["gpu-box"]);
    expect(names(groups.online)).toEqual([]);
    expect(names(groups.offline)).toEqual(["lab"]);
    expect(groups.thisDevice?.name).toBe("studio");
  });

  it("keeps a recent host in Recent even when the tailnet says it is offline", () => {
    const groups = buildHostGroups({
      pageHost: "h",
      snapshot: snapshot([peer("lab", "100.7.7.7", false)]),
      recents: [recent("http://100.7.7.7:8787", 1, "lab")],
    });
    expect(groups.recent[0].online).toBe("offline");
    expect(groups.offline).toEqual([]);
  });

  it("marks recents the tailnet does not list as unknown", () => {
    const groups = buildHostGroups({
      pageHost: "h",
      snapshot: snapshot([]),
      recents: [recent("http://10.0.4.12:9000", 1, "10.0.4.12")],
    });
    expect(groups.recent[0].online).toBe("unknown");
    expect(groups.recent[0].address).toBe("10.0.4.12:9000");
  });

  it("points a peer at the port of its most recent sign-in", () => {
    const groups = buildHostGroups({
      pageHost: "h",
      snapshot: snapshot([peer("gpu-box", "100.91.2.17")]),
      recents: [recent("http://gpu-box.tail1234.ts.net:8797", 1, "gpu-box")],
    });
    expect(groups.online).toEqual([]);
    expect(groups.recent).toHaveLength(1);
  });

  it("uses the deployment port for This device", () => {
    const groups = buildHostGroups({ pageHost: "studio.local", devicePort: 8797, snapshot: null, recents: [] });
    expect(groups.thisDevice?.url).toBe("http://studio.local:8797");
    expect(groups.thisDevice?.address).toBe("studio.local:8797");
    expect(groups.thisDevice?.name).toBe("studio.local");
  });

  it("works without tailscale", () => {
    const groups = buildHostGroups({
      pageHost: "localhost",
      snapshot: { available: false, error: "no tailscale", peers: [] },
      recents: [recent("http://10.0.4.12:9000", 1)],
    });
    expect(groups.online).toEqual([]);
    expect(groups.offline).toEqual([]);
    expect(groups.total).toBe(2);
  });

  it("searches name, DNS name, address and OS, and expands nothing itself", () => {
    const peers = [
      peer("gpu-box", "100.91.2.17", true, { os: "linux" }),
      peer("macbook", "100.5.5.5", false, { os: "macOS", dns_name: "macbook.example.ts.net" }),
      peer("ipad", "100.6.6.6", true, { os: "iOS" }),
    ];
    const run = (query: string) =>
      buildHostGroups({ pageHost: "h", snapshot: snapshot(peers), recents: [], query });
    expect(names(run("gpu").online)).toEqual(["gpu-box"]);
    expect(names(run("100.5").offline)).toEqual(["macbook"]);
    expect(names(run("macos").offline)).toEqual(["macbook"]);
    expect(names(run("example.ts.net").offline)).toEqual(["macbook"]);
    expect(names(run("ios ipad").online)).toEqual(["ipad"]);
    expect(run("zzz").online).toEqual([]);
    expect(run("zzz").thisDevice).toBeNull();
    expect(run("zzz").total).toBe(4);
  });
});

describe("buildSshRows", () => {
  const targets = [
    { id: "a", name: "gpu-ssh", auth: "password" as const, connected: false, supported_backends: ["claude_code", "codex"] },
    { id: "b", name: "key-ssh", auth: "key" as const, supported_backends: ["codex"] },
  ];

  it("reads password targets as online only once connected", () => {
    const rows = buildSshRows(targets);
    expect(rows[0]).toMatchObject({ key: "ssh:a", online: "offline", sshAuth: "password", sshConnected: false });
    expect(rows[1]).toMatchObject({ key: "ssh:b", online: "online", sshConnected: true });
    expect(buildSshRows([{ ...targets[0], connected: true }])[0].online).toBe("online");
  });

  it("filters by query", () => {
    expect(names(buildSshRows(targets, "key"))).toEqual(["key-ssh"]);
  });
});

describe("formatLastUsed", () => {
  const now = new Date(2026, 10, 25, 12).getTime();
  const at = (day: number, hour = 9) => new Date(2026, 10, day, hour).getTime();

  it("names today and yesterday", () => {
    expect(formatLastUsed(at(25, 8), now)).toBe("Today");
    expect(formatLastUsed(at(24, 23), now)).toBe("Yesterday");
  });

  it("uses a weekday within the week and a date after", () => {
    expect(formatLastUsed(at(23), now)).toBe("Mon");
    expect(formatLastUsed(at(3), now)).toBe("Nov 3");
  });
});

describe("describeHost", () => {
  it("prefers the SSH target, then the sign-in name", () => {
    const recents = [recent("http://100.83.12.4:8787", 1, "studio")];
    expect(describeHost("http://100.83.12.4:8787", { targetName: "gpu-ssh", recents })).toBe("gpu-ssh");
    expect(describeHost("http://100.83.12.4:8787", { recents })).toBe("studio");
  });

  it("falls back to the hostname", () => {
    expect(describeHost("http://localhost:8787")).toBe("Local");
    expect(describeHost("http://127.0.0.1:8787")).toBe("Local");
    expect(describeHost("http://100.83.12.4:8787")).toBe("100.83.12.4");
    expect(describeHost("http://studio.tail1234.ts.net:8787")).toBe("studio");
  });
});
