import { afterEach, describe, expect, it, vi } from "vitest";

import {
  canonicalBackendKey,
  DEFAULT_BACKEND_PORT,
  deploymentBackendPort,
  tailnetSummary,
  type TailnetPeer,
} from "./tailnet";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("deploymentBackendPort", () => {
  it("is the default when the variable is unset or empty", () => {
    vi.stubEnv("NEXT_PUBLIC_BACKEND_PORT", undefined);
    expect(deploymentBackendPort()).toBe(DEFAULT_BACKEND_PORT);
    vi.stubEnv("NEXT_PUBLIC_BACKEND_PORT", "");
    expect(deploymentBackendPort()).toBe(DEFAULT_BACKEND_PORT);
  });

  it("reads a valid port", () => {
    vi.stubEnv("NEXT_PUBLIC_BACKEND_PORT", "8797");
    expect(deploymentBackendPort()).toBe(8797);
  });

  it("falls back to the default for a value that is not a port", () => {
    for (const value of ["0", "70000", "-1", "abc"]) {
      vi.stubEnv("NEXT_PUBLIC_BACKEND_PORT", value);
      expect(deploymentBackendPort()).toBe(DEFAULT_BACKEND_PORT);
    }
  });
});

describe("canonicalBackendKey", () => {
  it("is the same for the same host and port however the URL is written", () => {
    expect(canonicalBackendKey("http://Studio:8787")).toBe(canonicalBackendKey("http://studio:8787/"));
    expect(canonicalBackendKey("http://studio:8787")).not.toBe(canonicalBackendKey("http://studio:9000"));
    expect(canonicalBackendKey("https://studio")).toBe("studio:443");
  });
});

describe("tailnetSummary", () => {
  const peer = (name: string, is_self = false): TailnetPeer => ({
    name,
    dns_name: null,
    ip: "100.64.0.1",
    online: true,
    os: null,
    is_self,
  });

  it("is empty before a snapshot loads", () => {
    expect(tailnetSummary(null)).toBe("");
  });

  it("says when Tailscale is not detected", () => {
    expect(tailnetSummary({ available: false, error: "no tailscale", peers: [] })).toBe("Tailscale not detected");
  });

  it("counts peers other than this device", () => {
    const peers = [peer("self", true), peer("a"), peer("b")];
    expect(tailnetSummary({ available: true, error: null, peers })).toBe("Tailscale, 2 peers");
    expect(tailnetSummary({ available: true, error: null, peers: peers.slice(0, 2) })).toBe("Tailscale, 1 peer");
  });
});
