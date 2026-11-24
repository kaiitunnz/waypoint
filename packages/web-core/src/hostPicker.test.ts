import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildBackendUrl,
  buildHostGroups,
  buildSshRows,
  describeHost,
  formatAddress,
  formatLastUsed,
  hostKey,
  isCompleteHost,
  pageDevice,
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

  it("reads a URL with a scheme and no port as the scheme's default port", () => {
    expect(parseAddress("https://waypoint.example.com")).toEqual({
      protocol: "https",
      hostname: "waypoint.example.com",
      port: 443,
    });
    expect(parseAddress("http://10.0.4.12/")).toEqual({ protocol: "http", hostname: "10.0.4.12", port: 80 });
  });

  it("rejects hosts a URL parser would read differently", () => {
    expect(parseAddress("evil.example\\@good.example")).toBeNull();
    expect(parseAddress("user@good.example")).toBeNull();
    expect(parseAddress("http://a%2eb")).toBeNull();
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
    expect(formatAddress("http://box")).toBe("box:80");
  });

  it("reads an address with no port as the scheme's default port", () => {
    expect(portForHost(["alpha.ts.net"], [recent("https://alpha.ts.net", 1)], 8787)).toBe(443);
    expect(portForHost(["alpha.ts.net"], [recent("http://alpha.ts.net", 1)], 8787)).toBe(80);
  });

  it("takes a host's port from its most recent sign-in", () => {
    const recents = [recent("http://100.1.1.1:9000", 2), recent("http://100.1.1.1:8800", 1)];
    expect(portForHost(["100.1.1.1"], recents, 8787)).toBe(9000);
    expect(portForHost(["100.2.2.2"], recents, 8787)).toBe(8787);
    expect(portForHost(["100.2.2.2"], recents, 8123)).toBe(8123);
    expect(portForHost([null, "100.1.1.1"], recents, 8787)).toBe(9000);
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

  it("folds a recent typed with the DNS name into the peer it names", () => {
    const groups = buildHostGroups({
      pageHost: "h",
      snapshot: snapshot([peer("lab", "100.7.7.7")]),
      recents: [recent("http://lab.tail1234.ts.net:8787", 1, "lab")],
    });
    expect(groups.recent).toHaveLength(1);
    expect(groups.recent[0].online).toBe("online");
    expect(groups.online).toEqual([]);
  });

  it("treats a recent that names the host serving the page as This device", () => {
    const groups = buildHostGroups({
      pageHost: "localhost",
      devicePort: 8787,
      snapshot: snapshot([peer("studio", "100.83.12.4", true, { is_self: true })]),
      recents: [recent("http://100.83.12.4:8787", 1, "studio"), recent("http://100.83.12.4:9000", 0, "studio-9000")],
    });
    expect(groups.thisDevice).toMatchObject({ name: "studio", hostname: "localhost" });
    expect(names(groups.recent)).toEqual(["studio-9000"]);
  });

  describe("with a signed-in backend's snapshot", () => {
    // Page served from laptop L, signed in to backend S: the snapshot is S's view, so `is_self` is S.
    const backendView = () =>
      snapshot([
        peer("server-s", "100.64.0.9", true, { is_self: true }),
        peer("laptop-l", "100.64.0.2", true),
        peer("box", "100.64.0.5", true),
      ]);

    it("names This device after the peer that serves the page and keeps the backend listed", () => {
      const groups = buildHostGroups({
        pageHost: "100.64.0.2",
        devicePort: 8787,
        snapshot: backendView(),
        selfIsPageHost: false,
        recents: [recent("http://100.64.0.9:8787", 1, "server-s")],
      });
      expect(groups.thisDevice).toMatchObject({ name: "laptop-l", url: "http://100.64.0.2:8787" });
      expect(names(groups.recent)).toEqual(["server-s"]);
      expect(names(groups.online)).toEqual(["box"]);
    });

    it("lists the backend under Online when it was never signed in to from here", () => {
      const groups = buildHostGroups({
        pageHost: "100.64.0.2",
        snapshot: backendView(),
        selfIsPageHost: false,
        recents: [],
      });
      expect(names(groups.online)).toEqual(["box", "server-s"]);
    });

    it("falls back to the page host when the backend's tailnet does not list it", () => {
      const groups = buildHostGroups({
        pageHost: "localhost",
        snapshot: backendView(),
        selfIsPageHost: false,
        recents: [],
      });
      expect(groups.thisDevice).toMatchObject({ name: "localhost" });
      expect(names(groups.online)).toEqual(["box", "laptop-l", "server-s"]);
    });
  });

  it("lists a peer once when its recent is an https address on the default port", () => {
    const groups = buildHostGroups({
      pageHost: "h",
      snapshot: snapshot([peer("alpha", "100.1.1.2")]),
      recents: [recent("https://alpha.tail1234.ts.net", 1, "alpha")],
    });
    expect(names(groups.recent)).toEqual(["alpha"]);
    expect(groups.recent[0].address).toBe("alpha.tail1234.ts.net");
    expect(groups.online).toEqual([]);
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

describe("pageDevice", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("reads the page host and scheme with the deployment port", () => {
    vi.stubEnv("NEXT_PUBLIC_BACKEND_PORT", "8797");
    vi.stubGlobal("window", { location: { hostname: "studio.tail1234.ts.net", protocol: "https:" } });
    expect(pageDevice()).toEqual({ pageHost: "studio.tail1234.ts.net", protocol: "https", devicePort: 8797 });
  });

  it("reads as localhost over http without a page", () => {
    vi.stubEnv("NEXT_PUBLIC_BACKEND_PORT", undefined);
    expect(pageDevice()).toEqual({ pageHost: "localhost", protocol: "http", devicePort: 8787 });
  });
});

describe("isCompleteHost", () => {
  it("waits for a whole IPv4 address", () => {
    expect(isCompleteHost("10.0")).toBe(false);
    expect(isCompleteHost("10.0.4.")).toBe(false);
    expect(isCompleteHost("10.0.4.12")).toBe(true);
    expect(isCompleteHost("10.0.4.12.5")).toBe(false);
  });

  it("accepts names and rejects empty or dangling ones", () => {
    expect(isCompleteHost("studio")).toBe(true);
    expect(isCompleteHost("studio.tail1234.ts.net")).toBe(true);
    expect(isCompleteHost("studio.")).toBe(false);
    expect(isCompleteHost("")).toBe(false);
  });
});

describe("hostKey", () => {
  it("is the same for the same host and port however the URL is written", () => {
    expect(hostKey("http://Studio:8787")).toBe(hostKey("http://studio:8787/"));
    expect(hostKey("http://studio:8787")).not.toBe(hostKey("http://studio:9000"));
    expect(hostKey("https://studio")).toBe("studio:443");
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
