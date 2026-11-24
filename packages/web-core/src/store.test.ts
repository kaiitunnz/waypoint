import { afterEach, describe, expect, it, vi } from "vitest";

import { readHost, readSavedHost } from "./store";

function stubBrowser(pageHostname: string, saved?: string) {
  vi.stubGlobal("window", {
    location: { protocol: "http:", hostname: pageHostname },
    localStorage: { getItem: (key: string) => (key === "waypoint.host" ? (saved ?? null) : null) },
  });
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("readSavedHost", () => {
  it("keeps a saved loopback host when the page is served from another machine", () => {
    stubBrowser("mac.tailnet", "http://127.0.0.1:9000");
    expect(readSavedHost()).toBe("http://127.0.0.1:9000");
  });

  it("returns any saved host as it is", () => {
    stubBrowser("mac.tailnet", "http://studio:8787");
    expect(readSavedHost()).toBe("http://studio:8787");
  });

  it("falls back to the page's own backend when nothing is saved", () => {
    vi.stubEnv("NEXT_PUBLIC_BACKEND_PORT", "8797");
    stubBrowser("mac.tailnet");
    expect(readSavedHost()).toBe("http://mac.tailnet:8797");
  });

  it("is empty outside a browser", () => {
    expect(readSavedHost()).toBe("");
  });
});

describe("readHost", () => {
  it("replaces a saved loopback host with the page's backend on a remote page", () => {
    stubBrowser("mac.tailnet", "http://localhost:9000");
    expect(readHost()).toBe("http://mac.tailnet:8787");
  });

  it("keeps a saved loopback host on a loopback page", () => {
    stubBrowser("localhost", "http://127.0.0.1:9000");
    expect(readHost()).toBe("http://127.0.0.1:9000");
  });
});
