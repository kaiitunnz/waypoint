import { describe, expect, it } from "vitest";

import {
  buildBackendUrl,
  formatAddress,
  isCompleteHost,
  parseAddress,
  parsePort,
  portAfterHostEdit,
  resolveTypedAddress,
  splitBackendUrl,
} from "./backendAddress";

describe("parseAddress", () => {
  it("reads a bare host", () => {
    expect(parseAddress("10.0.4.12")).toEqual({ protocol: null, hostname: "10.0.4.12", port: null });
  });

  it("reads host:port", () => {
    expect(parseAddress("Studio.local:9000")).toEqual({
      protocol: null,
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
    expect(parseAddress("[::1]:8787")).toEqual({ protocol: null, hostname: "[::1]", port: 8787 });
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

describe("resolveTypedAddress", () => {
  it("takes the scheme in the text, else the page's", () => {
    expect(resolveTypedAddress("100.64.0.5", "8787", "https")).toEqual({
      protocol: "https",
      hostname: "100.64.0.5",
      portText: "8787",
    });
    expect(resolveTypedAddress("http://10.0.0.5", "8787", "https")?.protocol).toBe("http");
    expect(resolveTypedAddress("https://box.example.com", "8787", "http")?.protocol).toBe("https");
  });

  it("uses the port field's text whatever the host text names", () => {
    expect(resolveTypedAddress("https://h", "8443", "http")?.portText).toBe("8443");
    expect(resolveTypedAddress("h:9000", "9100", "http")?.portText).toBe("9100");
  });

  it("is null for text that is not an address", () => {
    expect(resolveTypedAddress("", "8787", "http")).toBeNull();
    expect(resolveTypedAddress("a b", "8787", "http")).toBeNull();
  });
});

describe("portAfterHostEdit", () => {
  it("follows a port the text newly names", () => {
    expect(portAfterHostEdit("h:900", "h:9000", "900")).toBe("9000");
    expect(portAfterHostEdit("", "https://h", "8787")).toBe("443");
    expect(portAfterHostEdit("h", "h:9000", "8787")).toBe("9000");
  });

  it("keeps what was entered in the port field while the text's port is unchanged", () => {
    expect(portAfterHostEdit("https://h", "https://hx", "8443")).toBe("8443");
    expect(portAfterHostEdit("h:9000", "h:9000/", "9100")).toBe("9100");
  });

  it("keeps the port field when the text names no port", () => {
    expect(portAfterHostEdit("h", "ho", "9100")).toBe("9100");
    expect(portAfterHostEdit("h:9", "h:", "9")).toBe("9");
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
