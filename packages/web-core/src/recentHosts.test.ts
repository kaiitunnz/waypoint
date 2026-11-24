import { describe, expect, it } from "vitest";

import { parseRecentHosts, pushRecentHost, type RecentHost } from "./recentHosts";

const host = (url: string, lastUsedAt: number, name = url): RecentHost => ({
  url,
  name,
  lastUsedAt,
});

describe("pushRecentHost", () => {
  it("puts the newest sign-in first", () => {
    const list = [host("http://a:8787", 1)];
    expect(pushRecentHost(list, host("http://b:8787", 2)).map((h) => h.url)).toEqual([
      "http://b:8787",
      "http://a:8787",
    ]);
  });

  it("replaces an earlier entry for the same host and port", () => {
    const list = [host("http://a:8787", 1, "old"), host("http://b:8787", 0)];
    const next = pushRecentHost(list, host("http://a:8787", 5, "new"));
    expect(next).toEqual([host("http://a:8787", 5, "new"), host("http://b:8787", 0)]);
  });

  it("keeps the same machine on two ports as two entries", () => {
    const list = [host("http://a:8787", 1)];
    expect(pushRecentHost(list, host("http://a:9000", 2))).toHaveLength(2);
  });

  it("caps the list at five", () => {
    let list: RecentHost[] = [];
    for (let i = 0; i < 8; i += 1) {
      list = pushRecentHost(list, host(`http://h${i}:8787`, i));
    }
    expect(list.map((h) => h.url)).toEqual([
      "http://h7:8787",
      "http://h6:8787",
      "http://h5:8787",
      "http://h4:8787",
      "http://h3:8787",
    ]);
  });
});

describe("parseRecentHosts", () => {
  it("drops malformed entries and sorts newest first", () => {
    const parsed = parseRecentHosts([
      host("http://old:8787", 1),
      null,
      { name: "no url", lastUsedAt: 9 },
      { url: "", lastUsedAt: 9 },
      host("http://new:8787", 3),
      "junk",
    ]);
    expect(parsed.map((h) => h.url)).toEqual(["http://new:8787", "http://old:8787"]);
  });

  it("defaults a missing name to the url and a missing time to 0", () => {
    expect(parseRecentHosts([{ url: "http://a:8787" }])).toEqual([
      { url: "http://a:8787", name: "http://a:8787", lastUsedAt: 0 },
    ]);
  });

  it("de-duplicates by host and port, keeping the newest", () => {
    const parsed = parseRecentHosts([host("http://a:8787", 1), host("http://a:8787/", 2)]);
    expect(parsed).toHaveLength(1);
    expect(parsed[0].lastUsedAt).toBe(2);
  });

  it("returns an empty list for non-arrays", () => {
    expect(parseRecentHosts({})).toEqual([]);
    expect(parseRecentHosts(null)).toEqual([]);
  });
});
