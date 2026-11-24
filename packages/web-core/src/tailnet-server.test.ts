import { describe, expect, it } from "vitest";

import { parseTailnetStatus } from "./tailnet-server";

const node = (host: string, ip: string, online: boolean, extra: Record<string, unknown> = {}) => ({
  HostName: host,
  DNSName: `${host}.tail1234.ts.net.`,
  TailscaleIPs: ["fd7a:115c::1", ip],
  Online: online,
  OS: "linux",
  ...extra,
});

describe("parseTailnetStatus", () => {
  it("lists self first, then online peers, then offline, by name", () => {
    const raw = JSON.stringify({
      BackendState: "Running",
      Self: node("studio", "100.1.1.1", false),
      Peer: {
        a: node("zeta", "100.1.1.2", true),
        b: node("alpha", "100.1.1.3", false),
        c: node("beta", "100.1.1.4", true),
      },
    });
    const snapshot = parseTailnetStatus(raw);
    expect(snapshot.available).toBe(true);
    expect(snapshot.peers.map((p) => [p.name, p.is_self, p.online])).toEqual([
      ["studio", true, true],
      ["beta", false, true],
      ["zeta", false, true],
      ["alpha", false, false],
    ]);
  });

  it("keeps only IPv4 addresses and strips the DNS trailing dot", () => {
    const snapshot = parseTailnetStatus(
      JSON.stringify({
        Self: node("studio", "100.1.1.1", true),
        Peer: { v6: { HostName: "v6only", TailscaleIPs: ["fd7a:115c::9"], Online: true } },
      }),
    );
    expect(snapshot.peers).toHaveLength(1);
    expect(snapshot.peers[0]).toMatchObject({
      ip: "100.1.1.1",
      dns_name: "studio.tail1234.ts.net",
      os: "linux",
    });
  });

  it("reports a stopped backend as unavailable", () => {
    expect(parseTailnetStatus(JSON.stringify({ BackendState: "Stopped" }))).toEqual({
      available: false,
      error: "tailscale state: Stopped",
      peers: [],
    });
  });

  it("reports unparsable output as unavailable", () => {
    const snapshot = parseTailnetStatus("not json");
    expect(snapshot.available).toBe(false);
    expect(snapshot.error).toContain("could not parse tailscale output");
  });
});
